import pytest
from joserfc.jwk import RSAKey
from pydantic import ValidationError
from pydantic_settings import CliApp

from gcp_mcp.settings import AuthConfig, HttpConfig, Settings, StdioConfig

AUTH = {
    "transport": "http",
    "require_auth": True,
    "base_url": "https://mcp.example.com",
    "auth_issuer": "https://issuer.example.com",
    "auth_jwks_uri": "https://issuer.example.com/jwks.json",
}

PUBLIC_KEY = RSAKey.generate_key(2048).as_pem(private=False).decode()

READ_SCOPED_AUTH = AuthConfig(
    issuer="https://issuer.example.com",
    jwks_uri="https://issuer.example.com/jwks.json",
    public_key=None,
    audience="https://mcp.example.com/mcp",
    required_scopes=("gcp:read",),
    base_url="https://mcp.example.com",
)


def test_default_transport_is_stdio():
    assert Settings().config() == StdioConfig()


def test_http_config_carries_host_port_and_base_url():
    config = Settings(transport="http", port=9000).config()

    assert config == HttpConfig(host="127.0.0.1", port=9000, base_url=None, auth=None)


def test_environment_selects_the_transport(monkeypatch):
    monkeypatch.setenv("GCP_MCP_TRANSPORT", "http")

    assert isinstance(Settings().config(), HttpConfig)


def test_unknown_transport_is_refused():
    with pytest.raises(ValidationError):
        Settings(transport="carrier-pigeon")


def test_require_auth_builds_an_auth_config():
    config = Settings(**AUTH, auth_required_scopes=["gcp:read"]).config()

    assert config.auth == READ_SCOPED_AUTH


def test_audience_defaults_to_the_mcp_endpoint_without_a_double_slash():
    config = Settings(**AUTH | {"base_url": "https://mcp.example.com/"}).config()

    assert config.auth.audience == "https://mcp.example.com/mcp"


def test_auth_audience_overrides_the_default():
    config = Settings(**AUTH, auth_audience="https://audience.example.com").config()

    assert config.auth.audience == "https://audience.example.com"


def test_require_auth_is_a_bare_cli_flag():
    settings = CliApp.run(
        Settings,
        cli_args=[
            "--transport", "http",
            "--require-auth",
            "--base-url", "https://mcp.example.com",
            "--auth-issuer", "https://issuer.example.com",
            "--auth-jwks-uri", "https://issuer.example.com/jwks.json",
            "--auth-required-scopes", "gcp:read,gcp:list",
        ],
    )

    assert settings.config().auth.required_scopes == ("gcp:read", "gcp:list")


def test_require_auth_reads_the_environment(monkeypatch):
    monkeypatch.setenv("GCP_MCP_TRANSPORT", "http")
    monkeypatch.setenv("GCP_MCP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("GCP_MCP_BASE_URL", "https://mcp.example.com")
    monkeypatch.setenv("GCP_MCP_AUTH_ISSUER", "https://issuer.example.com")
    monkeypatch.setenv("GCP_MCP_AUTH_JWKS_URI", "https://issuer.example.com/jwks.json")
    monkeypatch.setenv("GCP_MCP_AUTH_REQUIRED_SCOPES", '["gcp:read"]')

    assert Settings().config().auth == READ_SCOPED_AUTH


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"transport": "stdio"}, "require_auth needs transport=http"),
        ({"base_url": None}, "require_auth needs base_url"),
        ({"auth_issuer": None}, "require_auth needs auth_issuer"),
        ({"auth_jwks_uri": None}, "require_auth needs exactly one of auth_jwks_uri"),
        ({"auth_public_key": PUBLIC_KEY}, "require_auth needs exactly one of auth_jwks_uri"),
    ],
)
def test_require_auth_refuses_an_incomplete_setup(overrides, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**AUTH | overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auth_issuer", "https://issuer.example.com"),
        ("auth_jwks_uri", "https://issuer.example.com/jwks.json"),
        ("auth_public_key", PUBLIC_KEY),
        ("auth_audience", "https://mcp.example.com/mcp"),
        ("auth_required_scopes", ["gcp:read"]),
    ],
)
def test_auth_settings_without_require_auth_are_refused(field, value):
    with pytest.raises(ValidationError, match=f"require_auth is false.*{field}"):
        Settings(transport="http", **{field: value})


def test_a_public_key_keeps_its_pem_text():
    config = Settings(**AUTH | {"auth_jwks_uri": None, "auth_public_key": PUBLIC_KEY}).config()

    assert config.auth.public_key == PUBLIC_KEY


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(PUBLIC_KEY.splitlines()[0], id="first line only"),
        pytest.param("not a key", id="not pem"),
    ],
)
def test_a_public_key_that_is_not_a_pem_rsa_key_is_refused(key):
    with pytest.raises(ValidationError, match="GCP_MCP_AUTH_PUBLIC_KEY"):
        Settings(**AUTH | {"auth_jwks_uri": None, "auth_public_key": key})
