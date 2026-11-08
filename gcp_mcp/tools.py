from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from functools import wraps
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from gcp_mcp.discovery import Param
from gcp_mcp.errors import GcpMcpError
from gcp_mcp.rest import Gcp

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
DESCRIPTION_LIMIT = 200


def read_only_tool(server: FastMCP, tool: Tool) -> Tool:
    return server.add_tool(tool.model_copy(update={"annotations": READ_ONLY}))


def llm_errors[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @wraps(fn)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except GcpMcpError as error:
            raise ToolError(error.for_llm()) from error

    return guarded


def make_tools(gcp: Gcp) -> tuple[Tool, ...]:
    registry = gcp.registry

    async def gcp_services(query: str | None = None) -> dict[str, Any]:
        """Which Google Cloud services can this server read? Lists about 194 services, so pass
        `query` to filter by name or title, such as "kubernetes" or "storage". Returns each
        service's name, title, default_version, other bundled versions, number of read_methods,
        and docs_url. Pass the name as `service` to the other tools."""
        services = [
            {
                "name": ref.name,
                "title": ref.title,
                "default_version": ref.default_version,
                "versions": list(ref.versions),
                "read_methods": ref.read_methods,
                "docs_url": ref.docs_url,
            }
            for ref in registry.services(query)
        ]
        return {"services": services, "count": len(services)}

    async def gcp_methods(
        service: str, query: str | None = None, version: str | None = None
    ) -> dict[str, Any]:
        """Which read methods does a service have? Pass the service name from gcp_services and
        an optional `query` that filters method names, such as "instances". Returns
        `resources`, which maps a resource such as "instances" to its methods such as "get";
        join the two with a dot to get the method name "instances.get". Methods on the service
        itself sit under the empty resource "". Mutating methods are not listed. Pass `version`
        only to read a non-default version that gcp_services lists."""
        spec = registry.service(service, version)
        needle = (query or "").lower()
        resources: defaultdict[str, list[str]] = defaultdict(list)
        for name in spec.methods:
            if needle in name.lower():
                resource, _, method = name.rpartition(".")
                resources[resource].append(method)
        return {
            "service": spec.name,
            "version": spec.version,
            "resources": dict(resources),
            "read_methods": len(spec.methods),
            "excluded_methods": len(spec.excluded),
        }

    async def gcp_describe_method(
        service: str, method: str, version: str | None = None
    ) -> dict[str, Any]:
        """What arguments does a read method take? Pass the service and the method name from
        gcp_methods, such as "instances.get". Returns its params (name, path or query
        location, type, required, enum), the request body fields for POST reads, the top-level
        response fields, and how it pages. Pass the params to gcp_call in `args` under these
        names, which are the API's own parameter names, and the body fields in `body`."""
        read = registry.method(service, method, version)
        spec = registry.service(service, read.version)
        return {
            "service": read.service,
            "version": read.version,
            "method": read.name,
            "description": brief(read.description),
            "http_method": read.http_method,
            "path": read.path_template,
            "params": [describe_param(param) for param in read.params.values()],
            "body": (
                {
                    "schema": read.body_schema,
                    "properties": properties(spec.schemas, read.body_schema),
                }
                if read.accepts_body
                else None
            ),
            "response_schema": read.response_schema,
            "response_properties": properties(spec.schemas, read.response_schema),
            "pagination": asdict(read.pagination) if read.pagination else None,
            "deprecated": read.deprecated,
            "docs_url": spec.docs_url,
        }

    return tuple(
        Tool.from_function(llm_errors(fn))
        for fn in (gcp_services, gcp_methods, gcp_describe_method)
    )


def describe_param(param: Param) -> dict[str, Any]:
    return {
        "name": param.name,
        "location": param.location,
        "type": param.type,
        "required": param.required,
        "repeated": param.repeated,
        "enum": list(param.enum) if param.enum else None,
        "description": brief(param.description),
    }


def properties(schemas: Mapping[str, Any], schema: str | None) -> dict[str, str]:
    fields = schemas.get(schema, {}).get("properties", {}) if schema else {}
    return {name: brief(field.get("description", "")) for name, field in fields.items()}


def brief(text: str) -> str:
    if len(text) <= DESCRIPTION_LIMIT:
        return text
    return text[: DESCRIPTION_LIMIT - 3] + "..."
