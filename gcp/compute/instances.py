import json
from app import mcp
from google.cloud import compute_v1
from gcp.utils import handle_gcp_exceptions


@mcp.tool()
def list_gcp_instances(project_id: str, zone: str) -> list:
    """
    Lists all Google Compute Engine VM instances in a zone of the
    specified project ID. The list of VMs is returned in a Python
    list that can be iterated over.

    Args:
    * project_id: the project ID where the VMs are. Must be a string.
    * zone: the zone you want to list. Must be in the format
    'us-central1-a' and be a string.
    """
    return list_all_instances_in_project_logic(project_id, zone)


@handle_gcp_exceptions
def list_all_instances_in_project_logic(project_id: str, zone: str) -> list:
    results = []
    with compute_v1.InstancesClient() as instance_client:
        request = compute_v1.ListInstancesRequest(project=project_id, zone=zone)

        instance_list = instance_client.list(request=request)

        for instance in instance_list:
            vm_data = {
                "name": instance.name,
                "status": str(instance.status),
                "machine_type": instance.machine_type.split("/")[-1],
            }

            results.append(vm_data)

        return results


@mcp.tool()
def describe_gcp_instance(instance_name: str, project_id: str, zone: str) -> dict:
    """
    Fetches detailed metadata about a single Google Compute Engine (GCE) VM
    instance.
    Use this tool to find comprehensive information like network
    interfaces (IP addresses), disk details, machine type, status, labels,
    and service accounts associated with a specific instance.

    Args:
        instance_name: The name of the GCE instance to describe.
        project_id: The unique identifier for the Google Cloud project
        where the instance resides.
        zone: The zone where the instance is located, e.g., 'us-central1-a'.

    Returns:
        A dict containing the full metadata of the specified VM instance.
    """
    return describe_gcp_instance_logic(
        instance_name=instance_name, project_id=project_id, zone=zone
    )


@handle_gcp_exceptions
def describe_gcp_instance_logic(instance_name: str, project_id: str, zone: str) -> dict:
    with compute_v1.InstancesClient() as client:
        request = compute_v1.GetInstanceRequest(
            project=project_id, instance=instance_name, zone=zone
        )

        instance_details = client.get(request=request)
        instance_details_json = compute_v1.Instance.to_json(instance_details)

        return json.loads(instance_details_json)
