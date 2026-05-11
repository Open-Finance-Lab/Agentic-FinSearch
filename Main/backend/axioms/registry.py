"""Session-scoped claim registry.

The agent calls the `report_claim` MCP tool each time it emits a supported
ratio; claims are stored here keyed by session_id. When the user clicks
Validate, the API view pulls all claims for the session and runs them
through the resolver + engine.

Backed by Django's cache framework (same substrate as UnifiedContextManager),
with a 1-hour TTL. No persistence beyond cache; that's intentional — claims
are tied to a conversational session, not audit history.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from django.core.cache import cache

_KEY_PREFIX = "axiom_claims:"
_TTL_SECONDS = 3600  # 1 hour

# claim_id is interpolated into an HTML attribute (data-claim-id="...")
# and later used as a CSS selector, so restrict to a conservative charset
# that can't close the attribute or break the selector. Anything else in
# the raw inputs (ticker / period / ratio) is replaced with a single '_'.
_CLAIM_ID_SAFE = re.compile(r"[^A-Za-z0-9_\-.]")


def _key(session_id: str) -> str:
    return f"{_KEY_PREFIX}{session_id}"


def _make_claim_id(claim: Dict[str, Any], index: int) -> str:
    raw = "{ratio}-{ticker}-{period}-{n}".format(
        ratio=claim.get("ratio", "ratio"),
        ticker=claim.get("ticker", ""),
        period=claim.get("period", ""),
        n=index,
    )
    return _CLAIM_ID_SAFE.sub("_", raw)


def add_claim(session_id: str, claim: Dict[str, Any]) -> None:
    """Append a claim to the session's claim list.

    Attaches a ``claim_id`` (``{ratio}-{ticker}-{period}-{n}``) and always
    passes it through the charset sanitizer so hostile input in ratio /
    ticker / period can't break out of a ``data-claim-id`` attribute or
    CSS selector downstream.
    """
    if not session_id:
        return
    claim = dict(claim)
    claim.setdefault("emitted_at", datetime.now(timezone.utc).isoformat())
    claims = cache.get(_key(session_id), []) or []
    claim_id = claim.get("claim_id") or _make_claim_id(claim, len(claims))
    claim["claim_id"] = _CLAIM_ID_SAFE.sub("_", str(claim_id))
    claims.append(claim)
    cache.set(_key(session_id), claims, _TTL_SECONDS)


def get_claims(session_id: str) -> List[Dict[str, Any]]:
    """Return all claims recorded for the session (oldest first)."""
    if not session_id:
        return []
    return cache.get(_key(session_id), []) or []


def clear_claims(session_id: str) -> None:
    if not session_id:
        return
    cache.delete(_key(session_id))
