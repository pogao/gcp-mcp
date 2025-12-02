import unittest
from unittest.mock import MagicMock, patch
from google.api_core import exceptions

from gcp.storage.buckets import (
    list_gcs_buckets_logic,
    describe_gcs_bucket_logic,
    is_ubla_enabled_in_bucket_logic,
    is_bucket_public_logic,
)


class TestGCPStorageBuckets(unittest.TestCase):
    @patch("gcp.storage.buckets.storage.Client")
    def test_list_gcs_buckets_logic_success(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_bucket_1 = MagicMock()
        mock_bucket_1.name = "bucket-1"
        mock_bucket_1.location = "US"
        mock_bucket_1.storage_class = "STANDARD"
        mock_bucket_1.time_created = "2025-12-01T00:00:00Z"
        mock_bucket_1.labels = {}
        mock_bucket_1.self_link = "https://storage.googleapis.com/storage/v1/b/bucket-1"

        mock_client_instance.list_buckets.return_value = [mock_bucket_1]

        project_id = "test-project"
        result = list_gcs_buckets_logic(project_id)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "bucket-1")
        MockStorageClient.assert_called_with(project=project_id)

    @patch("gcp.storage.buckets.storage.Client")
    def test_list_gcs_buckets_logic_permission_denied(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_client_instance.list_buckets.side_effect = exceptions.PermissionDenied(
            "Test permission denied"
        )
        result = list_gcs_buckets_logic("test-project")
        self.assertEqual(result, [])

    @patch("gcp.storage.buckets.storage.Client")
    def test_describe_gcs_bucket_logic_success(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_bucket = MagicMock()
        mock_bucket._properties = {"name": "bucket-1", "location": "US"}
        mock_client_instance.get_bucket.return_value = mock_bucket

        result = describe_gcs_bucket_logic("test-project", "bucket-1")

        self.assertEqual(result, {"name": "bucket-1", "location": "US"})
        mock_client_instance.get_bucket.assert_called_with("bucket-1")

    @patch("gcp.storage.buckets.storage.Client")
    def test_describe_gcs_bucket_logic_permission_denied(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_client_instance.get_bucket.side_effect = exceptions.PermissionDenied(
            "Test permission denied"
        )
        result = describe_gcs_bucket_logic("test-project", "bucket-1")
        self.assertEqual(result, {})

    @patch("gcp.storage.buckets.storage.Client")
    def test_is_ubla_enabled_in_bucket_logic_enabled(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_bucket = MagicMock()
        mock_bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        mock_client_instance.get_bucket.return_value = mock_bucket

        result = is_ubla_enabled_in_bucket_logic("test-project", "bucket-1")
        self.assertTrue(result)

    @patch("gcp.storage.buckets.storage.Client")
    def test_is_ubla_enabled_in_bucket_logic_disabled(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_bucket = MagicMock()
        mock_bucket.iam_configuration.uniform_bucket_level_access_enabled = False
        mock_client_instance.get_bucket.return_value = mock_bucket

        result = is_ubla_enabled_in_bucket_logic("test-project", "bucket-1")
        self.assertFalse(result)

    @patch("gcp.storage.buckets.storage.Client")
    def test_is_ubla_enabled_in_bucket_logic_permission_denied(
        self, MockStorageClient
    ):
        mock_client_instance = MockStorageClient.return_value
        mock_client_instance.get_bucket.side_effect = exceptions.PermissionDenied(
            "Test permission denied"
        )
        result = is_ubla_enabled_in_bucket_logic("test-project", "bucket-1")
        self.assertEqual(result, {})

    @patch("gcp.storage.buckets.storage.Client")
    def test_is_bucket_public_logic_all_users(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_bucket = MagicMock()
        mock_policy = MagicMock()
        mock_policy.bindings = [{"members": ["allUsers"]}]
        mock_bucket.get_iam_policy.return_value = mock_policy
        mock_client_instance.get_bucket.return_value = mock_bucket

        result = is_bucket_public_logic("test-project", "bucket-1")
        self.assertTrue(result)

    @patch("gcp.storage.buckets.storage.Client")
    def test_is_bucket_public_logic_all_authenticated_users(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_bucket = MagicMock()
        mock_policy = MagicMock()
        mock_policy.bindings = [{"members": ["allAuthenticatedUsers"]}]
        mock_bucket.get_iam_policy.return_value = mock_policy
        mock_client_instance.get_bucket.return_value = mock_bucket

        result = is_bucket_public_logic("test-project", "bucket-1")
        self.assertTrue(result)

    @patch("gcp.storage.buckets.storage.Client")
    def test_is_bucket_public_logic_not_public(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_bucket = MagicMock()
        mock_policy = MagicMock()
        mock_policy.bindings = [{"members": ["user:test@example.com"]}]
        mock_bucket.get_iam_policy.return_value = mock_policy
        mock_client_instance.get_bucket.return_value = mock_bucket

        result = is_bucket_public_logic("test-project", "bucket-1")
        self.assertFalse(result)

    @patch("gcp.storage.buckets.storage.Client")
    def test_is_bucket_public_logic_permission_denied(self, MockStorageClient):
        mock_client_instance = MockStorageClient.return_value
        mock_client_instance.get_bucket.side_effect = exceptions.PermissionDenied(
            "Test permission denied"
        )
        result = is_bucket_public_logic("test-project", "bucket-1")
        self.assertEqual(result, {})
