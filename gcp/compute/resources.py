from typing import Any, Literal

from fastmcp.exceptions import ToolError

from app import mcp
from gcp.compute.registry import RESOURCE_TYPES, ReadMethod, call_read_method

ResourceTypeName = Literal[tuple(RESOURCE_TYPES)]

SCOPE_FIELDS = frozenset({"project", "parent_id"})
LOCATION_FIELDS = frozenset({"zone", "region"})
# Compute rejects a page size above 500.
MAX_PAGE_SIZE = 500


def _accepted(read_method: ReadMethod, params: dict) -> dict:
    fields = read_method.required + read_method.optional
    return {k: v for k, v in params.items() if v is not None and k in fields}


@mcp.tool()
def list_compute_resource_types(
    resource_type: ResourceTypeName | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    Discovers the Compute Engine resource types and read-only methods this
    server can query. Call it with no argument to see every resource type and
    its method names. Call it with a resource_type to see each method's
    required and optional parameters.

    Use it to find the method name and parameter names to pass to
    call_compute_read_method.

    Args:
        resource_type: Optional. A resource type such as 'instances' or
        'backend_services'.
    """
    if resource_type is None:
        return [
            {"resource_type": rt.name, "methods": list(rt.methods)}
            for rt in RESOURCE_TYPES.values()
        ]
    return {
        "resource_type": resource_type,
        "methods": {
            m.name: {"required": list(m.required), "optional": list(m.optional)}
            for m in RESOURCE_TYPES[resource_type].methods.values()
        },
    }


@mcp.tool()
def list_compute_resources(
    resource_type: ResourceTypeName,
    project_id: str | None = None,
    zone: str | None = None,
    region: str | None = None,
    filter: str | None = None,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """
    Lists Compute Engine resources of one type, such as VM instances, disks,
    firewall rules, networks, subnetworks or backend services. Each item is
    the full resource as the Compute REST API returns it.

    Omit zone and region to list across all zones or regions where the API
    supports it. Each item then carries a 'scope' such as
    'zones/us-central1-a'.

    Args:
        resource_type: The resource type, e.g. 'instances' or 'firewalls'.
        project_id: The project to list. Most resource types require it.
        zone: Optional. Limit to one zone, e.g. 'us-central1-a'.
        region: Optional. Limit to one region, e.g. 'us-central1'.
        filter: Optional. A Compute filter expression, e.g.
        'name = "web-*"' or 'status = RUNNING'.
        max_results: Optional. The maximum number of items to return.
    """
    rt = RESOURCE_TYPES[resource_type]
    candidates = {"project": project_id, "zone": zone, "region": region, "filter": filter}
    list_methods = [rt.methods[m] for m in ("list", "aggregated_list") if m in rt.methods]
    if not list_methods:
        raise ToolError(
            f"'{resource_type}' has no list method. "
            "Use call_compute_read_method with one of: " + ", ".join(rt.methods)
        )
    for read_method in list_methods:
        params = _accepted(read_method, candidates)
        if set(read_method.required) <= set(params):
            if max_results is not None and "max_results" in read_method.optional:
                params["max_results"] = min(max_results, MAX_PAGE_SIZE)
            return call_read_method(
                resource_type, read_method.name, params, limit=max_results
            )
    first = list_methods[0]
    missing = sorted(set(first.required) - set(_accepted(first, candidates)))
    raise ToolError(f"Listing '{resource_type}' requires: {', '.join(missing)}")


@mcp.tool()
def get_compute_resource(
    resource_type: ResourceTypeName,
    name: str | None = None,
    project_id: str | None = None,
    zone: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """
    Fetches the full details of one Compute Engine resource by name, such as
    a VM instance, disk, firewall rule, network or backend service.

    Pass zone for zonal resources (instances, disks) and region for regional
    ones (subnetworks, region_backend_services). Project-wide singletons such
    as 'projects' need no name.

    Args:
        resource_type: The resource type, e.g. 'instances' or 'firewalls'.
        name: The resource name, e.g. 'web-1' or 'allow-ssh-ingress'.
        project_id: The project that owns the resource.
        zone: The zone of a zonal resource, e.g. 'us-central1-a'.
        region: The region of a regional resource, e.g. 'us-central1'.
    """
    rt = RESOURCE_TYPES[resource_type]
    read_method = rt.methods.get("get")
    if read_method is None:
        raise ToolError(
            f"'{resource_type}' has no get method. "
            "Use call_compute_read_method with one of: " + ", ".join(rt.methods)
        )
    # For regions and zones the name field is `region` or `zone` itself.
    name_fields = [f for f in read_method.required if f not in SCOPE_FIELDS]
    if len(name_fields) > 1:
        name_fields = [f for f in name_fields if f not in LOCATION_FIELDS]
    if len(name_fields) > 1:
        raise ToolError(
            f"Getting '{resource_type}' needs {', '.join(name_fields)}. "
            "Use call_compute_read_method with method 'get'."
        )
    params = _accepted(read_method, {"project": project_id, "zone": zone, "region": region})
    if name_fields:
        if name is not None:
            params[name_fields[0]] = name
        elif name_fields[0] not in params:
            raise ToolError(f"Getting '{resource_type}' requires a name.")
    return call_read_method(resource_type, "get", params)


@mcp.tool()
def call_compute_read_method(
    resource_type: ResourceTypeName, method: str, params: dict[str, Any]
) -> dict[str, Any] | list[dict[str, Any]]:
    """
    Calls any read-only Compute Engine method that the list and get tools do
    not cover. Use list_compute_resource_types to find the method names and
    the parameter names for a resource type. Parameters use the API's
    snake_case field names, e.g. 'project', 'zone', 'instance'.

    Examples:
    * instances / get_serial_port_output: a VM's serial console output.
    * instances / get_effective_firewalls: the firewall rules applied to a VM.
    * backend_services / get_health: health of a backend service's backends.
    * instance_group_managers / list_managed_instances: a MIG's instances.
    * subnetworks / get_iam_policy: who can use a subnetwork.

    Args:
        resource_type: The resource type, e.g. 'instances'.
        method: A read method of that resource type, e.g. 'get_health'.
        params: The request fields, e.g. {"project": "p", "zone": "z",
        "instance": "vm-1"}.
    """
    return call_read_method(resource_type, method, params)
