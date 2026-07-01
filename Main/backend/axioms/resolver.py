"""Resolve (ratio, ticker, period) -> XBRL-grounded numerical inputs.

Thin adapter over the canonical truth layer (`truthlayer`). Domain knowledge
(which us-gaap tags back each logical input) now lives in
`truthlayer.registry.CONCEPT_REGISTRY`; ratios reference concepts via
`RATIO_CONCEPTS`. The three public functions keep their original signatures so
`axioms.engine`, `axioms.__init__`, and `axioms.sources` are unchanged.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Dict, Optional

from truthlayer import retrieve as tl
from truthlayer.contracts import Period, Query
from truthlayer.registry import RATIO_CONCEPTS

logger = logging.getLogger(__name__)

# Retained for api.views.xbrl_filing_download, which serves the local SEC XBRL
# filings (mcp_server/xbrl/filings) for the Validate sources popup — independent of
# the truth layer. The resolver itself no longer reads local filings, but this
# constant stays here as the established import site so api/views.py is untouched.
FILINGS_DIR = Path(__file__).resolve().parent.parent / "mcp_server" / "xbrl" / "filings"

# Ratios that require a classified balance sheet (current vs non-current split).
# Financial-sector filers (banks, insurance, REITs) use unclassified balance sheets.
_REQUIRES_CLASSIFIED_BS = {"current_ratio"}


def _period(period: str) -> Period:
    return Period(period_end=date.fromisoformat(period)) if period else Period()


def fetch_ground_truth(ratio: str, ticker: str, period: str) -> Dict[str, Optional[float]]:
    """Return the resolved {input_name: value} dict for a ratio at (ticker, period).

    Values may be None if no tag was found; the engine's check_* functions handle
    None as SKIPPED. Uses as_of=None (latest known) — the Validate path is not
    restatement-sensitive.
    """
    if ratio not in RATIO_CONCEPTS:
        logger.warning("Unknown ratio: %s", ratio)
        return {}
    p = _period(period)
    out: Dict[str, Optional[float]] = {}
    for input_name, concept in RATIO_CONCEPTS[ratio].items():
        ev = tl.retrieve_evidence(Query(ticker, concept, p))
        out[input_name] = ev.value
    return out


def check_applicability(ratio: str, ticker: str) -> Optional[Dict[str, str]]:
    """Return a NOT_APPLICABLE reason dict if `ratio` does not apply to this
    filer's reporting structure; else None. Structural (tag-presence), not SIC."""
    if ratio not in _REQUIRES_CLASSIFIED_BS:
        return None
    # Inspect the entity's MOST RECENT 10-K, mirroring the old resolver. None means
    # no filing for this ticker in the truth layer -> defer downstream (distinct from
    # an unclassified filer). False means the latest 10-K omits AssetsCurrent, i.e. an
    # unclassified balance sheet (banks/insurers/REITs lack the current/non-current
    # split). True means classified -> the current ratio applies.
    reports_current = tl.filing_reports_tag(ticker, "AssetsCurrent", form="10-K")
    if reports_current is None or reports_current:
        return None
    return {
        "ratio": ratio,
        "reason": (
            f"{ticker.upper()} uses an unclassified balance sheet (typical for "
            "banks, insurance, and REITs). The current ratio is not defined "
            "for this reporting structure."
        ),
    }


def xbrl_source_url(ticker: str) -> Optional[str]:
    """Representative provenance for the Validate source card: the ticker's most
    recently filed 10-K accession (replaces the old local-filing path)."""
    return tl.latest_filing(ticker, form="10-K")
