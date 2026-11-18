"""The MCP server over stdio, called the way any MCP client calls it."""
from __future__ import annotations

import os
import sys
import unittest

from mcp import Client, StdioServerParameters

from browser_agent.config import PROJECT_ROOT

from .support import running_portal


class McpServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_lists_four_tools_and_answers_a_read(self) -> None:
        with running_portal("v1") as portal:
            env = dict(os.environ) | {"PORTAL_URL": portal.url, "AGENT_OUT": str(portal.out), "APPROVAL_WEB": "0",
                                      "LLM_MODE": "off"}
            params = StdioServerParameters(command=sys.executable, args=["-m", "browser_agent", "mcp"], env=env,
                                           cwd=str(PROJECT_ROOT))
            async with Client(params) as client:
                tools = {t.name: t for t in (await client.list_tools()).tools}
                self.assertEqual(set(tools), {"search_order", "check_stock", "create_sales_order_draft",
                                              "download_invoice"})
                self.assertIn("order", tools["create_sales_order_draft"].input_schema["properties"])
                result = await client.call_tool("check_stock", {"skus": ["3.01.0019"]})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["items"][0]["available"], 21.0)
                bad = await client.call_tool("check_stock", {"skus": []})
                self.assertTrue(bad.is_error)


if __name__ == "__main__":
    unittest.main()
