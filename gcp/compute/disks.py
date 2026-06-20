import json
from app import mcp
from google.cloud import compute_v1
from gcp.utils import handle_gcp_exceptions


@mcp.tool()
def list_gcp_disks_per_zone(project_id: str, zone: str) -> list:
    """
    Lists all Google Compute Engine persistent disks in a zone of the
    specified project ID. The list of disks is returned in a Python
    list that can be iterated over.

    Args:
    * project_id: the project ID where the disks are. Must be a string.
    * zone: the zone you want to list. Must be in the format
    'us-central1-a' and be a string.
    """
    return list_gcp_disks_per_zone_logic(project_id, zone)


@handle_gcp_exceptions
def list_gcp_disks_per_zone_logic(project_id: str, zone: str) -> list:
    results = []
    with compute_v1.DisksClient() as disk_client:
        request = compute_v1.ListDisksRequest(project=project_id, zone=zone)

        disk_list = disk_client.list(request=request)

        for disk in disk_list:
            disk_data = {
                "name": disk.name,
                "status": str(disk.status),
                "size_gb": disk.size_gb,
                "type": disk.type_.split("/")[-1],
                "users": [user.split("/")[-1] for user in disk.users],
            }

            results.append(disk_data)

        return results


@mcp.tool()
def describe_gcp_disk(disk_name: str, project_id: str, zone: str) -> dict:
    """
    Fetches detailed metadata about a single Google Compute Engine
    persistent disk. Use this to find information like size, type,
    source image, snapshot schedule, encryption, and which instances
    are using the disk.

    Args:
        disk_name: The name of the persistent disk to describe.
        project_id: The Google Cloud project where the disk resides.
        zone: The zone where the disk is located, e.g., 'us-central1-a'.

    Returns:
        A dict containing the full metadata of the specified disk.
    """
    return describe_gcp_disk_logic(
        disk_name=disk_name, project_id=project_id, zone=zone
    )


@handle_gcp_exceptions
def describe_gcp_disk_logic(disk_name: str, project_id: str, zone: str) -> dict:
    with compute_v1.DisksClient() as client:
        request = compute_v1.GetDiskRequest(
            project=project_id, disk=disk_name, zone=zone
        )

        disk_details = client.get(request=request)
        disk_details_json = compute_v1.Disk.to_json(disk_details)

        return json.loads(disk_details_json)
