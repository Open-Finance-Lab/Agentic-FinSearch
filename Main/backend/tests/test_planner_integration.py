"""
Integration test: verify the planner correctly constrains the agent.
Tests the full flow from message_list → planner → create_fin_agent.
"""
import pytest
from unittest.mock import patch


class TestPlannerIntegration:
    """Test that the planner integration in datascraper works end-to-end."""

    @pytest.fixture
    def mock_deps(self):
        """Mock external dependencies."""
        with patch("mcp_client.agent.get_global_mcp_manager", return_value=None), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "test", "GOOGLE_API_KEY": ""}):
            yield

    def test_summarize_uses_zero_tools(self, mock_deps):
        """When prescraped content exists and user asks to summarize, agent gets zero tools."""
        from planner.planner import Planner

        planner = Planner()
        plan = planner.plan(
            user_query="summarize this page",
            system_prompt=(
                "[CURRENT PAGE CONTENT - Already scraped, do NOT re-scrape]:\n"
                "- From https://example.com:\nEarnings report for Q4..."
            ),
            domain="example.com",
        )

        assert plan.skill_name == "summarize_page"
        assert plan.tools_allowed == []
        assert plan.max_turns == 1
        assert plan.instructions is not None
        assert "Earnings report for Q4" in plan.instructions

    def test_fallback_uses_all_tools(self, mock_deps):
        """Complex queries with no skill match get full tool access."""
        from planner.planner import Planner

        planner = Planner()
        plan = planner.plan(
            user_query="research biotech trends and navigate to FDA.gov",
            system_prompt=None,
            domain=None,
        )

        assert plan.skill_name == "web_research"
        assert plan.tools_allowed is None
        assert plan.max_turns == 10

    def test_stock_query_gets_filtered_tools(self, mock_deps):
        """Stock price queries get only fundamental tools."""
        from planner.planner import Planner

        planner = Planner()
        plan = planner.plan(
            user_query="what is the current price of TSLA?",
            system_prompt=None,
            domain="finance.yahoo.com",
        )

        assert plan.skill_name == "stock_fundamentals"
        assert set(plan.tools_allowed) == {"get_stock_info", "get_stock_financials", "get_stock_history", "get_earnings_info", "calculate"}
        assert plan.max_turns == 5
