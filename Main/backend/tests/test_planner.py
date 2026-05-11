import pytest
from planner.planner import Planner
from planner.plan import ExecutionPlan


class TestPlanner:
    def setup_method(self):
        self.planner = Planner()

    def test_plan_returns_execution_plan(self):
        plan = self.planner.plan(
            user_query="summarize this page",
            system_prompt="[CURRENT PAGE CONTENT - Already scraped, do NOT re-scrape]:\n- From https://example.com:\nSome content here",
            domain="example.com",
        )
        assert isinstance(plan, ExecutionPlan)

    def test_summarize_plan_no_tools(self):
        plan = self.planner.plan(
            user_query="what does this article say?",
            system_prompt="[CURRENT PAGE CONTENT - Already scraped, do NOT re-scrape]:\n- From https://example.com:\nArticle about earnings...",
            domain="example.com",
        )
        assert plan.skill_name == "summarize_page"
        assert plan.tools_allowed == []
        assert plan.max_turns == 1
        assert plan.instructions is not None
        assert "Article about earnings" in plan.instructions

    def test_stock_price_plan(self):
        plan = self.planner.plan(
            user_query="what is AAPL stock price?",
            system_prompt=None,
            domain="finance.yahoo.com",
        )
        assert plan.skill_name == "stock_fundamentals"
        assert "get_stock_info" in plan.tools_allowed
        assert plan.max_turns == 5
        assert plan.instructions is None

    def test_fallback_plan(self):
        plan = self.planner.plan(
            user_query="find me biotech investment ideas",
            system_prompt=None,
            domain=None,
        )
        assert plan.skill_name == "web_research"
        assert plan.tools_allowed is None
        assert plan.max_turns == 10

    def test_prescraped_detection(self):
        plan = self.planner.plan(
            user_query="summarize this",
            system_prompt="[CURRENT PAGE CONTENT - Already scraped, do NOT re-scrape]:\n- From url:\nContent",
            domain=None,
        )
        assert plan.skill_name == "summarize_page"

    def test_no_prescraped_detection(self):
        plan = self.planner.plan(
            user_query="summarize this",
            system_prompt="Some other system prompt without page content",
            domain=None,
        )
        assert plan.skill_name == "web_research"

    def test_options_plan(self):
        plan = self.planner.plan(
            user_query="show me options volume for AVGO",
            system_prompt=None,
            domain="finance.yahoo.com",
        )
        assert plan.skill_name == "options_analysis"
        assert "get_options_summary" in plan.tools_allowed

    def test_earnings_plan(self):
        plan = self.planner.plan(
            user_query="when are MSFT earnings and what's the EPS estimate?",
            system_prompt=None,
            domain=None,
        )
        # stock_fundamentals now includes get_earnings_info and matches EPS,
        # so it may win over financial_statements for simple EPS queries
        assert plan.skill_name in {"stock_fundamentals", "financial_statements"}
        assert "get_earnings_info" in plan.tools_allowed

    def test_technical_analysis_plan(self):
        plan = self.planner.plan(
            user_query="what's the RSI for BTC?",
            system_prompt=None,
            domain=None,
        )
        assert plan.skill_name == "technical_analysis"


class TestPlannerEdgeCases:
    def setup_method(self):
        self.planner = Planner()

    def test_empty_query(self):
        plan = self.planner.plan(user_query="", system_prompt=None, domain=None)
        assert plan.skill_name == "web_research"  # Fallback

    def test_prescraped_but_data_query(self):
        """Pre-scraped content exists but user asks for stock data → use data skill, not summarize."""
        plan = self.planner.plan(
            user_query="what is the PE ratio?",
            system_prompt="[CURRENT PAGE CONTENT - Already scraped, do NOT re-scrape]:\n- From url:\nSome article",
            domain="finance.yahoo.com",
        )
        assert plan.skill_name == "stock_fundamentals"
        assert plan.instructions is None  # No override — use PromptBuilder

    def test_multiple_intents_highest_wins(self):
        """When query matches multiple skills, the highest-confidence one wins."""
        plan = self.planner.plan(
            user_query="what is AAPL RSI and earnings date?",
            system_prompt=None,
            domain=None,
        )
        # technical_analysis, financial_statements, and stock_fundamentals all match.
        # Any is acceptable, but it should not be web_research.
        assert plan.skill_name in {"technical_analysis", "financial_statements", "stock_fundamentals"}

    def test_none_system_prompt(self):
        plan = self.planner.plan(user_query="hello", system_prompt=None, domain=None)
        assert isinstance(plan, ExecutionPlan)

    def test_summarize_with_web_search_only(self):
        """Web search results (not page content) should NOT trigger SummarizePageSkill."""
        plan = self.planner.plan(
            user_query="summarize the search results",
            system_prompt="[WEB SEARCH RESULTS]:\n- From google.com: some results",
            domain=None,
        )
        # No [CURRENT PAGE CONTENT] marker → has_prescraped=False
        assert plan.skill_name == "web_research"
