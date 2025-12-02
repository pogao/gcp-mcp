import unittest
from unittest.mock import MagicMock, patch
from google.api_core import exceptions

from gcp.iam.policy import list_project_iam_logic


class TestGCPIAMPolicy(unittest.TestCase):
    @patch("gcp.iam.policy.MessageToDict")
    @patch("gcp.iam.policy.resourcemanager_v3.ProjectsClient")
    def test_list_project_iam_logic_success(
        self, MockProjectsClient, MockMessageToDict
    ):
        """
        Tests the 'happy path' where the function successfully returns a dict.
        """
        mock_client_instance = MockProjectsClient.return_value
        mock_client_instance.__enter__.return_value = mock_client_instance
        mock_policy = MagicMock()
        mock_client_instance.get_iam_policy.return_value = mock_policy
        expected_dict = {"bindings": [{"role": "roles/owner", "members": ["user:test@example.com"]}]}
        MockMessageToDict.return_value = expected_dict

        project_id = "test-project"
        result = list_project_iam_logic(project_id)

        self.assertIsInstance(result, dict)
        self.assertEqual(result, expected_dict)
        mock_client_instance.get_iam_policy.assert_called_once()
        MockMessageToDict.assert_called_once_with(mock_policy)

    @patch("gcp.iam.policy.resourcemanager_v3.ProjectsClient")
    def test_list_project_iam_logic_permission_denied(self, MockProjectsClient):
        """
        Tests the 'failure path' where the API raises a PermissionDenied error.
        """
        mock_client_instance = MockProjectsClient.return_value
        mock_client_instance.__enter__.return_value = mock_client_instance
        mock_client_instance.get_iam_policy.side_effect = exceptions.PermissionDenied(
            "Test permission denied"
        )

        result = list_project_iam_logic("test-project")

        self.assertIsInstance(result, list)
        self.assertEqual(result, [])
