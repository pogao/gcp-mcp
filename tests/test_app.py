import unittest

from fastmcp import Client

from app import mcp


class TestToolRegistration(unittest.IsolatedAsyncioTestCase):
    async def test_iam_tools_are_registered(self):
        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}

        self.assertLessEqual({"list_project_iam", "find_project_owners"}, names)

    async def test_compute_tools_are_registered(self):
        async with Client(mcp) as client:
            names = {tool.name for tool in await client.list_tools()}

        self.assertLessEqual(
            {
                "list_compute_resource_types",
                "list_compute_resources",
                "get_compute_resource",
                "call_compute_read_method",
                "unsafe_ssh_exposure",
            },
            names,
        )
