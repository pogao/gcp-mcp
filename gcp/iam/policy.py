from app import mcp
from gcp import utils
from google.cloud import resourcemanager_v3
from google.iam.v1 import iam_policy_pb2
from google.protobuf.json_format import MessageToDict


@mcp.tool()
def list_project_iam(project_id: str) -> dict:
    """
    Retrieves and lists the full Identity and Access Management (IAM)
    policy for a specified Google Cloud project.

    This function is useful for answering questions about who has
    what type of access to a specific project. Use this tool
    when a user asks "Who has access to project X?", "What are
    the permissions for project Y?", or "Show me the IAM policy
    for project Z".

    The IAM policy consists of a list of bindings that associate
    a set of members with a role.

    Args:
        project_id: The unique identifier for the Google Cloud project.

    Returns:
        A dictionary representing the IAM policy. This includes a
        list of bindings, where each binding specifies a role
        and the members (users, groups, service accounts)
        assigned to that role.
    """
    return list_project_iam_logic(project_id)


@utils.handle_gcp_exceptions
def list_project_iam_logic(project_id: str) -> dict:
    with resourcemanager_v3.ProjectsClient() as client:
        project = f"projects/{project_id}"
        request = iam_policy_pb2.GetIamPolicyRequest(resource=project)
        policy = client.get_iam_policy(request=request)

    return MessageToDict(policy)
