"""Integration test for the full Validate flow: register claims, then run
the shared `validate_session()` pipeline that `/api/axioms/validate/` and
`benchmark.py` both use. Bypasses Django by stubbing the cache module;
conftest.py handles the mcp_server shadowing workaround.
"""
from unittest.mock import patch

from axioms import validate_session


class _DictCache:
    """Minimal Django-cache-compatible stub backed by a dict."""
    def __init__(self):
        self._store = {}
    def get(self, key, default=None):
        return self._store.get(key, default)
    def set(self, key, value, timeout=None):
        self._store[key] = value
    def delete(self, key):
        self._store.pop(key, None)


def test_full_flow_three_verified_claims():
    """Register 3 valid claims (A=L+E, gross margin, current ratio) across
    AAPL/MSFT/TSLA, then run the validate pipeline end-to-end."""
    stub_cache = _DictCache()
    with patch("axioms.registry.cache", stub_cache):
        from axioms.registry import add_claim

        sid = "test_sess_1"

        # AAPL balance sheet
        add_claim(sid, {
            "ratio": "accounting_equation",
            "ticker": "AAPL",
            "period": "2023-09-30",
            "claimed_value": 352583e6,
            "formula_inputs": {"assets": 352583e6, "liabilities": 290437e6, "equity": 62146e6},
        })
        # MSFT gross margin
        add_claim(sid, {
            "ratio": "gross_margin",
            "ticker": "MSFT",
            "period": "2023-06-30",
            "claimed_value": 68.92,
            "formula_inputs": {"revenue": 211915e6, "cogs": 65863e6},
        })
        # TSLA current ratio
        add_claim(sid, {
            "ratio": "current_ratio",
            "ticker": "TSLA",
            "period": "2023-12-31",
            "claimed_value": 1.7259,
            "formula_inputs": {"current_assets": 49616e6, "current_liabilities": 28748e6},
        })

        response = validate_session(sid)
        results = response["claims"]

    assert len(results) == 3
    assert [r["status"] for r in results] == ["VERIFIED", "VERIFIED", "VERIFIED"]
    # Each result carries the XBRL source path
    for r in results:
        assert "xbrl" in r["xbrl_source"].lower()
    # Each result carries a backend-generated claim_id so the in-text
    # marking layer can join validation status back to the prose span.
    claim_ids = [r.get("claim_id") for r in results]
    assert all(cid for cid in claim_ids), f"missing claim_id: {claim_ids}"
    assert len(set(claim_ids)) == 3, f"non-unique claim_ids: {claim_ids}"


def test_flow_detects_hallucinated_gross_margin():
    """Agent claims 50% but truth is 44.13% → must FAIL."""
    stub_cache = _DictCache()
    with patch("axioms.registry.cache", stub_cache):
        from axioms.registry import add_claim
        sid = "test_sess_2"
        add_claim(sid, {
            "ratio": "gross_margin",
            "ticker": "AAPL",
            "period": "2023-09-30",
            "claimed_value": 50.0,
            "formula_inputs": {"revenue": 383285e6, "cogs": 214137e6},
        })
        response = validate_session(sid)
        results = response["claims"]

    assert len(results) == 1
    assert results[0]["status"] == "FAILED"
    assert results[0]["variance_pct"] > 10.0


def test_flow_empty_session_returns_empty():
    stub_cache = _DictCache()
    with patch("axioms.registry.cache", stub_cache):
        response = validate_session("no_such_session")
    assert response["claims"] == []
    assert response["summary"]["total"] == 0


def test_claim_id_is_charset_sanitized():
    """``claim_id`` is interpolated into an HTML attribute and later
    used as a CSS selector. Any non-``[A-Za-z0-9_\\-.]`` char in the
    source fields (ticker/period/ratio) must be replaced so the id
    cannot break out of the attribute or the selector.
    """
    stub_cache = _DictCache()
    with patch("axioms.registry.cache", stub_cache):
        from axioms.registry import add_claim, get_claims

        sid = "test_sess_sanitize"
        # A hostile period string containing quote, angle bracket, and
        # HTML-ish junk — the kind of input a rogue tool call could
        # smuggle in.
        add_claim(sid, {
            "ratio": "gross_margin",
            "ticker": "AAPL",
            "period": '"><script>alert(1)</script>',
            "claimed_value": 44.13,
            "formula_inputs": {"revenue": 1.0, "cogs": 0.5},
        })
        claims = get_claims(sid)

    assert len(claims) == 1
    cid = claims[0]["claim_id"]
    # No characters that could close the attribute or the selector.
    for bad in ('"', "<", ">", "/", " ", "'", "(", ")", "`"):
        assert bad not in cid, f"sanitized claim_id leaked {bad!r}: {cid}"
    # Safe charset only.
    import re
    assert re.fullmatch(r"[A-Za-z0-9_\-.]+", cid), cid
