import asyncio
import ipaddress
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from typing import Any

import fastmcp
import structlog
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools import Tool
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from gcp_mcp.rest import Gcp, Limits, make_gcp
from gcp_mcp.settings import MCP_PATH, HttpConfig, ServerConfig, Settings, StdioConfig
from gcp_mcp.tools import make_tools, read_only_tool

STATELESS_HTTP = True
IAT_LEEWAY_S = 300

INSTRUCTIONS = """\
Read-only access to every Google Cloud service. Use the tools in this order:

1. gcp_services finds the service, such as "compute" or "storage". Pass a query.
2. gcp_methods lists the read methods of the service, such as "instances.aggregatedList".
3. gcp_describe_method shows the parameters, request body, and pagination of a method.
4. gcp_call runs the method and returns its JSON response.

This server is read-only. It cannot create, change, or delete anything, and it refuses a
mutating method with the reason. gcp_call takes path and query parameters in `args`, keyed by
the API's own parameter names, and a request body in `body` for POST reads. Responses are cut
to a size limit, so add "fields" to `args` to get only the fields you need. One call returns
one page. When next_page_token is set, pass it as pageToken to get the next page. Fields that
look like credentials arrive as "[redacted]". An error about the server's own credentials or
permissions is for the operator to fix, not you.
"""

log = structlog.get_logger(__name__)


def build_server(settings: Settings, gcp: Gcp | None = None) -> FastMCP:
    fastmcp.settings.check_for_updates = "off"
    if gcp is None:
        gcp = make_gcp(
            Limits(
                default_page_size=settings.default_page_size,
                max_response_bytes=settings.max_response_bytes,
                request_timeout_s=settings.request_timeout_s,
            )
        )
    server = FastMCP(
        "gcp-mcp",
        instructions=INSTRUCTIONS,
        version=version("gcp-mcp"),
        auth=auth_provider(settings.config()),
    )
    for tool in (*make_tools(gcp), *legacy_tools()):
        read_only_tool(server, tool)
    server.custom_route("/healthz", methods=["GET"])(healthz)
    return server


def legacy_tools() -> Sequence[Tool]:
    from app import mcp

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, mcp.list_tools()).result()


def auth_provider(config: ServerConfig) -> RemoteAuthProvider | None:
    match config:
        case StdioConfig() | HttpConfig(auth=None):
            return None
        case HttpConfig(auth=auth):
            verifier = StrictJWTVerifier(
                jwks_uri=auth.jwks_uri,
                public_key=auth.public_key,
                issuer=auth.issuer,
                audience=auth.audience,
                required_scopes=list(auth.required_scopes),
                allowed_subjects=auth.allowed_subjects,
            )
            return RemoteAuthProvider(
                token_verifier=verifier,
                authorization_servers=[auth.issuer],
                base_url=auth.base_url,
                resource_name="gcp-mcp",
            )


class StrictJWTVerifier(JWTVerifier):
    def __init__(self, *, allowed_subjects: tuple[str, ...], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.allowed_subjects = allowed_subjects

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await super().verify_token(token)
        if access is None:
            return None
        if reason := refusal(access.claims, self.allowed_subjects):
            log.info(reason, sub=access.claims.get("sub"), email=access.claims.get("email"))
            return None
        return access


def refusal(claims: Mapping[str, Any], allowed_subjects: tuple[str, ...]) -> str | None:
    now = time.time()
    if claims.get("exp") is None:
        return "token refused: no exp claim"
    if not starts_by(claims.get("nbf"), now):
        return "token refused: nbf has not passed"
    if not starts_by(claims.get("iat"), now + IAT_LEEWAY_S):
        return "token refused: iat is too far ahead"
    identities = (claims.get("sub"), claims.get("email"))
    if allowed_subjects and not any(identity in allowed_subjects for identity in identities):
        return "token refused: subject is not allowed"
    return None


def starts_by(start: object, deadline: float) -> bool:
    return start is None or (isinstance(start, int | float) and start <= deadline)


def http_app(server: FastMCP) -> StarletteWithLifespan:
    return server.http_app(path=MCP_PATH, stateless_http=STATELESS_HTTP)


def serve(server: FastMCP, config: ServerConfig) -> None:
    match config:
        case StdioConfig():
            server.run("stdio", show_banner=False)
        case HttpConfig(host=host, port=port, auth=auth):
            if auth is None and not is_loopback(host):
                log.warning("serving without authentication on a non-loopback host", host=host)
            server.run(
                "http",
                show_banner=False,
                host=host,
                port=port,
                path=MCP_PATH,
                stateless_http=STATELESS_HTTP,
                uvicorn_config={"log_config": None},
            )


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")
