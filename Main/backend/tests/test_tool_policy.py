"""Deny-by-default allow-list policy tests (Task 4).

Pure SimpleTestCase (no DB) so it is discovered by ``manage.py test tests``.
Covers BOTH enforcement sites:
  * exec layer  -> MCPClientManager.execute_tool raises PermissionError;
  * attach layer -> create_fin_agent strips denied AND non-allowed tools;
plus the policy unit functions and the skill-list invariants
(every skill declares a finite real-name list; no skill returns None).
"""
import asyncio
import types

from django.test import SimpleTestCase


class TestToolPolicyUnit(SimpleTestCase):
    """Unit tests for is_allowed / filter_to_allowed / DENY_ALWAYS."""

    def test_deny_always_has_14_filesystem_names(self):
        from mcp_client.tool_policy import DENY_ALWAYS
        self.assertEqual(len(DENY_ALWAYS), 14)
        for name in ("read_file", "write_file", "edit_file", "list_directory",
                     "directory_tree", "move_file", "list_allowed_directories"):
            self.assertIn(name, DENY_ALWAYS)

    def test_is_allowed_true_for_listed_non_denied(self):
        from mcp_client.tool_policy import is_allowed
        self.assertTrue(is_allowed("calculate", ["calculate", "scrape_url"]))

    def test_is_allowed_false_when_not_listed(self):
        from mcp_client.tool_policy import is_allowed
        self.assertFalse(is_allowed("get_stock_news", ["calculate"]))

    def test_is_allowed_false_for_deny_always_even_if_listed(self):
        from mcp_client.tool_policy import is_allowed
        self.assertFalse(is_allowed("write_file", ["write_file", "calculate"]))

    def test_filter_to_allowed_keeps_only_allowed(self):
        from mcp_client.tool_policy import filter_to_allowed
        tools = [types.SimpleNamespace(name=n)
                 for n in ("calculate", "scrape_url", "write_file", "get_holders")]
        kept = {t.name for t in
                filter_to_allowed(tools, ["calculate", "scrape_url", "write_file"])}
        self.assertEqual(kept, {"calculate", "scrape_url"})


class TestSkillAllowlists(SimpleTestCase):
    """Every skill must declare a finite list of REAL tool names; none None."""

    def test_no_skill_returns_none(self):
        from planner.skills.registry import SkillRegistry
        for skill in SkillRegistry().skills:
            allowed = skill.tools_allowed
            self.assertIsNotNone(allowed, f"{skill.name} returned None")
            self.assertIsInstance(allowed, list, f"{skill.name} not a list")

    def test_web_research_uses_real_readonly_catalog(self):
        from planner.skills.web_research import WebResearchSkill
        from planner.skills._catalog import READ_ONLY_DATA_TOOLS
        allowed = WebResearchSkill().tools_allowed
        self.assertEqual(allowed, list(READ_ONLY_DATA_TOOLS))
        self.assertEqual(len(allowed), 46)
        self.assertIn("get_recent_filings", allowed)
        self.assertIn("get_filing_content", allowed)
        self.assertIn("search_companies", allowed)
        self.assertNotIn("get_filing", allowed)       # fictional name
        self.assertNotIn("search_filings", allowed)   # core.md aspirational

    def test_web_research_excludes_all_filesystem_tools(self):
        from planner.skills.web_research import WebResearchSkill
        from mcp_client.tool_policy import DENY_ALWAYS
        allowed = set(WebResearchSkill().tools_allowed)
        self.assertEqual(allowed & DENY_ALWAYS, set())


class _FakeMCPManager:
    """Stand-in for the global MCP manager: synchronous (_loop is None) path."""
    _loop = None

    async def get_all_tools(self):
        from mcp import Tool as MCPTool
        empty = {"type": "object", "properties": {}}
        return [
            MCPTool(name="write_file", description="fs write", inputSchema=empty),
            MCPTool(name="get_stock_news", description="news", inputSchema=empty),
        ]


class TestAttachEnforcement(SimpleTestCase):
    """create_fin_agent strips denied AND non-allowed tools at attach time."""

    def _build(self, allowed):
        from unittest.mock import patch
        from mcp_client.agent import create_fin_agent

        async def run():
            async with create_fin_agent(
                model="gpt-4o-mini",
                allowed_tools=allowed,
                instructions_override="test",
            ) as agent:
                return {t.name for t in agent.tools}

        with patch.dict("os.environ",
                        {"OPENAI_API_KEY": "test", "GOOGLE_API_KEY": ""}, clear=False), \
             patch("mcp_client.agent.get_global_mcp_manager",
                   return_value=_FakeMCPManager()):
            return asyncio.run(run())

    def test_strips_denied_and_non_allowed(self):
        names = self._build(["scrape_url"])
        self.assertEqual(names, {"scrape_url"})
        self.assertNotIn("write_file", names)      # denied (DENY_ALWAYS)
        self.assertNotIn("get_stock_news", names)  # non-allowed

    def test_deny_always_belt_strips_filesystem_even_if_listed(self):
        names = self._build(["scrape_url", "write_file"])
        self.assertNotIn("write_file", names)
        self.assertEqual(names, {"scrape_url"})

    def test_none_means_deny_all(self):
        self.assertEqual(self._build(None), set())


class TestExecToolEnforcement(SimpleTestCase):
    """MCPClientManager.execute_tool refuses non-allow-listed MCP tools."""

    def _manager(self):
        from mcp_client.mcp_manager import MCPClientManager
        mgr = MCPClientManager(verbose=False)
        mgr.tools_map = {"write_file": "filesystem", "get_stock_info": "yahoo-finance"}
        mgr.sessions = {}
        return mgr

    def test_denied_name_raises_permission_error(self):
        mgr = self._manager()
        with self.assertRaises(PermissionError):
            asyncio.run(mgr.execute_tool("write_file", {}, ["get_stock_info"]))

    def test_deny_always_belt_raises_even_if_listed(self):
        mgr = self._manager()
        with self.assertRaises(PermissionError):
            asyncio.run(mgr.execute_tool("write_file", {}, ["write_file"]))

    def test_allowed_name_passes_allowlist_gate(self):
        # Allow-listed name is NOT blocked by the gate; it proceeds to the
        # session lookup, which raises RuntimeError (no live session) -- proving
        # the PermissionError gate did not fire for an allowed tool.
        mgr = self._manager()
        with self.assertRaises(RuntimeError):
            asyncio.run(mgr.execute_tool("get_stock_info", {}, ["get_stock_info"]))
