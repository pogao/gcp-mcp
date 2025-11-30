from fastmcp import FastMCP
from logging_config import setup_logging

setup_logging()

mcp = FastMCP(
    name="GCPMCPServer",
    instructions="""
        This MCP server provides read-only access to a Google Cloud Platform,
        organization, its services and resources.

        Use this MCP server if you need to interact with the user's GCP
        organization and gather information about it.
    """,
)

from gcp.compute import instances
from gcp.compute import firewalls
