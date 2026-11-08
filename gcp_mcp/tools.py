from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


def read_only_tool(server: FastMCP, tool: Tool) -> Tool:
    return server.add_tool(tool.model_copy(update={"annotations": READ_ONLY}))
