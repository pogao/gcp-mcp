from typing import Any

import google.auth
import google.auth.exceptions
import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from gcp_mcp.discovery import Registry
from gcp_mcp.rest import CredentialSource, Gcp, Limits, StaticCredentials
from gcp_mcp.server import build_server
from gcp_mcp.settings import Settings

GENERIC_TOOLS = ("gcp_services", "gcp_methods", "gcp_describe_method", "gcp_call")
REGISTRY = Registry()
LIMITS = Limits(default_page_size=100, max_response_bytes=100_000, request_timeout_s=30.0)
VM = {"project": "example-project", "zone": "us-central1-a", "instance": "vm-1"}
AGGREGATED = (
    "https://compute.googleapis.com/compute/v1/projects/example-project/aggregated/instances"
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Google:
    def __init__(
        self,
        response: httpx2.Response | Exception | None = None,
        *,
        limits: Limits = LIMITS,
        credentials: CredentialSource | None = None,
    ) -> None:
        self.response = response or httpx2.Response(200, json={})
        self.limits = limits
        self.credentials = credentials or StaticCredentials("example-token")
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def gcp(self) -> Gcp:
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self))
        return Gcp(REGISTRY, self.limits, self.credentials, http)


class BrokenCredentials:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def headers(self) -> dict[str, str]:
        raise self.error


async def call(name: str, arguments: dict[str, Any], google: Google | None = None) -> Any:
    gcp = (google or Google()).gcp()
    async with Client(build_server(Settings(), gcp=gcp)) as client:
        result = await client.call_tool(name, arguments)
    return result.structured_content


async def refusal(name: str, arguments: dict[str, Any], google: Google | None = None) -> str:
    with pytest.raises(ToolError) as caught:
        await call(name, arguments, google)
    return str(caught.value)


@pytest.mark.anyio
async def test_the_generic_tools_come_first_and_every_tool_is_read_only() -> None:
    async with Client(build_server(Settings(), gcp=Google().gcp())) as client:
        tools = await client.list_tools()
    assert tuple(tool.name for tool in tools[:4]) == GENERIC_TOOLS
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is True


@pytest.mark.anyio
async def test_the_instructions_name_the_four_step_workflow() -> None:
    async with Client(build_server(Settings(), gcp=Google().gcp())) as client:
        instructions = client.initialize_result.instructions
    positions = [instructions.index(name) for name in GENERIC_TOOLS]
    assert positions == sorted(positions)
    for word in ("read-only", "fields", "pageToken", "redacted"):
        assert word in instructions


@pytest.mark.anyio
async def test_a_server_built_from_settings_reads_credentials_only_on_the_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookups = []

    def default(scopes: list[str]) -> None:
        lookups.append(scopes)
        raise google.auth.exceptions.DefaultCredentialsError("no credentials")

    monkeypatch.setattr(google.auth, "default", default)
    async with Client(build_server(Settings())) as client:
        services = await client.call_tool("gcp_services", {"query": "compute"})
        assert lookups == []
        with pytest.raises(ToolError) as caught:
            await client.call_tool(
                "gcp_call", {"service": "compute", "method": "instances.get", "args": VM}
            )
    assert "compute" in [service["name"] for service in services.structured_content["services"]]
    assert "gcloud auth application-default login" in str(caught.value)
    assert len(lookups) == 1


@pytest.mark.anyio
async def test_services_lists_every_service_sorted_with_its_default_version() -> None:
    answer = await call("gcp_services", {})
    services = answer["services"]
    names = [service["name"] for service in services]
    assert answer["count"] == len(services) >= 190
    assert names == sorted(names)
    compute = next(service for service in services if service["name"] == "compute")
    assert set(compute) == {
        "name",
        "title",
        "default_version",
        "versions",
        "read_methods",
        "docs_url",
    }
    assert compute["default_version"] == "v1"
    assert "v1" in compute["versions"]
    assert compute["read_methods"] > 0


@pytest.mark.anyio
async def test_services_filters_by_name_or_title_ignoring_case() -> None:
    answer = await call("gcp_services", {"query": "KUBERNETES"})
    assert "container" in [service["name"] for service in answer["services"]]
    assert answer["count"] == len(answer["services"]) < 10


@pytest.mark.anyio
async def test_methods_groups_read_methods_by_resource_and_leaves_out_writes() -> None:
    answer = await call("gcp_methods", {"service": "compute", "query": "instances"})
    assert (answer["service"], answer["version"]) == ("compute", "v1")
    instances = answer["resources"]["instances"]
    assert {"aggregatedList", "get", "list"} <= set(instances)
    assert "delete" not in instances
    assert answer["read_methods"] > len(instances)
    assert answer["excluded_methods"] > 0
    names = [f"{prefix}.{name}" for prefix, group in answer["resources"].items() for name in group]
    assert all("instances" in name.lower() for name in names)


@pytest.mark.anyio
async def test_methods_puts_service_level_methods_under_the_empty_resource() -> None:
    answer = await call("gcp_methods", {"service": "cloudasset", "query": "searchall"})
    assert set(answer["resources"][""]) == {"searchAllIamPolicies", "searchAllResources"}


@pytest.mark.anyio
async def test_methods_honors_an_explicit_version() -> None:
    answer = await call("gcp_methods", {"service": "compute", "version": "beta"})
    assert answer["version"] == "beta"


@pytest.mark.anyio
async def test_describe_shows_the_required_path_params_of_a_get() -> None:
    answer = await call("gcp_describe_method", {"service": "compute", "method": "instances.get"})
    assert (answer["method"], answer["http_method"]) == ("instances.get", "GET")
    assert answer["path"] == "projects/{project}/zones/{zone}/instances/{instance}"
    required = {
        param["name"]
        for param in answer["params"]
        if param["required"] and param["location"] == "path"
    }
    assert required == {"project", "zone", "instance"}
    assert set(answer["params"][0]) == {
        "name",
        "location",
        "type",
        "required",
        "repeated",
        "enum",
        "description",
    }
    assert answer["body"] is None
    assert answer["pagination"] is None
    assert answer["response_schema"] == "Instance"
    assert "networkInterfaces" in answer["response_properties"]
    assert answer["docs_url"].startswith("https://")


@pytest.mark.anyio
async def test_describe_shows_the_body_and_body_pagination_of_a_post_read() -> None:
    answer = await call("gcp_describe_method", {"service": "logging", "method": "entries.list"})
    assert answer["http_method"] == "POST"
    assert answer["body"]["schema"] == "ListLogEntriesRequest"
    assert "resourceNames" in answer["body"]["properties"]
    assert answer["pagination"] == {
        "token_param": "pageToken",
        "size_param": "pageSize",
        "size_max": None,
        "in_body": True,
    }


@pytest.mark.anyio
async def test_describe_cuts_every_description_to_200_characters() -> None:
    answer = await call("gcp_describe_method", {"service": "logging", "method": "entries.list"})
    texts = [
        answer["description"],
        *answer["body"]["properties"].values(),
        *answer["response_properties"].values(),
    ]
    assert max(map(len, texts)) == 200


@pytest.mark.anyio
async def test_an_unknown_service_is_a_tool_error_with_suggestions() -> None:
    text = await refusal("gcp_methods", {"service": "comptue"})
    assert "'comptue'" in text
    assert "compute" in text


@pytest.mark.anyio
async def test_describing_a_refused_method_says_the_server_is_read_only() -> None:
    text = await refusal(
        "gcp_describe_method", {"service": "compute", "method": "instances.delete"}
    )
    assert "read-only" in text


@pytest.mark.anyio
async def test_call_sends_one_get_and_returns_the_page_with_a_paging_hint() -> None:
    answer = {
        "items": {"zones/us-central1-a": {"instances": [{"name": "vm-1"}]}},
        "nextPageToken": "next",
    }
    google = Google(httpx2.Response(200, json=answer))
    args = {"project": "example-project"}
    result = await call(
        "gcp_call",
        {"service": "compute", "method": "instances.aggregatedList", "args": args},
        google,
    )
    [request] = google.requests
    assert request.method == "GET"
    assert str(request.url) == f"{AGGREGATED}?alt=json&prettyPrint=false&maxResults=100"
    assert result["response"] == answer
    assert (result["service"], result["version"], result["method"]) == (
        "compute",
        "v1",
        "instances.aggregatedList",
    )
    assert result["next_page_token"] == "next"
    assert (result["truncated"], result["omitted_items"], result["redacted_fields"]) == (
        False,
        0,
        0,
    )
    assert "pageToken" in result["hint"]
    assert "args" in result["hint"]


@pytest.mark.anyio
async def test_a_complete_answer_has_no_hint() -> None:
    google = Google(httpx2.Response(200, json={"name": "vm-1"}))
    result = await call(
        "gcp_call", {"service": "compute", "method": "instances.get", "args": VM}, google
    )
    assert result["response"] == {"name": "vm-1"}
    assert "hint" not in result


@pytest.mark.anyio
async def test_a_post_read_sends_the_body_and_says_the_page_token_goes_in_the_body() -> None:
    answer = {"entries": [{"logName": "projects/example-project/logs/a"}], "nextPageToken": "next"}
    google = Google(httpx2.Response(200, json=answer))
    body = {"resourceNames": ["projects/example-project"]}
    result = await call(
        "gcp_call", {"service": "logging", "method": "entries.list", "body": body}, google
    )
    [request] = google.requests
    assert request.method == "POST"
    assert result["next_page_token"] == "next"
    assert "pageToken" in result["hint"]
    assert "body" in result["hint"]


@pytest.mark.anyio
async def test_a_truncated_answer_hints_at_fields_and_the_page_size() -> None:
    answer = {"items": [{"name": f"vm-{n}"} for n in range(100)], "nextPageToken": "next"}
    limits = Limits(default_page_size=100, max_response_bytes=500, request_timeout_s=30.0)
    google = Google(httpx2.Response(200, json=answer), limits=limits)
    args = {"project": "example-project"}
    result = await call(
        "gcp_call",
        {"service": "compute", "method": "instances.aggregatedList", "args": args},
        google,
    )
    assert result["truncated"] is True
    assert result["omitted_items"] > 0
    for word in ("fields", "maxResults", "pageToken"):
        assert word in result["hint"]


@pytest.mark.anyio
async def test_credential_shaped_fields_arrive_redacted_with_a_hint() -> None:
    google = Google(httpx2.Response(200, json={"name": "vm-1", "password": "example"}))
    result = await call(
        "gcp_call", {"service": "compute", "method": "instances.get", "args": VM}, google
    )
    assert result["response"] == {"name": "vm-1", "password": "[redacted]"}
    assert result["redacted_fields"] == 1
    assert "redacted" in result["hint"]


@pytest.mark.anyio
async def test_a_refused_method_is_a_read_only_tool_error_and_sends_nothing() -> None:
    google = Google()
    text = await refusal(
        "gcp_call", {"service": "compute", "method": "instances.delete", "args": VM}, google
    )
    assert "read-only" in text
    assert google.requests == []


@pytest.mark.anyio
async def test_a_missing_required_param_is_a_tool_error_with_the_accepted_names() -> None:
    google = Google()
    args = {"project": "example-project", "zone": "us-central1-a"}
    text = await refusal(
        "gcp_call", {"service": "compute", "method": "instances.get", "args": args}, google
    )
    assert "'instance'" in text
    assert "fields, instance, project, zone" in text
    assert google.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error", [httpx2.ConnectTimeout("timed out"), httpx2.ConnectError("refused")]
)
async def test_a_network_failure_is_a_tool_error_that_says_to_retry(error: Exception) -> None:
    text = await refusal(
        "gcp_call", {"service": "compute", "method": "instances.get", "args": VM}, Google(error)
    )
    assert "compute" in text
    assert "retry" in text.lower()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        google.auth.exceptions.DefaultCredentialsError("no credentials"),
        google.auth.exceptions.RefreshError("expired"),
    ],
)
async def test_missing_server_credentials_tell_the_operator_to_log_in(error: Exception) -> None:
    server = Google(credentials=BrokenCredentials(error))
    text = await refusal(
        "gcp_call", {"service": "compute", "method": "instances.get", "args": VM}, server
    )
    assert "gcloud auth application-default login" in text
    assert "operator" in text
    assert server.requests == []


@pytest.mark.anyio
async def test_a_403_is_a_tool_error_about_the_server_identity() -> None:
    envelope = {
        "error": {"code": 403, "message": "Permission denied", "status": "PERMISSION_DENIED"}
    }
    google = Google(httpx2.Response(403, json=envelope))
    text = await refusal(
        "gcp_call", {"service": "compute", "method": "instances.get", "args": VM}, google
    )
    assert "PERMISSION_DENIED" in text
    assert "identity" in text
