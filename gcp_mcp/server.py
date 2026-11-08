import asyncio
import ipaddress
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version

import structlog
from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools import Tool
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from gcp_mcp.settings import HttpConfig, ServerConfig, Settings, StdioConfig
from gcp_mcp.tools import read_only_tool

STATELESS_HTTP = True

INSTRUCTIONS = """\
Read-only access to Google Cloud. This server cannot create, change, or delete
anything. Tools for every Google Cloud service will arrive through API discovery.
"""

log = structlog.get_logger(__name__)


def build_server(settings: Settings) -> FastMCP:
    server = FastMCP("gcp-mcp", instructions=INSTRUCTIONS, version=version("gcp-mcp"))
    for tool in legacy_tools():
        read_only_tool(server, tool)
    server.custom_route("/healthz", methods=["GET"])(healthz)
    return server


def legacy_tools() -> Sequence[Tool]:
    from app import mcp

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, mcp.list_tools()).result()


def http_app(server: FastMCP) -> StarletteWithLifespan:
    return server.http_app(stateless_http=STATELESS_HTTP)


def serve(server: FastMCP, config: ServerConfig) -> None:
    match config:
        case StdioConfig():
            server.run("stdio", show_banner=False)
        case HttpConfig(host=host, port=port):
            if not is_loopback(host):
                log.warning("serving without authentication on a non-loopback host", host=host)
            server.run(
                "http",
                show_banner=False,
                host=host,
                port=port,
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
