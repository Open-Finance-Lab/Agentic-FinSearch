"""Deterministic ratio-checking engine for FinSearch Layer 1.

Three axioms, each a pure function. No LLM, no network, no file I/O.
Tolerance: max(0.01% of |expected|, 0.005) — handles near-zero without
spurious failures on clean data.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RatioResult:
    status: str                          # "VERIFIED" | "FAILED" | "SKIPPED"
    ratio: str                           # "accounting_equation" | "gross_margin" | "current_ratio"
    formula: str                         # human-readable formula
    expected: Optional[float] = None     # computed from ground truth
    actual: Optional[float] = None       # claimed by agent
    variance_pct: Optional[float] = None
    message: str = ""


def _tolerance(expected: float) -> float:
    return max(1e-4 * abs(expected), 5e-3)


def _verdict(expected: float, actual: float) -> tuple[str, float]:
    diff = abs(expected - actual)
    if expected == 0:
        variance_pct = 0.0 if diff <= 5e-3 else float("inf")
    else:
        variance_pct = 100.0 * diff / abs(expected)
    status = "VERIFIED" if diff <= _tolerance(expected) else "FAILED"
    return status, variance_pct


def check_accounting_equation(
    assets: Optional[float],
    liabilities: Optional[float],
    equity: Optional[float],
    temporary_equity: Optional[float] = 0.0,
    claimed_assets: Optional[float] = None,
) -> RatioResult:
    """Assets = Liabilities + Temporary Equity + Equity.

    `temporary_equity` captures redeemable noncontrolling interests and similar
    mezzanine items that sit between L and E on a 10-K balance sheet. Companies
    without these (AAPL, MSFT) resolve to None from XBRL and are treated as 0.
    """
    formula = "Assets = Liabilities + Temporary Equity + Equity"
    if assets is None or liabilities is None or equity is None:
        return RatioResult(
            status="SKIPPED",
            ratio="accounting_equation",
            formula=formula,
            message="Missing one or more of (assets, liabilities, equity) from XBRL.",
        )
    te = temporary_equity if temporary_equity is not None else 0.0

    expected = liabilities + te + equity
    actual = claimed_assets if claimed_assets is not None else assets
    status, variance_pct = _verdict(expected, actual)
    te_part = f" + TempEq=${te:,.0f}" if te != 0 else ""
    msg = (
        f"A=${assets:,.0f}, L=${liabilities:,.0f}{te_part} + E=${equity:,.0f} = ${expected:,.0f}"
        if claimed_assets is None
        else f"Claimed A=${actual:,.0f}, L+TE+E=${expected:,.0f}"
    )
    return RatioResult(
        status=status,
        ratio="accounting_equation",
        formula=formula,
        expected=expected,
        actual=actual,
        variance_pct=variance_pct,
        message=msg,
    )


def check_gross_margin(
    revenue: Optional[float],
    cogs: Optional[float],
    claimed_margin_pct: Optional[float],
) -> RatioResult:
    """Gross Margin (%) = (Revenue − COGS) / Revenue × 100.

    `claimed_margin_pct` is expressed in percentage points (e.g., 43.3 for 43.3%).
    """
    formula = "Gross Margin = (Revenue − COGS) / Revenue"
    if revenue is None or cogs is None or claimed_margin_pct is None:
        return RatioResult(
            status="SKIPPED",
            ratio="gross_margin",
            formula=formula,
            message="Missing revenue, COGS, or claimed margin.",
        )
    if revenue == 0:
        return RatioResult(
            status="SKIPPED",
            ratio="gross_margin",
            formula=formula,
            message="Revenue is zero; margin undefined.",
        )

    expected = 100.0 * (revenue - cogs) / revenue
    actual = claimed_margin_pct
    status, variance_pct = _verdict(expected, actual)
    return RatioResult(
        status=status,
        ratio="gross_margin",
        formula=formula,
        expected=expected,
        actual=actual,
        variance_pct=variance_pct,
        message=f"Ground truth {expected:.2f}%, claimed {actual:.2f}%.",
    )


def check_current_ratio(
    current_assets: Optional[float],
    current_liabilities: Optional[float],
    claimed_ratio: Optional[float],
) -> RatioResult:
    """Current Ratio = Current Assets / Current Liabilities."""
    formula = "Current Ratio = Current Assets / Current Liabilities"
    if current_assets is None or current_liabilities is None or claimed_ratio is None:
        return RatioResult(
            status="SKIPPED",
            ratio="current_ratio",
            formula=formula,
            message="Missing current assets, current liabilities, or claimed ratio.",
        )
    if current_liabilities == 0:
        return RatioResult(
            status="SKIPPED",
            ratio="current_ratio",
            formula=formula,
            message="Current liabilities are zero; ratio undefined.",
        )

    expected = current_assets / current_liabilities
    actual = claimed_ratio
    status, variance_pct = _verdict(expected, actual)
    return RatioResult(
        status=status,
        ratio="current_ratio",
        formula=formula,
        expected=expected,
        actual=actual,
        variance_pct=variance_pct,
        message=f"Ground truth {expected:.4f}, claimed {actual:.4f}.",
    )
