import unittest

from google.cloud import compute_v1

from gcp.compute.registry import RESOURCE_TYPES

WRITE_VERBS = (
    "insert",
    "delete",
    "patch",
    "update",
    "set_",
    "add_",
    "remove_",
    "resize",
    "start",
    "stop",
    "reset",
    "attach",
    "detach",
    "move",
    "test_iam_permissions",
    "wait",
)


class TestComputeRegistry(unittest.TestCase):
    def test_covers_compute_resource_types(self):
        self.assertLessEqual(
            {
                "instances",
                "disks",
                "firewalls",
                "networks",
                "subnetworks",
                "backend_services",
                "routers",
                "snapshots",
                "images",
                "firewall_policies",
                "global_frontend_settings_service",
            },
            set(RESOURCE_TYPES),
        )
        self.assertGreaterEqual(len(RESOURCE_TYPES), 100)

    def test_exposes_only_read_methods(self):
        for rt in RESOURCE_TYPES.values():
            client = getattr(compute_v1, rt.client_name)
            for name in rt.methods:
                with self.subTest(resource_type=rt.name, method=name):
                    self.assertFalse(any(verb in name for verb in WRITE_VERBS))
                    self.assertFalse(name.endswith("_unary"))
                    self.assertFalse(hasattr(client, f"{name}_unary"))

    def test_excludes_long_running_reads(self):
        self.assertNotIn("get_version", RESOURCE_TYPES["hosts"].methods)
        self.assertNotIn("get_health", RESOURCE_TYPES["reservation_slots"].methods)

    def test_records_required_and_optional_fields(self):
        get = RESOURCE_TYPES["instances"].methods["get"]
        self.assertEqual(set(get.required), {"instance", "project", "zone"})

        list_policies = RESOURCE_TYPES["firewall_policies"].methods["list"]
        self.assertNotIn("project", list_policies.required + list_policies.optional)
        self.assertIn("parent_id", list_policies.optional)


if __name__ == "__main__":
    unittest.main()
