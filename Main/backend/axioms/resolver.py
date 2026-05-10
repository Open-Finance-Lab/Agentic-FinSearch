"""Resolve (ratio, ticker, period) → XBRL-grounded numerical inputs.

Bridges the ratio engine with the existing XBRL parser. Domain knowledge
lives in ``RATIO_TAG_MAP``: the ordered list of US-GAAP tag names the
resolver will try for each logical input.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from mcp_server.xbrl.parser import parse_filing, find_filing

logger = logging.getLogger(__name__)

FILINGS_DIR = Path(__file__).resolve().parent.parent / "mcp_server" / "xbrl" / "filings"


@lru_cache(maxsize=64)
def _cached_find_filing(ticker: str) -> Optional[Path]:
    """find_filing() globs the filings dir on every call; cache per ticker so
    the validate endpoint (which hits check_applicability + fetch_ground_truth
    + xbrl_source_url per claim, three lookups) does at most one glob."""
    return find_filing(ticker, FILINGS_DIR)

# ---------------------------------------------------------------------------
# USER CONTRIBUTION POINT — domain-knowledge tag preferences.
#
# Each ratio maps logical input -> ordered list of US-GAAP XBRL tags to try.
# Order matters: the resolver uses the first tag it finds a value for.
#
# Key trade-offs you (FlyM1ss) should confirm or adjust:
#
# 1. accounting_equation.equity
#    Option A: StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest  (gross, includes NCI)
#    Option B: StockholdersEquity                                                       (parent-only)
#    Pairing: Liabilities (non-current-classified) typically nets minority interest,
#    so Option A pairs correctly and avoids the Tesla 0.53% false-failure gotcha
#    documented in the 2026-04-07 spec. Default: A then B as fallback.
#
# 2. gross_margin.revenue
#    Option A: RevenueFromContractWithCustomerExcludingAssessedTax  (ASC 606, modern)
#    Option B: Revenues                                              (pre-606, broader)
#    Apple/MSFT/Tesla all report under A. Default: A then B.
#
# 3. gross_margin.cogs
#    Option A: CostOfGoodsAndServicesSold   (Apple convention)
#    Option B: CostOfRevenue                (MSFT convention, includes services)
#    Option C: CostOfGoodsSold              (older / industrial)
#    Default below: A, B, C in order. Confirm.
#
# 4. current_ratio.*
#    AssetsCurrent and LiabilitiesCurrent are canonical; no alternatives needed
#    for the demo companies.
# ---------------------------------------------------------------------------
RATIO_TAG_MAP: Dict[str, Dict[str, List[str]]] = {
    "accounting_equation": {
        "assets":      ["Assets"],
        "liabilities": ["Liabilities"],
        "equity": [
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "StockholdersEquity",
        ],
        # Redeemable NCI / mezzanine equity. Missing for AAPL, MSFT → treated as 0.
        # Critical for TSLA (0.227% balance-sheet discrepancy without it).
        "temporary_equity": [
            "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
            "RedeemableNoncontrollingInterestEquityCarryingAmount",
        ],
    },
    "gross_margin": {
        "revenue": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",  # ASC 606 (modern)
            "Revenues",                                              # pre-606 / general
            "SalesRevenueNet",                                       # retail / legacy industrial
            "SalesRevenueGoodsNet",                                  # goods-only retail
        ],
        "cogs": [
            "CostOfGoodsAndServicesSold",  # Apple-style
            "CostOfRevenue",               # MSFT / Tesla
            "CostOfGoodsSold",             # older / industrial
        ],
    },
    "current_ratio": {
        "current_assets":      ["AssetsCurrent"],
        "current_liabilities": ["LiabilitiesCurrent"],
    },
}


def _select_fact(
    facts: List[dict],
    tag_name: str,
    period: str,
    prefer_instant: bool,
) -> Optional[float]:
    """Pick the matching fact for a tag at a given period.

    `period` is a fiscal period-end ISO date (YYYY-MM-DD).
    For instant (balance sheet) tags we require period_end == period and
    period_start is None. For duration (income statement) tags we require
    period_end == period and period_start is present (annual duration
    preferred — picks largest duration ending on that date).
    """
    candidates = [
        f for f in facts
        if f["tag"] == tag_name
        and f["period_end"] == period
        and not f["has_dimensions"]
    ]
    if prefer_instant:
        candidates = [f for f in candidates if f["period_start"] is None]
    else:
        candidates = [f for f in candidates if f["period_start"] is not None]
        # Prefer the longest duration ending on `period` (annual over quarterly)
        candidates.sort(
            key=lambda f: f["period_start"] or "9999-12-31",
        )  # ascending start => longest duration first

    if not candidates:
        return None
    return float(candidates[0]["value"])


def _resolve_tag_value(
    facts: List[dict],
    tag_options: List[str],
    period: str,
    prefer_instant: bool,
) -> Optional[float]:
    """Try each tag in preference order; return first hit."""
    for tag in tag_options:
        value = _select_fact(facts, tag, period, prefer_instant)
        if value is not None:
            return value
    return None


# Per-ratio metadata: which inputs are instant vs duration.
_INPUT_KINDS = {
    "accounting_equation": {
        "assets": True, "liabilities": True, "equity": True, "temporary_equity": True,
    },
    "gross_margin":        {"revenue": False, "cogs": False},
    "current_ratio":       {"current_assets": True, "current_liabilities": True},
}


# Ratios that require a classified balance sheet (current vs non-current split).
# Financial-sector filers (banks, insurance, REITs) use unclassified balance sheets.
_REQUIRES_CLASSIFIED_BS = {"current_ratio"}


def _filing_has_tag(facts: List[dict], tag_name: str) -> bool:
    return any(f["tag"] == tag_name for f in facts)


def check_applicability(ratio: str, ticker: str) -> Optional[Dict[str, str]]:
    """Return a NOT_APPLICABLE reason dict if `ratio` does not apply to this
    filer's reporting structure; else None.

    Detection is XBRL-structural, not sector-lookup: if a filing lacks the
    canonical classification tag entirely, the balance sheet is unclassified
    (as banks, insurance, and REITs report). This avoids SIC-code dependencies.
    """
    if ratio not in _REQUIRES_CLASSIFIED_BS:
        return None

    filing = _cached_find_filing(ticker)
    if filing is None:
        return None  # handled downstream as missing filing
    facts = parse_filing(filing)
    if not _filing_has_tag(facts, "AssetsCurrent"):
        return {
            "ratio": ratio,
            "reason": (
                f"{ticker.upper()} uses an unclassified balance sheet (typical for "
                "banks, insurance, and REITs). The current ratio is not defined "
                "for this reporting structure."
            ),
        }
    return None


def fetch_ground_truth(
    ratio: str,
    ticker: str,
    period: str,
) -> Dict[str, Optional[float]]:
    """Return the resolved {input_name: value} dict for a ratio at (ticker, period).

    Values may be None if the tag was not found in the filing. The engine's
    check_* functions handle None as SKIPPED.
    """
    if ratio not in RATIO_TAG_MAP:
        logger.warning("Unknown ratio: %s", ratio)
        return {}

    filing = _cached_find_filing(ticker)
    if filing is None:
        logger.warning("No local XBRL filing for ticker=%s", ticker)
        return {name: None for name in RATIO_TAG_MAP[ratio]}

    facts = parse_filing(filing)

    out: Dict[str, Optional[float]] = {}
    for input_name, tag_options in RATIO_TAG_MAP[ratio].items():
        prefer_instant = _INPUT_KINDS[ratio][input_name]
        out[input_name] = _resolve_tag_value(facts, tag_options, period, prefer_instant)

    return out


def xbrl_source_url(ticker: str) -> Optional[str]:
    """Return the relative path of the local filing used as ground truth."""
    filing = _cached_find_filing(ticker)
    if filing is None:
        return None
    return str(filing.relative_to(FILINGS_DIR.parent.parent))
