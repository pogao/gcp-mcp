from app import mcp
from starlette.requests import Request
from starlette.responses import PlainTextResponse


@mcp.custom_route("/healthz", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """
    health_check provides a simple way to check whether the MCP Server
    is up and running.

    It returns a plaintext "OK" when path /healthz is requested.
    """

    return PlainTextResponse("OK")


def main():
    print("Hello from gcp-mcp!")


if __name__ == "__main__":
    mcp.run(transport="http", port=8888, log_level="DEBUG")
