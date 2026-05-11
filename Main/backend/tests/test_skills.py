import pytest
from planner.plan import ExecutionPlan
from planner.skills.base import BaseSkill


class TestExecutionPlan:
    def test_creation_with_defaults(self):
        plan = ExecutionPlan(skill_name="test")
        assert plan.skill_name == "test"
        assert plan.tools_allowed is None
        assert plan.max_turns == 10
        assert plan.instructions is None

    def test_zero_tool_plan(self):
        plan = ExecutionPlan(
            skill_name="summarize_page",
            tools_allowed=[],
            max_turns=1,
            instructions="Summarize this content.",
        )
        assert plan.tools_allowed == []
        assert plan.max_turns == 1

    def test_filtered_tool_plan(self):
        plan = ExecutionPlan(
            skill_name="stock_fundamentals",
            tools_allowed=["get_stock_info", "get_stock_history", "calculate"],
            max_turns=3,
        )
        assert len(plan.tools_allowed) == 3
        assert "get_stock_info" in plan.tools_allowed


class TestBaseSkill:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseSkill()

    def test_concrete_skill_must_implement_methods(self):
        class IncompleteSkill(BaseSkill):
            pass

        with pytest.raises(TypeError):
            IncompleteSkill()


from planner.skills.summarize_page import SummarizePageSkill


class TestSummarizePageSkill:
    def setup_method(self):
        self.skill = SummarizePageSkill()

    def test_name(self):
        assert self.skill.name == "summarize_page"

    def test_no_tools(self):
        assert self.skill.tools_allowed == []

    def test_single_turn(self):
        assert self.skill.max_turns == 1

    def test_matches_summarize_with_prescraped(self):
        score = self.skill.matches("summarize this page", has_prescraped=True, domain=None)
        assert score >= 0.8

    def test_matches_explain_with_prescraped(self):
        score = self.skill.matches("what does this article say?", has_prescraped=True, domain=None)
        assert score >= 0.7

    def test_no_match_without_prescraped(self):
        score = self.skill.matches("summarize this page", has_prescraped=False, domain=None)
        assert score == 0.0

    def test_no_match_stock_query(self):
        score = self.skill.matches("what is AAPL stock price?", has_prescraped=True, domain=None)
        assert score < 0.5

    def test_build_instructions_includes_content(self):
        content = "Page about earnings report..."
        instructions = self.skill.build_instructions(pre_scraped_content=content)
        assert content in instructions
        assert "summarize" in instructions.lower() or "content" in instructions.lower()

    def test_build_instructions_none_without_content(self):
        instructions = self.skill.build_instructions(pre_scraped_content=None)
        assert instructions is None


from planner.skills.stock_fundamentals import StockFundamentalsSkill
from planner.skills.options_analysis import OptionsAnalysisSkill
from planner.skills.financial_statements import FinancialStatementsSkill
from planner.skills.technical_analysis import TechnicalAnalysisSkill


class TestStockFundamentalsSkill:
    def setup_method(self):
        self.skill = StockFundamentalsSkill()

    def test_tools(self):
        # Compound queries like "AAPL EPS and gross margin" route to this skill
        # via the EPS pattern; without get_stock_financials the agent could not
        # fetch revenue/COGS and would invent the gross-margin number.
        assert set(self.skill.tools_allowed) == {"get_stock_info", "get_stock_financials", "get_stock_history", "get_earnings_info", "calculate"}

    def test_max_turns(self):
        assert self.skill.max_turns == 5

    def test_matches_price_query(self):
        assert self.skill.matches("what is AAPL stock price?", has_prescraped=False, domain=None) >= 0.7

    def test_matches_market_cap(self):
        assert self.skill.matches("market cap of MSFT", has_prescraped=False, domain=None) >= 0.7

    def test_no_match_options(self):
        assert self.skill.matches("show me AAPL options chain", has_prescraped=False, domain=None) < 0.5

    def test_no_instructions_override(self):
        assert self.skill.build_instructions() is None


class TestOptionsAnalysisSkill:
    def setup_method(self):
        self.skill = OptionsAnalysisSkill()

    def test_tools(self):
        assert set(self.skill.tools_allowed) == {"get_options_summary", "get_options_chain", "calculate"}

    def test_max_turns(self):
        assert self.skill.max_turns == 5

    def test_matches_options_volume(self):
        assert self.skill.matches("total options volume for AVGO", has_prescraped=False, domain=None) >= 0.7

    def test_matches_put_call_ratio(self):
        assert self.skill.matches("put call ratio for TSLA", has_prescraped=False, domain=None) >= 0.7


class TestFinancialStatementsSkill:
    def setup_method(self):
        self.skill = FinancialStatementsSkill()

    def test_tools(self):
        assert set(self.skill.tools_allowed) == {"get_stock_financials", "get_earnings_info", "calculate"}

    def test_max_turns(self):
        assert self.skill.max_turns == 5

    def test_matches_revenue(self):
        assert self.skill.matches("what was AAPL revenue last quarter?", has_prescraped=False, domain=None) >= 0.7

    def test_matches_earnings(self):
        assert self.skill.matches("when are MSFT earnings?", has_prescraped=False, domain=None) >= 0.7


class TestTechnicalAnalysisSkill:
    def setup_method(self):
        self.skill = TechnicalAnalysisSkill()

    def test_tools_include_tradingview(self):
        tools = self.skill.tools_allowed
        assert "get_coin_analysis" in tools
        assert "calculate" in tools

    def test_max_turns(self):
        assert self.skill.max_turns == 5

    def test_matches_rsi(self):
        assert self.skill.matches("what is the RSI for AAPL?", has_prescraped=False, domain=None) >= 0.7

    def test_matches_macd(self):
        assert self.skill.matches("show MACD for BTC", has_prescraped=False, domain=None) >= 0.7


from planner.skills.web_research import WebResearchSkill
from planner.skills.registry import SkillRegistry


class TestWebResearchSkill:
    def setup_method(self):
        self.skill = WebResearchSkill()

    def test_all_tools(self):
        assert self.skill.tools_allowed is None

    def test_max_turns(self):
        assert self.skill.max_turns == 10

    def test_always_matches_at_baseline(self):
        score = self.skill.matches("random query", has_prescraped=False, domain=None)
        assert 0.0 < score <= 0.2


class TestSkillRegistry:
    def setup_method(self):
        self.registry = SkillRegistry()

    def test_all_skills_registered(self):
        names = {s.name for s in self.registry.skills}
        assert names == {
            "summarize_page",
            "stock_fundamentals",
            "options_analysis",
            "financial_statements",
            "technical_analysis",
            "web_research",
        }

    def test_best_match_summarize(self):
        skill = self.registry.best_match("summarize this page", has_prescraped=True, domain=None)
        assert skill.name == "summarize_page"

    def test_best_match_stock_price(self):
        skill = self.registry.best_match("what is AAPL stock price?", has_prescraped=False, domain=None)
        assert skill.name == "stock_fundamentals"

    def test_best_match_fallback(self):
        skill = self.registry.best_match(
            "find me some interesting investment ideas for biotech sector",
            has_prescraped=False,
            domain=None,
        )
        assert skill.name == "web_research"

    def test_best_match_options(self):
        skill = self.registry.best_match("options volume for AVGO", has_prescraped=False, domain=None)
        assert skill.name == "options_analysis"

    def test_best_match_earnings(self):
        skill = self.registry.best_match("when are AAPL earnings?", has_prescraped=False, domain=None)
        assert skill.name == "financial_statements"

    def test_best_match_rsi(self):
        skill = self.registry.best_match("what is RSI for TSLA?", has_prescraped=False, domain=None)
        assert skill.name == "technical_analysis"
