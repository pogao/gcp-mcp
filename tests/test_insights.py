import json
from typing import Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from gcp_mcp.discovery import Registry
from gcp_mcp.insights import owners, public_bindings, ssh_open_to_world
from gcp_mcp.rest import Gcp, Limits, StaticCredentials
from gcp_mcp.server import build_server
from gcp_mcp.settings import Settings


def rule(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "allow-ssh",
        "network": "https://www.googleapis.com/compute/v1/projects/example-project/global/networks/default",
        "direction": "INGRESS",
        "disabled": False,
        "sourceRanges": ["0.0.0.0/0"],
        "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}],
    }
    return {**base, **changes}


@pytest.mark.parametrize(
    ("firewall", "open_to_world"),
    [
        (rule(), True),
        (rule(disabled=True), False),
        (rule(direction="EGRESS"), False),
        (rule(allowed=[{"IPProtocol": "tcp", "ports": ["20-30"]}]), True),
        (rule(allowed=[{"IPProtocol": "all"}]), True),
        (rule(allowed=[{"IPProtocol": "tcp"}]), True),
        (rule(allowed=[{"IPProtocol": "6", "ports": ["22"]}]), True),
        (rule(sourceRanges=["::/0"]), True),
        (rule(sourceRanges=["10.0.0.0/8", "192.168.0.0/16"]), False),
        (rule(allowed=[{"IPProtocol": "udp", "ports": ["22"]}]), False),
        (rule(allowed=[{"IPProtocol": "tcp", "ports": ["80", "443"]}]), False),
        (rule(allowed=[{"IPProtocol": "tcp", "ports": ["23-30"]}]), False),
        (rule(allowed=[], denied=[{"IPProtocol": "tcp", "ports": ["22"]}]), False),
    ],
    ids=[
        "tcp 22 from anywhere",
        "disabled",
        "egress",
        "range covering 22",
        "all protocols and ports",
        "tcp on every port",
        "protocol number 6",
        "ipv6 anywhere",
        "private ranges only",
        "udp 22",
        "web ports only",
        "range missing 22",
        "deny rule",
    ],
)
def test_ssh_open_to_world(firewall: dict[str, Any], open_to_world: bool) -> None:
    assert ssh_open_to_world(firewall) is open_to_world


def test_owners_flattens_the_members_of_every_owner_binding() -> None:
    policy = {
        "bindings": [
            {"role": "roles/viewer", "members": ["user:viewer@example.com"]},
            {"role": "roles/owner", "members": ["user:a@example.com", "group:ops@example.com"]},
            {
                "role": "roles/owner",
                "members": ["serviceAccount:sa@example-project.iam.gserviceaccount.com"],
                "condition": {"expression": "request.time < timestamp('2027-01-01T00:00:00Z')"},
            },
        ]
    }
    assert owners(policy) == [
        "user:a@example.com",
        "group:ops@example.com",
        "serviceAccount:sa@example-project.iam.gserviceaccount.com",
    ]


def test_owners_of_a_policy_without_owner_bindings_is_empty() -> None:
    assert owners({"bindings": [{"role": "roles/viewer", "members": ["user:v@example.com"]}]}) == []
    assert owners({"etag": "BwX", "version": 3}) == []


def test_public_bindings_name_the_role_and_the_public_member() -> None:
    policy = {
        "bindings": [
            {"role": "roles/storage.admin", "members": ["user:admin@example.com"]},
            {
                "role": "roles/storage.objectViewer",
                "members": ["allUsers", "user:reader@example.com"],
            },
            {"role": "roles/storage.legacyBucketReader", "members": ["allAuthenticatedUsers"]},
        ]
    }
    assert public_bindings(policy) == [
        {"role": "roles/storage.objectViewer", "member": "allUsers"},
        {"role": "roles/storage.legacyBucketReader", "member": "allAuthenticatedUsers"},
    ]


def test_public_bindings_of_a_private_policy_is_empty() -> None:
    policy = {"bindings": [{"role": "roles/storage.admin", "members": ["user:admin@example.com"]}]}
    assert public_bindings(policy) == []


REGISTRY = Registry()
LIMITS = Limits(default_page_size=100, max_response_bytes=100_000, request_timeout_s=30.0)
INSIGHT_TOOLS = ("unsafe_ssh_exposure", "find_project_owners", "is_bucket_public")
FIREWALLS = "https://compute.googleapis.com/compute/v1/projects/example-project/global/firewalls"
PROJECT_POLICY = (
    "https://cloudresourcemanager.googleapis.com/v3/projects/example-project:getIamPolicy"
)
BUCKET_POLICY = "https://storage.googleapis.com/storage/v1/b/example-bucket/iam"
FIELDS = "items(name,network,direction,disabled,sourceRanges,allowed),nextPageToken"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Google:
    def __init__(self, *responses: httpx2.Response, limits: Limits = LIMITS) -> None:
        self.responses = list(responses)
        self.limits = limits
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def gcp(self) -> Gcp:
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(self))
        return Gcp(REGISTRY, self.limits, StaticCredentials("example-token"), http)


async def call(name: str, arguments: dict[str, Any], google: Google) -> Any:
    async with Client(build_server(Settings(), gcp=google.gcp())) as client:
        result = await client.call_tool(name, arguments)
    return result.structured_content


@pytest.mark.anyio
async def test_the_insight_tools_follow_the_generic_tools_and_are_read_only() -> None:
    async with Client(build_server(Settings(), gcp=Google().gcp())) as client:
        tools = await client.list_tools()
    insights = [tool for tool in tools if tool.name in INSIGHT_TOOLS]
    assert tuple(tool.name for tool in insights) == INSIGHT_TOOLS
    assert tuple(tool.name for tool in tools[-3:]) == INSIGHT_TOOLS
    for tool in insights:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is True
        assert len(tool.description.split()) < 100


@pytest.mark.anyio
async def test_the_instructions_name_the_security_checks() -> None:
    async with Client(build_server(Settings(), gcp=Google().gcp())) as client:
        instructions = client.instructions
    for name in INSIGHT_TOOLS:
        assert name in instructions


@pytest.mark.anyio
async def test_ssh_exposure_walks_every_page_and_reports_the_open_rules() -> None:
    first_page = {
        "items": [rule(name="allow-web", allowed=[{"IPProtocol": "tcp", "ports": ["80"]}])],
        "nextPageToken": "page-2",
    }
    second_page = {
        "items": [
            rule(name="allow-internal", sourceRanges=["10.0.0.0/8"]),
            rule(sourceRanges=["203.0.113.0/24", "0.0.0.0/0", "::/0"]),
        ]
    }
    google = Google(httpx2.Response(200, json=first_page), httpx2.Response(200, json=second_page))

    answer = await call("unsafe_ssh_exposure", {"project": "example-project"}, google)

    first, second = google.requests
    assert (first.method, first.url.copy_with(query=None)) == ("GET", httpx2.URL(FIREWALLS))
    assert first.url.params["fields"] == FIELDS
    assert first.url.params["maxResults"] == "100"
    assert "pageToken" not in first.url.params
    assert second.url.params["pageToken"] == "page-2"
    assert second.url.params["fields"] == FIELDS
    assert answer == {
        "project": "example-project",
        "rules": [
            {
                "name": "allow-ssh",
                "network": "default",
                "source_ranges": ["0.0.0.0/0", "::/0"],
                "ports": ["22"],
            }
        ],
        "count": 1,
    }


@pytest.mark.anyio
async def test_ssh_exposure_reports_every_port_when_a_rule_has_no_port_list() -> None:
    page = {"items": [rule(allowed=[{"IPProtocol": "all"}])]}
    google = Google(httpx2.Response(200, json=page))

    answer = await call("unsafe_ssh_exposure", {"project": "example-project"}, google)

    assert answer["rules"][0]["ports"] == ["all"]
    assert answer["count"] == 1


@pytest.mark.anyio
async def test_ssh_exposure_refuses_a_verdict_on_a_truncated_page() -> None:
    page = {"items": [rule(name=f"rule-{n}") for n in range(50)]}
    limits = Limits(default_page_size=100, max_response_bytes=1_000, request_timeout_s=30.0)
    google = Google(httpx2.Response(200, json=page), limits=limits)

    with pytest.raises(ToolError) as caught:
        await call("unsafe_ssh_exposure", {"project": "example-project"}, google)

    assert "max-response-bytes" in str(caught.value)


@pytest.mark.anyio
async def test_project_owners_posts_a_version_3_policy_request() -> None:
    policy = {
        "version": 3,
        "bindings": [
            {"role": "roles/owner", "members": ["user:a@example.com", "group:ops@example.com"]},
            {"role": "roles/editor", "members": ["user:b@example.com"]},
        ],
    }
    google = Google(httpx2.Response(200, json=policy))

    answer = await call("find_project_owners", {"project": "example-project"}, google)

    [request] = google.requests
    assert (request.method, request.url.copy_with(query=None)) == (
        "POST",
        httpx2.URL(PROJECT_POLICY),
    )
    assert json.loads(request.content) == {"options": {"requestedPolicyVersion": 3}}
    assert request.headers["content-type"] == "application/json"
    assert answer == {
        "project": "example-project",
        "owners": ["user:a@example.com", "group:ops@example.com"],
        "count": 2,
    }


@pytest.mark.anyio
async def test_bucket_public_gets_the_bucket_policy_at_version_3() -> None:
    policy = {
        "bindings": [
            {"role": "roles/storage.objectViewer", "members": ["allUsers"]},
            {"role": "roles/storage.admin", "members": ["user:admin@example.com"]},
        ]
    }
    google = Google(httpx2.Response(200, json=policy))

    answer = await call("is_bucket_public", {"bucket": "example-bucket"}, google)

    [request] = google.requests
    assert (request.method, request.url.copy_with(query=None)) == ("GET", httpx2.URL(BUCKET_POLICY))
    assert request.url.params["optionsRequestedPolicyVersion"] == "3"
    assert answer == {
        "bucket": "example-bucket",
        "public": True,
        "bindings": [{"role": "roles/storage.objectViewer", "member": "allUsers"}],
    }


@pytest.mark.anyio
async def test_a_private_bucket_is_not_public() -> None:
    policy = {"bindings": [{"role": "roles/storage.admin", "members": ["user:admin@example.com"]}]}
    google = Google(httpx2.Response(200, json=policy))

    answer = await call("is_bucket_public", {"bucket": "example-bucket"}, google)

    assert answer == {"bucket": "example-bucket", "public": False, "bindings": []}


@pytest.mark.anyio
async def test_a_google_error_becomes_a_tool_error_with_the_next_move() -> None:
    envelope = {"error": {"code": 403, "message": "Forbidden", "status": "PERMISSION_DENIED"}}
    google = Google(httpx2.Response(403, json=envelope))

    with pytest.raises(ToolError) as caught:
        await call("find_project_owners", {"project": "example-project"}, google)

    assert "PERMISSION_DENIED" in str(caught.value)
    assert "identity" in str(caught.value)
