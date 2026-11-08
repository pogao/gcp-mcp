import pytest
from pydantic import ValidationError

from gcp_mcp.settings import HttpConfig, Settings, StdioConfig


def test_default_transport_is_stdio():
    assert Settings().config() == StdioConfig()


def test_http_config_carries_host_port_and_base_url():
    config = Settings(transport="http", port=9000).config()

    assert config == HttpConfig(host="127.0.0.1", port=9000, base_url=None)


def test_environment_selects_the_transport(monkeypatch):
    monkeypatch.setenv("GCP_MCP_TRANSPORT", "http")

    assert isinstance(Settings().config(), HttpConfig)


def test_unknown_transport_is_refused():
    with pytest.raises(ValidationError):
        Settings(transport="carrier-pigeon")
