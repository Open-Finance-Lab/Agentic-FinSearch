"""Unit tests for the axiom engine: pure-function correctness."""
import pytest

from axioms.engine import (
    RatioResult,
    check_accounting_equation,
    check_gross_margin,
    check_current_ratio,
)


# ── accounting_equation ─────────────────────────────────────────────

def test_accounting_equation_exact_match():
    r = check_accounting_equation(assets=100.0, liabilities=60.0, equity=40.0)
    assert r.status == "VERIFIED"
    assert r.variance_pct == 0.0


def test_accounting_equation_with_temporary_equity():
    # Tesla-like shape: $242M redeemable NCI
    r = check_accounting_equation(
        assets=106618e6, liabilities=43009e6, equity=63367e6,
        temporary_equity=242e6,
    )
    assert r.status == "VERIFIED"
    assert r.variance_pct == 0.0


def test_accounting_equation_missing_temp_equity_tolerated():
    """For filers without NCI, temporary_equity=None is treated as 0."""
    r = check_accounting_equation(
        assets=352583e6, liabilities=290437e6, equity=62146e6,
        temporary_equity=None,
    )
    assert r.status == "VERIFIED"


def test_accounting_equation_claimed_assets_detects_hallucination():
    r = check_accounting_equation(
        assets=100.0, liabilities=60.0, equity=40.0,
        claimed_assets=105.0,  # agent hallucinated 5% too high
    )
    assert r.status == "FAILED"
    assert r.variance_pct == pytest.approx(5.0, rel=1e-3)


def test_accounting_equation_skipped_on_missing_input():
    r = check_accounting_equation(assets=None, liabilities=60.0, equity=40.0)
    assert r.status == "SKIPPED"


# ── gross_margin ────────────────────────────────────────────────────

def test_gross_margin_exact():
    # Apple-like: revenue 383.285B, COGS 214.137B → 44.13%
    r = check_gross_margin(revenue=383285e6, cogs=214137e6, claimed_margin_pct=44.13)
    assert r.status == "VERIFIED"


def test_gross_margin_detects_0_01pp_discrepancy():
    # 18.24 vs 18.25 → 0.05% relative variance — outside our 0.01% tolerance
    r = check_gross_margin(revenue=96773e6, cogs=79113e6, claimed_margin_pct=18.24)
    assert r.status == "FAILED"


def test_gross_margin_zero_revenue_skipped():
    r = check_gross_margin(revenue=0, cogs=0, claimed_margin_pct=0)
    assert r.status == "SKIPPED"


# ── current_ratio ───────────────────────────────────────────────────

def test_current_ratio_exact():
    r = check_current_ratio(current_assets=143566e6, current_liabilities=145308e6,
                            claimed_ratio=0.9880)
    assert r.status == "VERIFIED"


def test_current_ratio_zero_denominator():
    r = check_current_ratio(current_assets=100, current_liabilities=0, claimed_ratio=1.0)
    assert r.status == "SKIPPED"


def test_current_ratio_detects_wrong_claim():
    # True = 1.77; agent says 2.0 → 13% off
    r = check_current_ratio(current_assets=184257e6, current_liabilities=104149e6,
                            claimed_ratio=2.0)
    assert r.status == "FAILED"
    assert r.variance_pct > 10.0


# ── tolerance edge cases ────────────────────────────────────────────

def test_tolerance_relative_at_large_magnitude():
    # 100B with 0.005% variance → 5M diff, should be under 0.01% tolerance
    r = check_accounting_equation(assets=100e9 + 5e6, liabilities=60e9, equity=40e9)
    assert r.status == "VERIFIED"


def test_tolerance_absolute_at_near_zero():
    # Near-zero case: tiny absolute diff must still pass
    r = check_current_ratio(current_assets=1.0, current_liabilities=1.0,
                            claimed_ratio=1.001)
    # 0.001 diff vs 1.0 expected → 0.1% relative, OUTSIDE 0.01% relative tolerance
    # but absolute tolerance is 0.005 → still absolute pass
    assert r.status == "VERIFIED"
