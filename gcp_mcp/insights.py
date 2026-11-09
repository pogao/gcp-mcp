from collections.abc import Mapping
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool

from gcp_mcp.rest import Gcp
from gcp_mcp.tools import llm_errors

SSH_PORT = 22
SSH_PROTOCOLS = frozenset({"tcp", "6", "all"})
EVERYONE = frozenset({"0.0.0.0/0", "::/0"})
PUBLIC_MEMBERS = frozenset({"allUsers", "allAuthenticatedUsers"})
FIREWALL_FIELDS = "items(name,network,direction,disabled,sourceRanges,allowed),nextPageToken"
POLICY_VERSION = 3


def ssh_open_to_world(rule: Mapping[str, Any]) -> bool:
    return (
        rule.get("direction") == "INGRESS"
        and not rule.get("disabled", False)
        and not EVERYONE.isdisjoint(rule.get("sourceRanges", ()))
        and any(opens_ssh(entry) for entry in rule.get("allowed", ()))
    )


def opens_ssh(entry: Mapping[str, Any]) -> bool:
    if entry.get("IPProtocol") not in SSH_PROTOCOLS:
        return False
    ports = entry.get("ports")
    return not ports or any(covers(spec, SSH_PORT) for spec in ports)


def covers(spec: str, port: int) -> bool:
    low, _, high = spec.partition("-")
    return int(low) <= port <= int(high or low)


def owners(policy: Mapping[str, Any]) -> list[str]:
    return [
        member
        for binding in policy.get("bindings", ())
        if binding.get("role") == "roles/owner"
        for member in binding.get("members", ())
    ]


def public_bindings(policy: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": binding["role"], "member": member}
        for binding in policy.get("bindings", ())
        for member in binding.get("members", ())
        if member in PUBLIC_MEMBERS
    ]


def exposure(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": rule.get("name"),
        "network": rule.get("network", "").rpartition("/")[2],
        "source_ranges": sorted(EVERYONE.intersection(rule.get("sourceRanges", ()))),
        "ports": [
            port
            for entry in rule.get("allowed", ())
            if opens_ssh(entry)
            for port in entry.get("ports") or ["all"]
        ],
    }


async def every_item(
    gcp: Gcp, service: str, method: str, args: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    token = None
    while True:
        result = await gcp.call(service, method, {**args, "pageToken": token})
        if result.truncated:
            raise ToolError(
                f"A page of {service} {method} did not fit in max_response_bytes, so the check "
                "would miss resources. Raise --max-response-bytes and call again."
            )
        items.extend(result.response.get("items", ()))
        token = result.next_page_token
        if token is None:
            return items


def make_tools(gcp: Gcp) -> tuple[Tool, ...]:
    async def unsafe_ssh_exposure(project: str) -> dict[str, Any]:
        """Which firewall rules let anyone on the internet reach SSH? Walks every firewall rule
        of the project and keeps the enabled ingress rules that allow TCP port 22, a range that
        covers it, or every port, from 0.0.0.0/0 or ::/0. Returns each such rule's name, network,
        offending source ranges, and ports, where "all" means every port, plus `count`."""
        args = {"project": project, "fields": FIREWALL_FIELDS}
        rules = await every_item(gcp, "compute", "firewalls.list", args)
        exposed = [exposure(rule) for rule in rules if ssh_open_to_world(rule)]
        return {"project": project, "rules": exposed, "count": len(exposed)}

    async def find_project_owners(project: str) -> dict[str, Any]:
        """Who holds the basic Owner role on a project? Reads the project's IAM policy and returns
        every member of a roles/owner binding, such as "user:name@example.com" or
        "serviceAccount:...", plus `count`. A conditional binding counts, because its members can
        become owners when the condition holds."""
        result = await gcp.call(
            "cloudresourcemanager",
            "projects.getIamPolicy",
            {"resource": f"projects/{project}"},
            {"options": {"requestedPolicyVersion": POLICY_VERSION}},
        )
        found = owners(result.response)
        return {"project": project, "owners": found, "count": len(found)}

    async def is_bucket_public(bucket: str) -> dict[str, Any]:
        """Does a Cloud Storage bucket grant access to the public? Reads the bucket's IAM policy
        and returns `public`, true when any binding names allUsers or allAuthenticatedUsers, and
        `bindings`, each with the role and the public member. Pass the bucket name without a
        gs:// prefix."""
        args = {"bucket": bucket, "optionsRequestedPolicyVersion": POLICY_VERSION}
        result = await gcp.call("storage", "buckets.getIamPolicy", args)
        bindings = public_bindings(result.response)
        return {"bucket": bucket, "public": bool(bindings), "bindings": bindings}

    return tuple(
        Tool.from_function(llm_errors(fn))
        for fn in (unsafe_ssh_exposure, find_project_owners, is_bucket_public)
    )
