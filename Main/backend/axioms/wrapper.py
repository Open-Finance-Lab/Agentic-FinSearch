"""Post-process finalized prose to decorate reported claim values.

Layer 1 anchoring strategy: the agent has no formatting responsibility.
After the LLM stream completes, the server picks the earliest-matching
candidate string for each registered ``claimed_value`` and wraps *every*
non-delimited occurrence of that candidate in a
``<span data-claim-id="...">value</span>`` so the frontend can decorate
each one per Validate status.

Delimited regions (fenced code, inline code, math, HTML attributes,
existing ``data-claim-id`` spans) are treated as non-wrappable and left
untouched — this preserves markdown structural integrity and makes the
function idempotent on re-run (a claim_id already present anywhere in
the prose is skipped wholesale).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

# Delimited regions that must NOT be rewritten. Order matters for the
# alternation: longer/more-specific first so `$$...$$` binds before `$...$`
# and full claim-id spans bind before generic HTML tags.
_DELIMITED = re.compile(
    r"```.*?```"                                                 # fenced code
    r"|`[^`\n]+`"                                                # inline code
    r"|\$\$.*?\$\$"                                              # display math $$
    r"|\$[^$\n]+\$"                                              # inline math $
    r"|\\\[.*?\\\]"                                              # display math \[
    r"|\\\(.*?\\\)"                                              # inline math \(
    r'|<span\s+data-claim-id="[^"]*"[^>]*>.*?</span>'            # existing wrap
    r"|</?[a-zA-Z][^>]*>",                                       # HTML tag (name required)
    re.DOTALL,
)


def _split_by_delimited(prose: str) -> List[List]:
    """Return a list of ``[is_wrappable, text]`` segments, in order."""
    segments: List[List] = []
    i = 0
    for m in _DELIMITED.finditer(prose):
        s, e = m.start(), m.end()
        if i < s:
            segments.append([True, prose[i:s]])
        segments.append([False, prose[s:e]])
        i = e
    if i < len(prose):
        segments.append([True, prose[i:]])
    return segments


def _push_unique(out: List[str], value: str) -> None:
    if value and value not in out:
        out.append(value)


def _candidates(claim: Dict[str, Any]) -> List[str]:
    """Ratio-aware candidate strings, ordered longer/more-specific first.

    ``wrap_claim_values`` picks one candidate per claim — the earliest one
    that appears anywhere in wrappable prose — so candidate order matters
    when two candidates overlap (e.g., ``44.13%`` vs ``44.13``) within the
    same text: the more-specific form wins positional ties.
    """
    ratio = claim.get("ratio")
    v = claim.get("claimed_value")
    if v is None:
        return []
    try:
        f = float(v)
    except (TypeError, ValueError):
        return [str(v)]

    out: List[str] = []

    if ratio == "gross_margin":
        # Percentage forms (most natural for gross margin prose).
        _push_unique(out, f"{f:.2f}%")
        _push_unique(out, f"{f:.1f}%")
        _push_unique(out, f"{int(round(f))}%")
        # Bare numeric forms (table values, terse prose).
        _push_unique(out, f"{f:.2f}")
        _push_unique(out, f"{f:.1f}")
        _push_unique(out, f"{int(round(f))}")
        # Decimal forms (0.4413 etc). Some LLMs report as ratios.
        d = f / 100.0
        _push_unique(out, f"{d:.4f}")
        _push_unique(out, f"{d:.3f}")
        _push_unique(out, f"{d:.2f}")
    elif ratio == "current_ratio":
        _push_unique(out, f"{f:.4f}")
        _push_unique(out, f"{f:.3f}")
        _push_unique(out, f"{f:.2f}x")
        _push_unique(out, f"{f:.2f}×")  # unicode ×
        _push_unique(out, f"{f:.2f}")
        _push_unique(out, f"{f:.1f}")
    elif ratio == "accounting_equation":
        # Raw integer forms.
        n = int(round(f))
        _push_unique(out, f"${n:,}")
        _push_unique(out, f"{n:,}")
        _push_unique(out, str(n))
        # Billions form. Longer/more-specific decimals first so that
        # ``$106.618 billion`` binds before ``$106.6 billion`` at the
        # same prose position. Truncation (``int(b)``) is kept alongside
        # rounding because LLMs do both on non-negative totals
        # ("352.755" → "352" via truncation, "353" via rounding).
        billion_forms: List[str] = []
        if f >= 1e9:
            b = f / 1e9
            billion_forms = [
                f"{b:.3f}", f"{b:.2f}", f"{b:.1f}",
                str(int(round(b))), str(int(b)),
            ]
            for v in billion_forms:
                for tmpl in (f"${v} billion", f"{v} billion", f"${v}B"):
                    _push_unique(out, tmpl)
        # Millions form.
        if f >= 1e6:
            mm = f / 1e6
            mm_round = int(round(mm))
            _push_unique(out, f"${mm_round:,} million")
            _push_unique(out, f"${mm_round:,}M")
            _push_unique(out, f"{mm_round:,} million")
            # Bare form (e.g. ``$106,618``) for tables that put the
            # "in millions" unit in the column header. Must come AFTER
            # the suffix forms so ties go to the more specific match.
            _push_unique(out, f"${mm_round:,}")
            _push_unique(out, f"{mm_round:,}")
        # Bare billion forms last: they cover tables with "in billions" in
        # the header and prose equations that drop the unit (e.g. ``106.62
        # = 43.01 + 63.61``). Kept at the end so that short integer forms
        # like ``$106`` / ``106`` never match as substrings of longer
        # candidates (e.g., ``$106,618 million``) when both are present.
        for v in billion_forms:
            _push_unique(out, f"${v}")
            _push_unique(out, v)
    else:
        _push_unique(out, str(v))

    return out


def _warn_no_match(claim: Dict[str, Any], session_id: str = "") -> None:
    logger.warning(
        "wrap_claim_values: no candidate matched prose for claim",
        extra={
            "session_id": session_id or claim.get("session_id", ""),
            "claim_id": claim.get("claim_id", ""),
            "ratio": claim.get("ratio", ""),
            "ticker": claim.get("ticker", ""),
            "claimed_value": claim.get("claimed_value"),
        },
    )


def _find_first_match(text: str, candidates: Iterable[str]) -> Tuple[int, str]:
    """Return ``(pos, candidate)`` of earliest match, or ``(-1, "")``."""
    best_pos = -1
    best_cand = ""
    for cand in candidates:
        pos = text.find(cand)
        if pos == -1:
            continue
        if best_pos == -1 or pos < best_pos:
            best_pos = pos
            best_cand = cand
    return best_pos, best_cand


def wrap_claim_values(
    prose: str,
    claims: List[Dict[str, Any]],
    session_id: str = "",
) -> str:
    """Wrap every non-delimited occurrence of each claim's value in a span.

    For each claim, the earliest-appearing candidate string across all
    wrappable segments is chosen; every occurrence of *that* candidate is
    then wrapped. Delimited regions (code, math, HTML) are preserved.
    Claims with no matching candidate anywhere are left unwrapped and
    logged via a structured warning.
    """
    if not prose or not claims:
        return prose

    segments = _split_by_delimited(prose)

    # Seed idempotency set from any data-claim-id spans already present in
    # the input prose (e.g., re-running over an already-wrapped response).
    wrapped_ids = set(re.findall(r'data-claim-id="([^"]*)"', prose))

    for claim in claims:
        claim_id = claim.get("claim_id")
        if not claim_id or claim_id in wrapped_ids:
            continue

        cands = _candidates(claim)
        if not cands:
            _warn_no_match(claim, session_id)
            continue

        chosen = ""
        for seg in segments:
            if not seg[0]:
                continue
            _, cand = _find_first_match(seg[1], cands)
            if cand:
                chosen = cand
                break

        if not chosen:
            _warn_no_match(claim, session_id)
            continue

        span = f'<span data-claim-id="{claim_id}">{chosen}</span>'
        new_segments: List[List] = []
        for seg in segments:
            if not seg[0] or chosen not in seg[1]:
                new_segments.append(seg)
                continue
            parts = seg[1].split(chosen)
            for i, part in enumerate(parts):
                if i > 0:
                    new_segments.append([False, span])
                new_segments.append([True, part])
        segments = new_segments
        wrapped_ids.add(claim_id)

    return "".join(seg[1] for seg in segments)
