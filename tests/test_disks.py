import unittest
from unittest.mock import MagicMock, patch
from google.api_core import exceptions

from gcp.compute.disks import (
    list_gcp_disks_per_zone_logic as list_gcp_disks_per_zone,
    describe_gcp_disk_logic as describe_gcp_disk,
)


class TestGCPDisksList(unittest.TestCase):
    def test_list_gcp_disks_per_zone_success(self):
        """
        Tests the 'happy path' where the function successfully returns a
        list of persistent disks.
        """
        mock_disk_1 = MagicMock()
        mock_disk_1.name = "disk-1"
        mock_disk_1.status.__str__.return_value = "READY"
        mock_disk_1.size_gb = 100
        mock_disk_1.type_ = (
            "projects/my-project/zones/us-central1-a/diskTypes/pd-ssd"
        )
        mock_disk_1.users = [
            "projects/my-project/zones/us-central1-a/instances/vm-1"
        ]

        mock_disk_2 = MagicMock()
        mock_disk_2.name = "disk-2"
        mock_disk_2.status.__str__.return_value = "CREATING"
        mock_disk_2.size_gb = 50
        mock_disk_2.type_ = (
            "projects/my-project/zones/us-central1-a/diskTypes/pd-standard"
        )
        mock_disk_2.users = []

        mock_pager_response = [mock_disk_1, mock_disk_2]

        with patch(
            "gcp.compute.disks.compute_v1.DisksClient"
        ) as MockDisksClient:
            mock_client_instance = MockDisksClient.return_value
            mock_client_instance.list.return_value = mock_pager_response
            mock_client_instance.__enter__.return_value = mock_client_instance

            project_id = "test-project"
            zone = "us-central1-a"
            result = list_gcp_disks_per_zone(project_id, zone)

            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 2)

            self.assertEqual(result[0]["name"], "disk-1")
            self.assertEqual(result[0]["status"], "READY")
            self.assertEqual(result[0]["size_gb"], 100)
            self.assertEqual(result[0]["type"], "pd-ssd")
            self.assertEqual(result[0]["users"], ["vm-1"])

            self.assertEqual(result[1]["name"], "disk-2")
            self.assertEqual(result[1]["status"], "CREATING")
            self.assertEqual(result[1]["size_gb"], 50)
            self.assertEqual(result[1]["type"], "pd-standard")
            self.assertEqual(result[1]["users"], [])

            mock_client_instance.list.assert_called_once()

            call_args = mock_client_instance.list.call_args
            self.assertEqual(call_args.kwargs["request"].project, project_id)
            self.assertEqual(call_args.kwargs["request"].zone, zone)

    def test_list_gcp_disks_per_zone_empty(self):
        """
        Tests that an empty zone returns an empty list rather than erroring.
        """
        with patch(
            "gcp.compute.disks.compute_v1.DisksClient"
        ) as MockDisksClient:
            mock_client_instance = MockDisksClient.return_value
            mock_client_instance.list.return_value = []
            mock_client_instance.__enter__.return_value = mock_client_instance

            result = list_gcp_disks_per_zone("test-project", "us-central1-a")

            self.assertEqual(result, [])

    def test_list_gcp_disks_per_zone_permission_denied(self):
        """
        Tests the 'failure path' where the API raises a PermissionDenied error.
        """
        with patch(
            "gcp.compute.disks.compute_v1.DisksClient"
        ) as MockDisksClient:
            mock_client_instance = MockDisksClient.return_value
            mock_client_instance.list.side_effect = exceptions.PermissionDenied(
                "Test permission denied"
            )
            mock_client_instance.__enter__.return_value = mock_client_instance

            result = list_gcp_disks_per_zone("test-project", "us-central1-a")

            self.assertIsInstance(result, list)
            self.assertEqual(result, [])

    def test_list_gcp_disks_per_zone_not_found(self):
        """
        Tests the 'failure path' where the API raises a NotFound error.
        """
        with patch(
            "gcp.compute.disks.compute_v1.DisksClient"
        ) as MockDisksClient:
            mock_client_instance = MockDisksClient.return_value
            mock_client_instance.list.side_effect = exceptions.NotFound(
                "Test not found"
            )
            mock_client_instance.__enter__.return_value = mock_client_instance

            result = list_gcp_disks_per_zone("test-project", "us-central1-a")

            self.assertIsInstance(result, list)
            self.assertEqual(result, [])


class TestGCPDisksDescribe(unittest.TestCase):
    def test_describe_gcp_disk_success(self):
        """
        Tests the 'happy path' where a single disk's full metadata is
        returned as a dict.
        """
        with patch(
            "gcp.compute.disks.compute_v1.DisksClient"
        ) as MockDisksClient, patch(
            "gcp.compute.disks.compute_v1.Disk.to_json"
        ) as mock_to_json:
            mock_client_instance = MockDisksClient.return_value
            mock_client_instance.__enter__.return_value = mock_client_instance
            mock_client_instance.get.return_value = MagicMock()
            mock_to_json.return_value = (
                '{"name": "disk-1", "sizeGb": "100", "status": "READY"}'
            )

            project_id = "test-project"
            zone = "us-central1-a"
            disk_name = "disk-1"
            result = describe_gcp_disk(
                disk_name=disk_name, project_id=project_id, zone=zone
            )

            self.assertIsInstance(result, dict)
            self.assertEqual(result["name"], "disk-1")
            self.assertEqual(result["sizeGb"], "100")
            self.assertEqual(result["status"], "READY")

            mock_client_instance.get.assert_called_once()
            call_args = mock_client_instance.get.call_args
            self.assertEqual(call_args.kwargs["request"].project, project_id)
            self.assertEqual(call_args.kwargs["request"].zone, zone)
            self.assertEqual(call_args.kwargs["request"].disk, disk_name)

    def test_describe_gcp_disk_not_found(self):
        """
        Tests the 'failure path' where the disk does not exist; the decorator
        returns an empty dict for non-list functions.
        """
        with patch(
            "gcp.compute.disks.compute_v1.DisksClient"
        ) as MockDisksClient:
            mock_client_instance = MockDisksClient.return_value
            mock_client_instance.__enter__.return_value = mock_client_instance
            mock_client_instance.get.side_effect = exceptions.NotFound(
                "Test not found"
            )

            result = describe_gcp_disk(
                disk_name="missing-disk",
                project_id="test-project",
                zone="us-central1-a",
            )

            self.assertIsInstance(result, dict)
            self.assertEqual(result, {})

    def test_describe_gcp_disk_permission_denied(self):
        """
        Tests the 'failure path' where the API raises a PermissionDenied error.
        """
        with patch(
            "gcp.compute.disks.compute_v1.DisksClient"
        ) as MockDisksClient:
            mock_client_instance = MockDisksClient.return_value
            mock_client_instance.__enter__.return_value = mock_client_instance
            mock_client_instance.get.side_effect = exceptions.PermissionDenied(
                "Test permission denied"
            )

            result = describe_gcp_disk(
                disk_name="disk-1",
                project_id="test-project",
                zone="us-central1-a",
            )

            self.assertIsInstance(result, dict)
            self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
