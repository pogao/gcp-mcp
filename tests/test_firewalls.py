import json
import unittest
from unittest.mock import MagicMock, patch

from fastmcp import Client
from google.cloud import compute_v1
from google.cloud.compute_v1.services.firewalls import pagers

from app import mcp


def rule(name, ports=None, protocol="tcp", source="0.0.0.0/0", **fields):
    return compute_v1.Firewall(
        name=name,
        network="projects/p/global/networks/default",
        direction="INGRESS",
        source_ranges=[source],
        allowed=[compute_v1.Allowed(I_p_protocol=protocol, ports=ports or [])],
        **fields,
    )


class TestUnsafeSshExposure(unittest.IsolatedAsyncioTestCase):
    @patch("gcp.compute.firewalls.compute_v1.FirewallsClient")
    async def test_flags_rules_that_open_ssh_to_the_internet(self, FirewallsClient):
        client = FirewallsClient.return_value
        client.__enter__.return_value = client
        client.list.return_value = pagers.ListPager(
            method=MagicMock(),
            request=compute_v1.ListFirewallsRequest(),
            response=compute_v1.FirewallList(
                items=[
                    rule("ssh", ["22"]),
                    rule("range", ["0-1024"]),
                    rule("all-protocols", protocol="all"),
                    rule("ipv6", ["22"], source="::/0"),
                    rule("http", ["80", "443"]),
                    rule("internal", ["22"], source="10.0.0.0/8"),
                    rule("disabled", ["22"], disabled=True),
                    rule("udp", ["22"], protocol="udp"),
                ]
            ),
        )

        async with Client(mcp) as mcp_client:
            result = await mcp_client.call_tool(
                "unsafe_ssh_exposure", {"project_id": "p"}
            )

        self.assertEqual(
            [r["name"] for r in json.loads(result.content[0].text)],
            ["ssh", "range", "all-protocols", "ipv6"],
        )
        self.assertEqual(json.loads(result.content[0].text)[0]["network"], "default")
