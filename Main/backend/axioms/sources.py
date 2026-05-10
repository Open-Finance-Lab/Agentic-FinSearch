"""Build source-card entries for XBRL filings used by Layer 1 Validate.

Bridges the claim registry (populated during the agent run by ``report_claim``)
and the Sources popup (populated via ``used_sources`` in the final response
frame). Without this bridge, the XBRL filing that grounds the verification
would only be visible after the user clicks Validate — users who never click
Validate never see which filing was consulted.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePath
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

from axioms.registry import get_claims
from axioms.resolver import xbrl_source_url

# ---------------------------------------------------------------------------
# USER CONTRIBUTION POINT — source-card copy.
#
# The three strings below control what appears on the XBRL card in the Sources
# popup. They're the user's first signal that the claim is grounded in a real
# SEC filing, so the wording matters. Tune to taste after seeing it rendered.
#
# - ``_SITE_NAME`` is shown as the small site label at the top of the card.
# - ``_build_title`` returns the big card title.
# - ``_build_snippet`` returns the one-line description under the title.
#   Listing the ratios it grounds (accounting_equation, gross_margin, etc.)
#   makes the link feel purposeful rather than decorative.
# ---------------------------------------------------------------------------
_SITE_NAME = "SEC XBRL"


def _build_title(ticker: str, period: str) -> str:
    year = period[:4] if period else ""
    year_suffix = f" FY{year}" if year else ""
    return f"{ticker.upper()}{year_suffix} XBRL Filing (SEC)"


def _build_snippet(ratios: List[str]) -> str:
    unique = sorted(set(ratios))
    if not unique:
        return "SEC XBRL filing used as ground truth for ratio verification."
    ratios_str = ", ".join(unique)
    return f"SEC XBRL filing used as ground truth for: {ratios_str}."


def build_xbrl_sources(
    session_id: str,
    absolute_uri_builder: Optional[Callable[[str], str]] = None,
) -> List[Dict]:
    """Return source-card dicts for every unique XBRL filing referenced by
    claims in the session's registry.

    One card per (ticker, period) pair: multiple claims on the same filing
    collapse to one source, with their ratio names merged into the snippet.

    ``absolute_uri_builder`` converts an API path to a full URL. Pass
    ``request.build_absolute_uri`` so the frontend receives clickable links;
    fallback leaves the path relative, which still works when the frontend
    and backend are served from the same origin but not otherwise.
    """
    if not session_id:
        return []

    claims = get_claims(session_id)
    if not claims:
        return []

    # Group ratios by (ticker, period) so one filing gets one card regardless
    # of how many ratios the agent emitted against it.
    ratios_by_filing: Dict[tuple, List[str]] = defaultdict(list)
    for claim in claims:
        ticker = (claim.get("ticker") or "").strip()
        period = (claim.get("period") or "").strip()
        ratio = claim.get("ratio")
        if not ticker or not ratio:
            continue
        ratios_by_filing[(ticker, period)].append(ratio)

    sources: List[Dict] = []
    for (ticker, period), ratios in ratios_by_filing.items():
        filing_path = xbrl_source_url(ticker)
        if not filing_path:
            # No local filing — skip rather than emit a dead link.
            continue

        filename = PurePath(filing_path).name
        api_path = f"/api/axioms/xbrl/{quote(filename)}/"
        url = absolute_uri_builder(api_path) if absolute_uri_builder else api_path

        sources.append({
            "url": url,
            "title": _build_title(ticker, period),
            "site_name": _SITE_NAME,
            "display_url": filename,
            "snippet": _build_snippet(ratios),
            "source_type": "xbrl",
            "provisional": False,
        })

    return sources


def merge_xbrl_sources(existing: List[Dict], xbrl: List[Dict]) -> List[Dict]:
    """Append XBRL source entries to an existing source list, skipping any
    whose URL is already present. Preserves ordering (web sources first).
    """
    if not xbrl:
        return existing

    existing_urls = {
        entry.get("url")
        for entry in existing
        if isinstance(entry, dict) and entry.get("url")
    }
    return existing + [entry for entry in xbrl if entry.get("url") not in existing_urls]


__all__ = ["build_xbrl_sources", "merge_xbrl_sources"]
