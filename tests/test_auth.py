import time
from contextlib import asynccontextmanager

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from joserfc import jwt
from joserfc.jwk import RSAKey

from gcp_mcp.server import auth_provider, build_server, http_app
from gcp_mcp.settings import HttpConfig, Settings, StdioConfig

BASE_URL = "https://mcp.example.com"
MCP_URL = f"{BASE_URL}/mcp"
METADATA_URL = f"{BASE_URL}/.well-known/oauth-protected-resource/mcp"
ISSUER = "https://issuer.example.com"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def issuer_key():
    return RSAKey.generate_key(2048)


@pytest.fixture(scope="module")
def auth_settings(issuer_key):
    return Settings(
        transport="http",
        require_auth=True,
        base_url=BASE_URL,
        auth_issuer=ISSUER,
        auth_public_key=issuer_key.as_pem(private=False).decode(),
        auth_required_scopes=["gcp:read"],
    )


def token(key, **claims):
    defaults = {
        "iss": ISSUER,
        "aud": MCP_URL,
        "sub": "example-client",
        "scope": "gcp:read",
        "exp": int(time.time()) + 300,
    }
    payload = {name: value for name, value in (defaults | claims).items() if value is not None}
    return jwt.encode({"alg": "RS256"}, payload, key)


@asynccontextmanager
async def serving(settings):
    app = http_app(build_server(settings))
    async with app.lifespan(app):
        yield httpx2.ASGITransport(app=app)


async def post_mcp(transport, bearer=None):
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http:
        return await http.post("/mcp", json={}, headers=headers)


async def list_tools(transport, bearer=None):
    def asgi_client(**kwargs):
        return httpx2.AsyncClient(transport=transport, **kwargs)

    mcp = StreamableHttpTransport(MCP_URL, auth=bearer, httpx_client_factory=asgi_client)
    async with Client(mcp) as client:
        return [tool.name for tool in await client.list_tools()]


@pytest.mark.anyio
async def test_a_request_without_a_token_gets_a_401_challenge(auth_settings):
    async with serving(auth_settings) as transport:
        response = await post_mcp(transport)

    assert response.status_code == 401
    assert f'resource_metadata="{METADATA_URL}"' in response.headers["www-authenticate"]


@pytest.mark.anyio
async def test_the_metadata_document_names_the_issuer_and_the_resource(auth_settings):
    async with serving(auth_settings) as transport:
        async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            response = await http.get(METADATA_URL)

    assert response.status_code == 200
    assert response.json()["authorization_servers"] == [ISSUER]
    assert response.json()["resource"] == MCP_URL
    assert response.json()["scopes_supported"] == ["gcp:read"]


@pytest.mark.anyio
async def test_a_token_for_the_advertised_resource_lists_tools(auth_settings, issuer_key):
    async with serving(auth_settings) as transport:
        async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as http:
            resource = (await http.get(METADATA_URL)).json()["resource"]
        names = await list_tools(transport, bearer=token(issuer_key, aud=resource))

    assert "list_gcs_buckets" in names


@pytest.mark.anyio
async def test_a_valid_token_passes_the_bearer_check(auth_settings, issuer_key):
    async with serving(auth_settings) as transport:
        response = await post_mcp(transport, token(issuer_key))

    assert response.status_code != 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"aud": "https://other.example.com/mcp"}, id="wrong audience"),
        pytest.param({"aud": BASE_URL}, id="base url audience"),
        pytest.param({"scope": "openid"}, id="missing scope"),
        pytest.param({"exp": int(time.time()) - 60}, id="expired"),
        pytest.param({"exp": None}, id="no expiry"),
        pytest.param({"iss": "https://other.example.com"}, id="wrong issuer"),
    ],
)
async def test_a_bad_token_is_refused(auth_settings, issuer_key, claims):
    async with serving(auth_settings) as transport:
        response = await post_mcp(transport, token(issuer_key, **claims))

    assert response.status_code == 401


@pytest.mark.anyio
async def test_a_token_signed_by_another_key_is_refused(auth_settings):
    forged = token(RSAKey.generate_key(2048))

    async with serving(auth_settings) as transport:
        response = await post_mcp(transport, forged)

    assert response.status_code == 401


@pytest.mark.anyio
async def test_without_require_auth_the_same_app_lists_tools_with_no_token():
    async with serving(Settings(transport="http")) as transport:
        names = await list_tools(transport)

    assert "list_gcs_buckets" in names


def test_stdio_and_open_http_have_no_auth_provider():
    assert auth_provider(StdioConfig()) is None
    assert auth_provider(HttpConfig(host="127.0.0.1", port=8888, base_url=None, auth=None)) is None
