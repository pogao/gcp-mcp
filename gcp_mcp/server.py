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

from gcp_mcp.settings import MCP_PATH, HttpConfig, ServerConfig, Settings, StdioConfig
from gcp_mcp.tools import read_only_tool

STATELESS_HTTP = True
IAT_LEEWAY_S = 300

INSTRUCTIONS = """\
Read-only access to Google Cloud. This server cannot create, change, or delete
anything. Tools for every Google Cloud service will arrive through API discovery.
"""

log = structlog.get_logger(__name__)


def build_server(settings: Settings) -> FastMCP:
    fastmcp.settings.check_for_updates = "off"
    server = FastMCP(
        "gcp-mcp",
        instructions=INSTRUCTIONS,
        version=version("gcp-mcp"),
        auth=auth_provider(settings.config()),
    )
    for tool in legacy_tools():
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
        if access is None or access.expires_at is None or not_yet_valid(access.claims):
            return None
        if not subject_allowed(access.claims, self.allowed_subjects):
            return None
        return access


def subject_allowed(claims: Mapping[str, Any], allowed_subjects: tuple[str, ...]) -> bool:
    if not allowed_subjects:
        return True
    return any(claims.get(name) in allowed_subjects for name in ("sub", "email"))


def not_yet_valid(claims: Mapping[str, Any]) -> bool:
    now = time.time()
    for name, leeway in (("nbf", 0), ("iat", IAT_LEEWAY_S)):
        start = claims.get(name, now)
        if not isinstance(start, int | float) or start > now + leeway:
            return True
    return False


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
