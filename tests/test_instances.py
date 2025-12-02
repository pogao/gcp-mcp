import unittest
from unittest.mock import MagicMock, patch
from google.api_core import exceptions

from gcp.compute.instances import (
    list_all_instances_in_project_logic as list_all_instances_in_project,
)


class TestGCPInstances(unittest.TestCase):
    def test_list_all_instances_in_project_success(self):
        """
        Tests the 'happy path' where the function successfully returns a
        list of VMs.
        """
        mock_instance_1 = MagicMock()
        mock_instance_1.name = "vm-1"
        mock_instance_1.status.__str__.return_value = "RUNNING"
        mock_instance_1.machine_type = (
            "projects/my-project/zones/us-central1-a/machineTypes/n1-standard-1"
        )

        mock_instance_2 = MagicMock()
        mock_instance_2.name = "vm-2"
        mock_instance_2.status.__str__.return_value = "STOPPED"
        mock_instance_2.machine_type = (
            "projects/my-project/zones/us-central1-a/machineTypes/e2-medium"
        )

        mock_pager_response = [mock_instance_1, mock_instance_2]

        with patch(
            "gcp.compute.instances.compute_v1.InstancesClient"
        ) as MockInstancesClient:
            mock_client_instance = MockInstancesClient.return_value
            mock_client_instance.list.return_value = mock_pager_response
            mock_client_instance.__enter__.return_value = mock_client_instance

            project_id = "test-project"
            zone = "test-zone"
            result = list_all_instances_in_project(project_id, zone)

            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 2)

            self.assertEqual(result[0]["name"], "vm-1")
            self.assertEqual(result[0]["status"], "RUNNING")
            self.assertEqual(result[0]["machine_type"], "n1-standard-1")

            self.assertEqual(result[1]["name"], "vm-2")
            self.assertEqual(result[1]["status"], "STOPPED")
            self.assertEqual(result[1]["machine_type"], "e2-medium")

            mock_client_instance.list.assert_called_once()

            call_args = mock_client_instance.list.call_args
            self.assertEqual(call_args.kwargs["request"].project, project_id)
            self.assertEqual(call_args.kwargs["request"].zone, zone)

    def test_list_all_instances_in_project_permission_denied(self):
        """
        Tests the 'failure path' where the API raises a PermissionDenied error.
        """

        with patch(
            "gcp.compute.instances.compute_v1.InstancesClient"
        ) as MockInstancesClient:
            mock_client_instance = MockInstancesClient.return_value
            mock_client_instance.list.side_effect = exceptions.PermissionDenied(
                "Test permission denied"
            )

            mock_client_instance.__enter__.return_value = mock_client_instance

            result = list_all_instances_in_project("test-project", "test-zone")

            self.assertIsInstance(result, list)
            self.assertEqual(result, [])

    def test_list_all_instances_in_project_not_found(self):
        """
        Tests the 'failure path' where the API raises a NotFound error.
        """

        with patch(
            "gcp.compute.instances.compute_v1.InstancesClient"
        ) as MockInstancesClient:
            mock_client_instance = MockInstancesClient.return_value
            mock_client_instance.list.side_effect = exceptions.NotFound(
                "Test not found"
            )

            mock_client_instance.__enter__.return_value = mock_client_instance

            result = list_all_instances_in_project("test-project", "test-zone")

            self.assertIsInstance(result, list)
            self.assertEqual(result, [])
