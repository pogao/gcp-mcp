import json
from app import mcp
from google.cloud import compute_v1
from gcp.utils import handle_gcp_exceptions


@mcp.tool()
def list_firewall_rules(project_id: str):
    """
    Retrieves a comprehensive list of all firewall rules within a specified Google Cloud project.
    Use this tool for broad queries about firewall configurations or when you need to see all rules at once.

    Args:
        project_id: The unique identifier for the Google Cloud project.

    Returns:
        A list of dictionaries, where each dictionary represents a complete firewall rule.
    """
    return list_firewall_rules_logic(project_id)


@handle_gcp_exceptions
def list_firewall_rules_logic(project_id: str):
    results = []
    with compute_v1.FirewallsClient() as client:
        request = compute_v1.ListFirewallsRequest(project=project_id)
        firewall_rules = client.list(request)

        for rule in firewall_rules:
            firewall_data = {
                "name": rule.name,
                "network": rule.network,
                "direction": rule.direction,
                "allowed": rule.allowed,
                "source_ranges": rule.source_ranges,
                "source_tags": rule.source_tags,
                "destination_ranges": rule.destination_ranges,
                "disabled": rule.disabled,
                "priority": rule.priority,
                "self_link": rule.self_link,
        }

        results.append(firewall_data)

    return results


@mcp.tool()
def list_firewall_rules_per_vpc(project_id: str, vpc_name: str):
    """
    Allows you to only list the firewall rules crreated for a
    specific VPC network name. The list of rules are returned in
    JSON format.

    Args:
    * project_id: the project ID where the VPC network and firewall rules
    you're interested in were created.
    * vpc_name: the name of the VPC network that contains the firewall
    rules you're interested in. This is just the network name as a string,
    not the fully qualified name of the network.
    Wrong vpc_name:
    "https://www.googleapis.com/compute/v1/projects/example-project/global/networks/default"
    Correct vpc_name: "default"
    """
    return list_firewall_rules_per_vpc_logic(project_id, vpc_name)


@handle_gcp_exceptions
def list_firewall_rules_per_vpc_logic(project_id: str, vpc_name: str):
    vpc_rules_dict = []
    with compute_v1.FirewallsClient() as client:
        request = compute_v1.ListFirewallsRequest(project=project_id)
        firewall_rules = client.list(request)
        for rule in firewall_rules:
            if rule.network.endswith(f"/{vpc_name}"):
                rule_data = {
                    "name": rule.name,
                    "network": rule.network,
                    "direction": rule.direction,
                    "allowed": rule.allowed,
                    "source_ranges": rule.source_ranges,
                    "source_tags": rule.source_tags,
                    "destination_ranges": rule.destination_ranges,
                    "disabled": rule.disabled,
                    "priority": rule.priority,
                    "self_link": rule.self_link,
                }
                vpc_rules_dict.append(rule_data)

    return vpc_rules_dict


@mcp.tool()
def describe_firewall_rule(project_id: str, rule_name: str):
    """
    Given a firewall rule name in parameter rule_name, this function will
    retrieve and return all the details of such firewall rule.

    Args:
    * project_id: the project where the firewall rule is created. Must be
    a string.
    * rule_name: the name of the firewall rule, as it shows up in the
    last part of the Self Link: "allow-ssh-ingress", for example.
    """
    return describe_firewall_rule_logic(project_id, rule_name)


@handle_gcp_exceptions
def describe_firewall_rule_logic(project_id: str, rule_name: str):
    with compute_v1.FirewallsClient() as client:
        request = compute_v1.GetFirewallRequest(
            project=project_id, firewall=rule_name)

        firewall_rule = client.get(request=request)

        firewall_rule_dict = {
            "name": firewall_rule.name,
            "network": firewall_rule.network,
            "direction": firewall_rule.direction,
            "allowed": firewall_rule.allowed,
            "source_ranges": firewall_rule.source_ranges,
            "source_tags": firewall_rule.source_tags,
            "destination_ranges": firewall_rule.destination_ranges,
            "disabled": firewall_rule.disabled,
            "priority": firewall_rule.priority,
            "self_link": firewall_rule.self_link,
        }

        return firewall_rule_dict


@mcp.tool()
def unsafe_ssh_exposure(project_id: str):
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
def unsafe_ssh_exposure_logic(project_id: str):
    with compute_v1.FirewallsClient() as client:
        request = compute_v1.ListFirewallsRequest(project=project_id)
        firewall_rules = client.list(request)
        unsafe_rules_as_dicts = [
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

        return unsafe_rules_as_dicts
