from app import mcp
from google.cloud import compute_v1
from gcp.utils import handle_gcp_exceptions


@mcp.tool()
def unsafe_ssh_exposure(project_id: str) -> list:
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


@handle_gcp_exceptions
def unsafe_ssh_exposure_logic(project_id: str) -> list:
    with compute_v1.FirewallsClient() as client:
        request = compute_v1.ListFirewallsRequest(project=project_id)
        firewall_rules = client.list(request)
        unsafe_rules = [
            {
                "name": rule.self_link.split("/")[-1],
                "network": rule.network.split("/")[-1],
            }
            for rule in firewall_rules
            if (
                "0.0.0.0/0" in rule.source_ranges
                and rule.enabled
                and rule.direction == "INGRESS"
                and any(
                    allowed.ip_protocol == "tcp" and "22" in allowed.ports
                    for allowed in rule.allowed
                )
            )
        ]

        return unsafe_rules
