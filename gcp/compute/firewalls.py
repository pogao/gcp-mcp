from app import mcp
from google.cloud import compute_v1
from gcp.utils import handle_gcp_exceptions


@mcp.tool()
def unsafe_ssh_exposure(project_id: str) -> list[dict[str, str]]:
    """
    Analyses all firewall rules looking for rules that expose SSH
    to anyone on the internet. This means, if any firewall rule
    allows source range 0.0.0.0/0 to connect to port 22 TCP
    exists in the project, we'll consider that as being a security problem.

    This tool will return the name of the firewall rule and the name of the
    VPC network.

    Args:
    project_id: the project ID of the project that contains the
    firewall rules we're going to review.
    """
    return unsafe_ssh_exposure_logic(project_id)


OPEN_INTERNET_RANGES = frozenset({"0.0.0.0/0", "::/0"})
TCP_PROTOCOLS = frozenset({"tcp", "6", "all"})


def _covers_port(port_spec: str, port: int) -> bool:
    low, _, high = port_spec.partition("-")
    return int(low) <= port <= int(high or low)


def _allows_ssh(allowed: compute_v1.Allowed) -> bool:
    # An empty port list allows every port of the protocol.
    return allowed.I_p_protocol in TCP_PROTOCOLS and (
        not allowed.ports or any(_covers_port(p, 22) for p in allowed.ports)
    )


@handle_gcp_exceptions
def unsafe_ssh_exposure_logic(project_id: str) -> list:
    with compute_v1.FirewallsClient() as client:
        request = compute_v1.ListFirewallsRequest(project=project_id)
        firewall_rules = client.list(request)
        unsafe_rules = [
            {
                "name": rule.name,
                "network": rule.network.split("/")[-1],
            }
            for rule in firewall_rules
            if (
                OPEN_INTERNET_RANGES.intersection(rule.source_ranges)
                and not rule.disabled
                and rule.direction == "INGRESS"
                and any(_allows_ssh(allowed) for allowed in rule.allowed)
            )
        ]

        return unsafe_rules
