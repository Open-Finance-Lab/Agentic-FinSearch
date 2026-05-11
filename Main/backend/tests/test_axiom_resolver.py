"""Integration tests for the resolver against the 3 local XBRL filings.

Verifies that RATIO_TAG_MAP correctly pulls ground-truth values from the
SEC filings committed under mcp_server/xbrl/filings/. The mcp_server
shadowing workaround lives in tests/conftest.py.
"""
import pytest

from axioms.resolver import fetch_ground_truth, check_applicability, xbrl_source_url


# ── accounting_equation ground truth ─────────────────────────────────

def test_aapl_accounting_equation_resolves():
    v = fetch_ground_truth("accounting_equation", "AAPL", "2023-09-30")
    assert v["assets"] == pytest.approx(352583e6, rel=1e-6)
    assert v["liabilities"] == pytest.approx(290437e6, rel=1e-6)
    assert v["equity"] == pytest.approx(62146e6, rel=1e-6)
    # Apple has no NCI, tag miss expected
    assert v["temporary_equity"] is None


def test_msft_accounting_equation_resolves():
    v = fetch_ground_truth("accounting_equation", "MSFT", "2023-06-30")
    assert v["assets"] == pytest.approx(411976e6, rel=1e-6)
    assert v["liabilities"] == pytest.approx(205753e6, rel=1e-6)
    assert v["equity"] == pytest.approx(206223e6, rel=1e-6)
    assert v["temporary_equity"] is None


def test_tsla_accounting_equation_resolves_with_nci():
    v = fetch_ground_truth("accounting_equation", "TSLA", "2023-12-31")
    assert v["assets"] == pytest.approx(106618e6, rel=1e-6)
    assert v["liabilities"] == pytest.approx(43009e6, rel=1e-6)
    # Tesla uses the "Including NCI" equity tag
    assert v["equity"] == pytest.approx(63367e6, rel=1e-6)
    # Redeemable NCI must resolve (this is the gotcha)
    assert v["temporary_equity"] == pytest.approx(242e6, rel=1e-6)


# ── gross_margin ground truth ───────────────────────────────────────

@pytest.mark.parametrize("ticker,period,expected_rev,expected_cogs", [
    ("AAPL", "2023-09-30", 383285e6, 214137e6),
    ("MSFT", "2023-06-30", 211915e6, 65863e6),
    ("TSLA", "2023-12-31", 96773e6, 79113e6),
])
def test_gross_margin_resolves(ticker, period, expected_rev, expected_cogs):
    v = fetch_ground_truth("gross_margin", ticker, period)
    assert v["revenue"] == pytest.approx(expected_rev, rel=1e-6)
    assert v["cogs"] == pytest.approx(expected_cogs, rel=1e-6)


# ── current_ratio ground truth ──────────────────────────────────────

@pytest.mark.parametrize("ticker,period,expected_ca,expected_cl", [
    ("AAPL", "2023-09-30", 143566e6, 145308e6),
    ("MSFT", "2023-06-30", 184257e6, 104149e6),
    ("TSLA", "2023-12-31", 49616e6, 28748e6),
])
def test_current_ratio_resolves(ticker, period, expected_ca, expected_cl):
    v = fetch_ground_truth("current_ratio", ticker, period)
    assert v["current_assets"] == pytest.approx(expected_ca, rel=1e-6)
    assert v["current_liabilities"] == pytest.approx(expected_cl, rel=1e-6)


# ── applicability gating ────────────────────────────────────────────

def test_applicability_passes_for_classified_balance_sheets():
    for t in ["AAPL", "MSFT", "TSLA"]:
        assert check_applicability("current_ratio", t) is None


def test_applicability_ignores_ratios_not_requiring_classification():
    assert check_applicability("accounting_equation", "AAPL") is None
    assert check_applicability("gross_margin", "AAPL") is None


def test_applicability_missing_filing_returns_none():
    """When filing is absent, we defer to downstream (returns None here)."""
    assert check_applicability("current_ratio", "NONEXISTENT") is None


def test_xbrl_source_url_resolves():
    src = xbrl_source_url("AAPL")
    assert src is not None
    assert "aapl" in src.lower()


def test_xbrl_source_url_missing_filing():
    assert xbrl_source_url("NONEXISTENT") is None
