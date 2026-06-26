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
    # Unknown entity (no data in the truth layer) -> defer downstream, not
    # "unclassified". `Assets` is universal across SEC filers (incl. banks/insurers/
    # REITs that lack the current/non-current split), so its absence means we simply
    # have no balance sheet for this ticker, which is distinct from an unclassified one.
    if not tl.entity_has_tag(ticker, "Assets"):
        return None
    if not tl.entity_has_tag(ticker, "AssetsCurrent"):
        return {
            "ratio": ratio,
            "reason": (
                f"{ticker.upper()} uses an unclassified balance sheet (typical for "
                "banks, insurance, and REITs). The current ratio is not defined "
                "for this reporting structure."
            ),
        }
    return None


def xbrl_source_url(ticker: str) -> Optional[str]:
    """Representative provenance for the Validate source card: the ticker's most
    recently filed 10-K accession (replaces the old local-filing path)."""
    return tl.latest_filing(ticker, form="10-K")
