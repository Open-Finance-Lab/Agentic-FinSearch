"""Tests for the upgraded numerical validator with structured ValidationResult."""
import pytest
import json
from unittest.mock import MagicMock


def _make_run_result(tool_outputs: list[str]):
    """Create a mock run_result with tool_call_output_items."""
    items = []
    for output in tool_outputs:
        item = MagicMock()
        item.type = "tool_call_output_item"
        item.output = output
        items.append(item)
    result = MagicMock()
    result.new_items = items
    return result


# ── ValidationResult structure ──────────────────────────────────────

def test_validation_result_dataclass():
    from datascraper.numerical_validator import ValidationResult
    vr = ValidationResult(exact_matches=2, close_matches=[], orphan_numbers=[], suspicious=[])
    assert vr.exact_matches == 2
    assert vr.close_matches == []
    assert vr.orphan_numbers == []
    assert vr.suspicious == []


def test_validate_returns_validation_result():
    from datascraper.numerical_validator import validate_numerical_accuracy, ValidationResult
    tool_data = json.dumps({"price": 150.25, "volume": 1000000})
    run_result = _make_run_result([tool_data])
    result = validate_numerical_accuracy(run_result, "The price is $150.25 with volume 1,000,000.")
    assert isinstance(result, ValidationResult)


# ── Exact match detection ───────────────────────────────────────────

def test_exact_matches_counted():
    from datascraper.numerical_validator import validate_numerical_accuracy
    tool_data = json.dumps({"price": 190.68, "change": 2.35})
    run_result = _make_run_result([tool_data])
    result = validate_numerical_accuracy(run_result, "The stock price is $190.68, up $2.35.")
    assert result.exact_matches >= 2
    assert len(result.orphan_numbers) == 0


# ── Close-but-wrong detection ──────────────────────────────────────

def test_suspicious_close_match():
    from datascraper.numerical_validator import validate_numerical_accuracy
    tool_data = json.dumps({"price": 190.68})
    run_result = _make_run_result([tool_data])
    # 190.66 is close but wrong (within 1% but not exact)
    result = validate_numerical_accuracy(run_result, "The stock price is $190.66.")
    assert len(result.suspicious) > 0


# ── Orphan number detection ────────────────────────────────────────

def test_orphan_number_detected():
    """The options volume case: 97271 is not in any tool output."""
    from datascraper.numerical_validator import validate_numerical_accuracy
    tool_data = json.dumps({"call_volume": 10899, "put_volume": 9976})
    run_result = _make_run_result([tool_data])
    result = validate_numerical_accuracy(
        run_result,
        "The total daily options volume for AVGO is 97,271 contracts."
    )
    assert "97271" in result.orphan_numbers


# ── Empty/no-data cases ────────────────────────────────────────────

def test_empty_response():
    from datascraper.numerical_validator import validate_numerical_accuracy, ValidationResult
    run_result = _make_run_result([])
    result = validate_numerical_accuracy(run_result, "")
    assert isinstance(result, ValidationResult)
    assert result.exact_matches == 0


def test_no_tool_numbers():
    from datascraper.numerical_validator import validate_numerical_accuracy, ValidationResult
    run_result = _make_run_result([])
    result = validate_numerical_accuracy(run_result, "The price is $150.25.")
    assert isinstance(result, ValidationResult)


# ── No false positives on exact matches ─────────────────────────────

def test_no_orphans_when_all_match():
    from datascraper.numerical_validator import validate_numerical_accuracy
    tool_data = json.dumps({"eps_reported": 0.50, "eps_estimated": 0.45})
    run_result = _make_run_result([tool_data])
    result = validate_numerical_accuracy(run_result, "EPS was $0.50 vs estimated $0.45.")
    assert len(result.orphan_numbers) == 0
    assert len(result.suspicious) == 0
