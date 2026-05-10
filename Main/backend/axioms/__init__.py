from axioms.engine import (
    RatioResult,
    check_accounting_equation,
    check_current_ratio,
    check_gross_margin,
)

# Supported ratio names — single source of truth. Prefer referencing these
# constants over bare strings so a typo raises at import, not silently skips.
ACCOUNTING_EQUATION = "accounting_equation"
GROSS_MARGIN = "gross_margin"
CURRENT_RATIO = "current_ratio"
SUPPORTED_RATIOS = (ACCOUNTING_EQUATION, GROSS_MARGIN, CURRENT_RATIO)

# Dispatch: (check_fn, claimed-value kwarg name) per ratio.
_DISPATCH = {
    ACCOUNTING_EQUATION: (check_accounting_equation, "claimed_assets"),
    GROSS_MARGIN:        (check_gross_margin,        "claimed_margin_pct"),
    CURRENT_RATIO:       (check_current_ratio,       "claimed_ratio"),
}


def validate_claim(claim: dict) -> dict:
    """Resolve ground truth + run the engine for a single claim, returning the
    enriched dict that the /api/axioms/validate/ endpoint and the benchmark
    share. Dispatch logic lives here so HTTP and CLI callers stay thin.
    """
    # Imported lazily so axioms.engine can be used without pulling in the XBRL
    # parser (and its sys.path gymnastics) during tests that only exercise math.
    from axioms.resolver import (
        check_applicability,
        fetch_ground_truth,
        xbrl_source_url,
    )

    ratio = claim.get("ratio")
    ticker = claim.get("ticker", "")
    period = claim.get("period", "")
    claimed_value = claim.get("claimed_value")
    source = xbrl_source_url(ticker)

    if ratio not in _DISPATCH:
        return {**claim, "status": "SKIPPED",
                "message": f"Unknown ratio '{ratio}'", "xbrl_source": source}

    not_applicable = check_applicability(ratio, ticker)
    if not_applicable:
        return {**claim, "status": "NOT_APPLICABLE",
                "message": not_applicable["reason"], "xbrl_source": source}

    check_fn, claimed_key = _DISPATCH[ratio]
    inputs = fetch_ground_truth(ratio, ticker, period)
    claimed_kwargs = (
        {claimed_key: float(claimed_value)} if claimed_value is not None else {}
    )
    result = check_fn(**inputs, **claimed_kwargs)

    return {
        **claim,
        "status": result.status,
        "expected": result.expected,
        "actual": result.actual,
        "variance_pct": result.variance_pct,
        "formula": result.formula,
        "message": result.message,
        "xbrl_source": source,
    }


def validate_session(session_id: str) -> dict:
    """Return the full validate-endpoint response payload for a session."""
    from axioms.registry import get_claims

    counts = {"VERIFIED": 0, "FAILED": 0, "SKIPPED": 0, "NOT_APPLICABLE": 0}
    results = []
    for claim in get_claims(session_id):
        r = validate_claim(claim)
        results.append(r)
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "session_id": session_id,
        "claims": results,
        "summary": {"total": len(results), **counts},
    }


__all__ = [
    "RatioResult",
    "check_accounting_equation",
    "check_gross_margin",
    "check_current_ratio",
    "SUPPORTED_RATIOS",
    "ACCOUNTING_EQUATION",
    "GROSS_MARGIN",
    "CURRENT_RATIO",
    "validate_claim",
    "validate_session",
]
