import fastmcp
import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from gcp_mcp.server import build_server, http_app
from gcp_mcp.settings import Settings

TOOLS = (
    "gcp_services",
    "gcp_methods",
    "gcp_describe_method",
    "gcp_call",
    "unsafe_ssh_exposure",
    "find_project_owners",
    "is_bucket_public",
)
DELETED_TOOLS = (
    "describe_firewall_rule",
    "describe_gcp_disk",
    "describe_gcp_instance",
    "describe_gcs_bucket",
    "is_ubla_enabled_in_bucket",
    "list_all_instances_in_project",
    "list_firewall_rules",
    "list_firewall_rules_per_vpc",
    "list_gcp_disks_per_zone",
    "list_gcp_instances_per_zone",
    "list_gcs_buckets",
    "list_project_iam",
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def tool_names(server):
    async with Client(server) as client:
        return [tool.name for tool in await client.list_tools()]


@pytest.mark.anyio
async def test_serves_the_seven_tools_in_order_as_read_only():
    async with Client(build_server(Settings())) as client:
        tools = await client.list_tools()

    assert tuple(tool.name for tool in tools) == TOOLS
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is True


@pytest.mark.anyio
async def test_no_legacy_tool_is_served():
    names = await tool_names(build_server(Settings()))

    assert set(names).isdisjoint(DELETED_TOOLS)


@pytest.mark.anyio
async def test_tool_order_is_the_same_across_builds():
    first = await tool_names(build_server(Settings()))
    second = await tool_names(build_server(Settings()))

    assert first == second


@pytest.mark.anyio
async def test_healthz_returns_ok():
    app = http_app(build_server(Settings()))
    transport = httpx2.ASGITransport(app=app)

    async with app.lifespan(app):
        async with httpx2.AsyncClient(transport=transport, base_url="http://gcp-mcp.test") as http:
            response = await http.get("/healthz")

    assert (response.status_code, response.text) == (200, "OK")


@pytest.mark.anyio
async def test_streamable_http_lists_the_same_tools():
    app = http_app(build_server(Settings()))
    transport = httpx2.ASGITransport(app=app)

    def asgi_client(**kwargs):
        return httpx2.AsyncClient(transport=transport, **kwargs)

    mcp = StreamableHttpTransport("http://gcp-mcp.test/mcp", httpx_client_factory=asgi_client)
    async with app.lifespan(app):
        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}
            protocol = client.protocol_version

    assert names == set(TOOLS)
    assert protocol == "2026-07-28"


def test_building_the_server_turns_off_the_fastmcp_update_check(monkeypatch):
    monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")

    build_server(Settings())

    assert fastmcp.settings.check_for_updates == "off"
