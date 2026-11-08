from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from gcp_mcp.discovery import Registry
from gcp_mcp.rest import Gcp, Limits, StaticCredentials
from gcp_mcp.tools import make_tools, read_only_tool

REGISTRY = Registry()
LIMITS = Limits(default_page_size=100, max_response_bytes=100_000, request_timeout_s=30.0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Google:
    def __init__(self, response: httpx2.Response | None = None) -> None:
        self.response = response or httpx2.Response(200, json={})
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.response

    def gcp(self, limits: Limits = LIMITS) -> Gcp:
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self))
        return Gcp(REGISTRY, limits, StaticCredentials("example-token"), http)


def serve(gcp: Gcp) -> FastMCP:
    server = FastMCP("test")
    for tool in make_tools(gcp):
        read_only_tool(server, tool)
    return server


async def call(name: str, arguments: dict[str, Any], google: Google | None = None) -> Any:
    gcp = (google or Google()).gcp()
    async with Client(serve(gcp)) as client:
        result = await client.call_tool(name, arguments)
    return result.structured_content


async def refusal(name: str, arguments: dict[str, Any], google: Google | None = None) -> str:
    with pytest.raises(ToolError) as caught:
        await call(name, arguments, google)
    return str(caught.value)


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
