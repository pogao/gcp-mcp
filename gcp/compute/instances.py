import json
from app import mcp
from google.cloud import compute_v1
from gcp.utils import handle_gcp_exceptions


@mcp.tool()
def list_gcp_instances(project_id: str, zone: str):
    """
    Lists all Google Compute Engine VM instances in a zone of the
    specified project ID. The list of VMs is returned in a Python
    list that can be iterated over.

    Args:
    * project_id: the project ID where the VMs are. Must be a string.
    * zone: the zone you want to list. Must be in the format
    'us-central1-a' and be a string.
    """
    return _list_all_instances_in_project_logic(project_id, zone)


@handle_gcp_exceptions
def _list_all_instances_in_project_logic(project_id: str, zone: str):
    results = []
    with compute_v1.InstancesClient() as instance_client:
        request = compute_v1.ListInstancesRequest(
            project=project_id, zone=zone)

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
def describe_gcp_instance(instance_name: str, project_id: str, zone: str):
    """
    Use this to get details about a specific Google Compute Engine instance.
    Find out the name of the disk, Operating System, VPC network, service
    account, CPU platform, shielded instance configuraiton, labels, and other
    parameters. The Google Compute instance metadata is returned in JSON
    format.

    Args:
    * instance_name: the name of the Google Compute Instance from which you
    want the details about. Must be a string.
    * project_id: the project ID of the project where the instance exists.
    Must be a string.
    * zone: the zone on which the Google Compute Instance was created.
    Must be a string.
    """
    return describe_gcp_instance_logic(
        instance_name=instance_name, project_id=project_id, zone=zone
    )


@handle_gcp_exceptions
def describe_gcp_instance_logic(instance_name: str, project_id: str, zone: str):
    with compute_v1.InstancesClient() as client:
        request = compute_v1.GetInstanceRequest(
            project=project_id, instance=instance_name, zone=zone
        )

        instance_details = client.get(request=request)
        instance_details_json = compute_v1.Instance.to_json(instance_details)

        return json.loads(instance_details_json)
