"""Tests for the safe calculator tool."""
import pytest


# ── safe_compute tests ──────────────────────────────────────────────

def test_basic_addition():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("2 + 3") == 5.0


def test_basic_subtraction():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("10 - 4") == 6.0


def test_basic_multiplication():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("6 * 7") == 42.0


def test_basic_division():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("10 / 4") == 2.5


def test_percentage_calculation():
    """The exact EPS surprise case that was hallucinated as 10.96% instead of 11.11%."""
    from datascraper.calculator_tool import safe_compute
    result = safe_compute("(0.50 - 0.45) / 0.45 * 100")
    assert abs(result - 11.111111111111111) < 1e-9


def test_negative_numbers():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("-5 + 3") == -2.0


def test_exponentiation():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("2 ** 10") == 1024.0


def test_floor_division():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("7 // 2") == 3.0


def test_modulo():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("10 % 3") == 1.0


def test_whitelisted_abs():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("abs(-42)") == 42.0


def test_whitelisted_round():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("round(3.14159, 2)") == 3.14


def test_whitelisted_min():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("min(3, 5, 1)") == 1.0


def test_whitelisted_max():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("max(3, 5, 1)") == 5.0


def test_whitelisted_sqrt():
    from datascraper.calculator_tool import safe_compute
    assert safe_compute("sqrt(16)") == 4.0


def test_division_by_zero():
    from datascraper.calculator_tool import safe_compute
    with pytest.raises(ValueError, match="division by zero"):
        safe_compute("1 / 0")


def test_empty_input():
    from datascraper.calculator_tool import safe_compute
    with pytest.raises(ValueError):
        safe_compute("")


def test_large_numbers():
    from datascraper.calculator_tool import safe_compute
    result = safe_compute("999999999 * 999999999")
    assert result == 999999998000000001.0


# ── Security: reject dangerous inputs ───────────────────────────────

def test_reject_variable_names():
    from datascraper.calculator_tool import safe_compute
    with pytest.raises(ValueError, match="not allowed"):
        safe_compute("x + 1")


def test_reject_import():
    """Verify that import attempts are blocked by the AST walker."""
    from datascraper.calculator_tool import safe_compute
    with pytest.raises(ValueError, match="not allowed"):
        safe_compute("__import__('os').system('ls')")


def test_reject_attribute_access():
    from datascraper.calculator_tool import safe_compute
    with pytest.raises(ValueError, match="not allowed"):
        safe_compute("().__class__.__bases__")


def test_reject_strings():
    from datascraper.calculator_tool import safe_compute
    with pytest.raises(ValueError, match="not allowed"):
        safe_compute("'hello' + 'world'")


def test_reject_unknown_function():
    """Verify that non-whitelisted function calls are blocked."""
    from datascraper.calculator_tool import safe_compute
    with pytest.raises(ValueError, match="not allowed"):
        safe_compute("open('file.txt')")


# ── function_tool registration ──────────────────────────────────────

def test_get_calculator_tools_returns_list():
    from datascraper.calculator_tool import get_calculator_tools
    tools = get_calculator_tools()
    assert isinstance(tools, list)
    assert len(tools) == 1
