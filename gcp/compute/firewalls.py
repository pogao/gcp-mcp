import json
from app import mcp
from google.cloud import compute_v1
from gcp.utils import handle_gcp_exceptions


@mcp.tool()
def list_firewall_rules(project_id: str):
    """
    Lists all firewall rules, for all VPC in project_id. The result
    is returned in JSON.

    Args:
    * project_id: the project ID of the project you want to list the
    firewall rules. It needs to be a string.
    """
    return list_firewall_rules_logic(project_id)


@handle_gcp_exceptions
def list_firewall_rules_logic(project_id: str):
    with compute_v1.FirewallsClient() as client:
        request = compute_v1.ListFirewallsRequest(project=project_id)
        firewall_rules = client.list(request)

        rules_as_dict = [
            json.loads(compute_v1.Firewall.to_json(rule)) for rule in firewall_rules
        ]

        return rules_as_dict


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

        vpc_rules_dict = [
            json.loads(compute_v1.Firewall.to_json(rule))
            for rule in firewall_rules
            if rule.network.endswith(f"/{vpc_name}")
        ]
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

        return json.loads(compute_v1.Firewall.to_json(firewall_rule))


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
