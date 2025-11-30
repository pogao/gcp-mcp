from app import mcp
from gcp.utils import handle_gcp_exceptions
from google.cloud import storage


@mcp.tool()
def list_gcs_buckets(project_id: str) -> list:
    """
    Retrieves a comprehensive list of all Google Cloud Storage (GCS) buckets
     within a specified Google Cloud project.
     Use this tool for broad queries about GCS bucket configurations or
     when you need to see all buckets at once.

     Args:
         project_id: The unique identifier for the Google Cloud project.

     Returns:
         A list of dictionaries, where each dictionary represents a complete
         GCS bucket.
    """
    return list_gcs_buckets_logic(project_id)


@handle_gcp_exceptions
def list_gcs_buckets_logic(project_id: str) -> list:
    results = []
    # Storage client can't be used with "with".
    client = storage.Client(project=project_id)
    buckets = client.list_buckets()
    for bucket in buckets:
        bucket_dict = {
            "name": bucket.name,
            "location": bucket.location,
            "storage_class": bucket.storage_class,
            "created": bucket.time_created,
            "labels": bucket.labels,
            "self_link": bucket.self_link,
        }
        results.append(bucket_dict)

    return results


@mcp.tool()
def describe_gcs_bucket(project_id: str, bucket_name: str) -> dict:
    """
    Retrieves detailed metadata about a specific Google Cloud Storage (GCS)
    bucket. Use this tool to get comprehensive information about a bucket's
    configuration, including its location, storage class, creation time,
    versioning status, encryption settings, lifecycle rules, labels,
    and self-link.

    Args:
        project_id: The unique identifier for the Google Cloud project.
        bucket_name: The name of the GCS bucket to describe.

    Returns:
        A dictionary containing the full metadata of the specified GCS bucket.
    """
    return describe_gcs_bucket_logic(project_id, bucket_name)


@handle_gcp_exceptions
def describe_gcs_bucket_logic(project_id: str, bucket_name: str) -> dict:
    client = storage.Client(project=project_id)
    bucket = client.get_bucket(bucket_name)

    return bucket._properties


@mcp.tool()
def is_ubla_enabled_in_bucket(project_id: str, bucket_name: str):
    """
    Checks whether the specified GCS bucket has Uniform Bucket Level enabled.

    Args:
        project_id: The unique identifier for the Google Cloud project.
        bucket_name: The name of the GCS bucket to describe.

    Returns:
        A boolean. True if UBLA is enabled, False if it isn't.
    """
    return is_ubla_enabled_in_bucket_logic(project_id, bucket_name)


@handle_gcp_exceptions
def is_ubla_enabled_in_bucket_logic(project_id: str, bucket_name: str) -> bool:
    client = storage.Client(project=project_id)
    bucket = client.get_bucket(bucket_name)
    return bucket.iam_configuration.uniform_bucket_level_access_enabled


@mcp.tool()
def is_bucket_public(project_id: str, bucket_name: str):
    """
    Checks all IAM bindings of a GCS bucket looking for explicit
    bindings of any roles to principals "allUsers" or
    "allAuthenticatedUsers".

    Args:
        project_id: The unique identifier for the Google Cloud project.
        bucket_name: The name of the GCS bucket to describe.
    """
    return is_bucket_public_logic(project_id, bucket_name)


@handle_gcp_exceptions
def is_bucket_public_logic(project_id: str, bucket_name: str) -> bool:
    client = storage.Client(project=project_id)
    bucket = client.get_bucket(bucket_name)
    bucket_iam_policy = bucket.get_iam_policy()
    for binding in bucket_iam_policy.bindings:
        if (
            "allUsers" in binding["members"]
            or "allAuthenticatedUsers" in binding["members"]
        ):
            return True

    return False
