"""Integration tests for hallucination mitigation features.

Tests the two reported scenarios (EPS surprise, options volume) and
verifies that prompt changes are in place.
"""
import json
from unittest.mock import MagicMock


# ── Calculator: EPS surprise scenario ───────────────────────────────

def test_calculator_eps_surprise():
    """Verify (0.50-0.45)/0.45*100 = 11.111... (was hallucinated as 10.96%)."""
    from datascraper.calculator_tool import safe_compute
    result = safe_compute("(0.50 - 0.45) / 0.45 * 100")
    assert abs(result - 11.111111111111111) < 1e-9


# ── Validator: options volume orphan scenario ───────────────────────

def test_validator_catches_options_volume_orphan():
    """Verify 97271 is flagged as orphan when tool output has call_volume=10899, put_volume=9976."""
    from datascraper.numerical_validator import validate_numerical_accuracy

    tool_data = json.dumps({"call_volume": 10899, "put_volume": 9976})
    item = MagicMock()
    item.type = "tool_call_output_item"
    item.output = tool_data
    run_result = MagicMock()
    run_result.new_items = [item]

    result = validate_numerical_accuracy(
        run_result,
        "The total daily options volume for AVGO is 97,271 contracts."
    )
    assert "97271" in result.orphan_numbers


# ── Prompt checks ──────────────────────────────────────────────────

def test_synthesis_prompt_contains_anti_aggregation():
    """Verify _SYNTHESIS_SYSTEM has SOURCE INTEGRITY rules."""
    from datascraper.research_engine import _SYNTHESIS_SYSTEM
    assert "SOURCE INTEGRITY" in _SYNTHESIS_SYSTEM
    assert "partial data" in _SYNTHESIS_SYSTEM


def test_core_prompt_contains_calculation_rules(core_prompt):
    """Verify core.md has CALCULATION RULES and references calculate()."""
    assert "CALCULATION RULES" in core_prompt
    assert "calculate()" in core_prompt
