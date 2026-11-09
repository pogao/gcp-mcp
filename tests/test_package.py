import gcp_mcp
from gcp_mcp import discovery, rest, server, settings


def test_the_package_exports_its_public_names() -> None:
    from gcp_mcp import Gcp, Registry, Settings, build_server

    assert sorted(gcp_mcp.__all__) == ["Gcp", "Registry", "Settings", "build_server"]
    assert (Gcp, Registry, Settings, build_server) == (
        rest.Gcp,
        discovery.Registry,
        settings.Settings,
        server.build_server,
    )
