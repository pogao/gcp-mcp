import json
import unittest
from unittest.mock import MagicMock, patch

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.api_core import exceptions
from google.cloud import compute_v1
from google.cloud.compute_v1.services.instances import pagers

from app import mcp


def instances_pager(*instances):
    return pagers.ListPager(
        method=MagicMock(),
        request=compute_v1.ListInstancesRequest(),
        response=compute_v1.InstanceList(items=list(instances)),
    )


def payload(result):
    return json.loads(result.content[0].text)


def mock_client(client_class):
    client = client_class.return_value
    client.__enter__.return_value = client
    return client


class TestListComputeResources(unittest.IsolatedAsyncioTestCase):
    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_list_with_zone_lists_that_zone(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.list.return_value = instances_pager(
            compute_v1.Instance(name="vm-1", status="RUNNING"),
            compute_v1.Instance(name="vm-2", status="TERMINATED"),
        )

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "list_compute_resources",
                {"resource_type": "instances", "project_id": "p", "zone": "us-central1-a"},
            )

        request = client.list.call_args.kwargs["request"]
        self.assertEqual((request.project, request.zone), ("p", "us-central1-a"))
        client.aggregated_list.assert_not_called()
        self.assertEqual(
            [(i["name"], i["status"]) for i in payload(result)],
            [("vm-1", "RUNNING"), ("vm-2", "TERMINATED")],
        )

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_list_without_zone_spans_all_zones(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.aggregated_list.return_value = pagers.AggregatedListPager(
            method=MagicMock(),
            request=compute_v1.AggregatedListInstancesRequest(),
            response=compute_v1.InstanceAggregatedList(
                items={
                    "zones/us-central1-a": compute_v1.InstancesScopedList(
                        instances=[compute_v1.Instance(name="vm-a")]
                    ),
                    "zones/europe-west1-b": compute_v1.InstancesScopedList(
                        instances=[compute_v1.Instance(name="vm-b")]
                    ),
                    "zones/asia-east1-a": compute_v1.InstancesScopedList(
                        warning=compute_v1.Warning(code="NO_RESULTS_ON_PAGE")
                    ),
                }
            ),
        )

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "list_compute_resources",
                {"resource_type": "instances", "project_id": "p"},
            )

        self.assertEqual(client.aggregated_list.call_args.kwargs["request"].project, "p")
        client.list.assert_not_called()
        self.assertCountEqual(
            [(i["name"], i["scope"]) for i in payload(result)],
            [("vm-a", "zones/us-central1-a"), ("vm-b", "zones/europe-west1-b")],
        )

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_max_results_caps_items_and_page_size(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.list.return_value = instances_pager(
            *(compute_v1.Instance(name=f"vm-{i}") for i in range(5))
        )

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "list_compute_resources",
                {
                    "resource_type": "instances",
                    "project_id": "p",
                    "zone": "us-central1-a",
                    "max_results": 2,
                },
            )

        self.assertEqual([i["name"] for i in payload(result)], ["vm-0", "vm-1"])
        self.assertEqual(client.list.call_args.kwargs["request"].max_results, 2)

    @patch("google.cloud.compute_v1.FirewallsClient")
    async def test_list_returns_every_firewall_rule(self, FirewallsClient):
        client = mock_client(FirewallsClient)
        client.list.return_value = [
            compute_v1.Firewall(name="allow-ssh"),
            compute_v1.Firewall(name="allow-http"),
        ]

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "list_compute_resources",
                {"resource_type": "firewalls", "project_id": "p"},
            )

        self.assertEqual([r["name"] for r in payload(result)], ["allow-ssh", "allow-http"])

    @patch("google.cloud.compute_v1.FirewallsClient")
    async def test_list_without_required_project_is_a_tool_error(self, FirewallsClient):
        client = mock_client(FirewallsClient)

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "list_compute_resources", {"resource_type": "firewalls"}
                )

        client.list.assert_not_called()

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_permission_denied_is_a_tool_error(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.aggregated_list.side_effect = exceptions.PermissionDenied("denied")

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "list_compute_resources",
                    {"resource_type": "instances", "project_id": "p"},
                )

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_bad_filter_is_a_tool_error(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.aggregated_list.side_effect = exceptions.BadRequest("Invalid filter")

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "list_compute_resources",
                    {"resource_type": "instances", "project_id": "p", "filter": "("},
                )


class TestGetComputeResource(unittest.IsolatedAsyncioTestCase):
    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_get_instance_sends_instance_name(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.get.return_value = compute_v1.Instance(
            name="vm-1",
            status="RUNNING",
            machine_type="zones/us-central1-a/machineTypes/e2-medium",
        )

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "get_compute_resource",
                {
                    "resource_type": "instances",
                    "name": "vm-1",
                    "project_id": "p",
                    "zone": "us-central1-a",
                },
            )

        request = client.get.call_args.kwargs["request"]
        self.assertEqual(
            (request.instance, request.project, request.zone),
            ("vm-1", "p", "us-central1-a"),
        )
        self.assertEqual(payload(result)["name"], "vm-1")
        self.assertEqual(payload(result)["status"], "RUNNING")
        self.assertEqual(
            payload(result)["machineType"], "zones/us-central1-a/machineTypes/e2-medium"
        )

    @patch("google.cloud.compute_v1.FirewallsClient")
    async def test_get_firewall_sends_firewall_name(self, FirewallsClient):
        client = mock_client(FirewallsClient)
        client.get.return_value = compute_v1.Firewall(
            name="allow-ssh", source_ranges=["0.0.0.0/0"]
        )

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "get_compute_resource",
                {"resource_type": "firewalls", "name": "allow-ssh", "project_id": "p"},
            )

        request = client.get.call_args.kwargs["request"]
        self.assertEqual((request.firewall, request.project), ("allow-ssh", "p"))
        self.assertEqual(payload(result)["sourceRanges"], ["0.0.0.0/0"])

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_get_without_zone_is_a_tool_error(self, InstancesClient):
        client = mock_client(InstancesClient)

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "get_compute_resource",
                    {"resource_type": "instances", "name": "vm-1", "project_id": "p"},
                )

        client.get.assert_not_called()

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_not_found_is_a_tool_error(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.get.side_effect = exceptions.NotFound("no such instance")

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "get_compute_resource",
                    {
                        "resource_type": "instances",
                        "name": "vm-1",
                        "project_id": "p",
                        "zone": "us-central1-a",
                    },
                )


class TestCallComputeReadMethod(unittest.IsolatedAsyncioTestCase):
    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_calls_a_read_method(self, InstancesClient):
        client = mock_client(InstancesClient)
        client.get_serial_port_output.return_value = compute_v1.SerialPortOutput(
            contents="booted"
        )

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "call_compute_read_method",
                {
                    "resource_type": "instances",
                    "method": "get_serial_port_output",
                    "params": {"project": "p", "zone": "z", "instance": "vm-1"},
                },
            )

        self.assertEqual(
            client.get_serial_port_output.call_args.kwargs["request"].instance, "vm-1"
        )
        self.assertEqual(payload(result)["contents"], "booted")

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_write_method_is_refused(self, InstancesClient):
        client = mock_client(InstancesClient)

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "call_compute_read_method",
                    {
                        "resource_type": "instances",
                        "method": "delete",
                        "params": {"project": "p", "zone": "z", "instance": "vm-1"},
                    },
                )

        client.delete.assert_not_called()
        client.delete_unary.assert_not_called()

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_unknown_method_is_a_tool_error(self, InstancesClient):
        mock_client(InstancesClient)

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "call_compute_read_method",
                    {"resource_type": "instances", "method": "describe", "params": {}},
                )

    @patch("google.cloud.compute_v1.InstancesClient")
    async def test_unknown_param_is_a_tool_error(self, InstancesClient):
        client = mock_client(InstancesClient)

        async with Client(mcp) as mcp_client:
            with self.assertRaises(ToolError):
                await mcp_client.call_tool(
                    "call_compute_read_method",
                    {
                        "resource_type": "instances",
                        "method": "get",
                        "params": {"project": "p", "zone": "z", "name": "vm-1"},
                    },
                )

        client.get.assert_not_called()


class TestListComputeResourceTypes(unittest.IsolatedAsyncioTestCase):
    async def test_lists_types_and_describes_one(self):
        async with Client(mcp) as mcp_client:
            all_types = await mcp_client.call_tool("list_compute_resource_types", {})
            instances = await mcp_client.call_tool(
                "list_compute_resource_types", {"resource_type": "instances"}
            )

        by_type = {t["resource_type"]: t["methods"] for t in payload(all_types)}
        self.assertIn("get_effective_firewalls", by_type["instances"])
        self.assertNotIn("delete", by_type["instances"])
        get = payload(instances)["methods"]["get"]
        self.assertCountEqual(get["required"], ["instance", "project", "zone"])


if __name__ == "__main__":
    unittest.main()
