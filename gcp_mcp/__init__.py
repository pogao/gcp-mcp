"""Read-only MCP server for Google Cloud."""

from gcp_mcp.discovery import Registry
from gcp_mcp.rest import Gcp
from gcp_mcp.server import build_server
from gcp_mcp.settings import Settings

__all__ = ["Gcp", "Registry", "Settings", "build_server"]
