"""Layer 1 Validate benchmark: 3 demo questions × 3 ratios.

Runs the resolver + engine end-to-end over the local XBRL filings and
prints a publishable markdown table. Simulates the exact claims the
agent is expected to emit for each demo question, allowing the benchmark
to run without spinning up an LLM. This is the numerical artifact that
accompanies the Layer 1 paper.

Usage:
    cd Main/backend
    python -m axioms.benchmark
"""
from __future__ import annotations

from typing import Any, Dict, List

from axioms import (
    ACCOUNTING_EQUATION,
    CURRENT_RATIO,
    GROSS_MARGIN,
    validate_claim,
)

# Each case is a demo question paired with the claim a compliant agent would emit.
# Claimed values are accurate to 2 decimals — the precision the prompt requires.
CASES: List[Dict[str, Any]] = [
    {
        "question": "Summarize Tesla's Q4 2023 balance sheet.",
        "ratio": ACCOUNTING_EQUATION,
        "ticker": "TSLA",
        "period": "2023-12-31",
        "claimed_value": 106618e6,
    },
    {
        "question": "What was Apple's gross margin for FY2023?",
        "ratio": GROSS_MARGIN,
        "ticker": "AAPL",
        "period": "2023-09-30",
        "claimed_value": 44.13,
    },
    {
        "question": "What is Microsoft's current ratio as of FY2023?",
        "ratio": CURRENT_RATIO,
        "ticker": "MSFT",
        "period": "2023-06-30",
        "claimed_value": 1.77,
    },
]


def _fmt_var(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.4f}%"


def main() -> int:
    rows = [{**case, **validate_claim(case)} for case in CASES]

    print("# Layer 1 Validate Benchmark")
    print()
    print("Source: local SEC XBRL filings committed under `mcp_server/xbrl/filings/`.")
    print("Axiom engine: `axioms/engine.py`. Ground-truth resolver: `axioms/resolver.py`.")
    print("Tolerance: max(0.01% of |expected|, 0.005 absolute).")
    print()
    print("| Demo Question | Ratio | Ticker | Period | Claim | Ground Truth | Variance | Status |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        gt = r.get("expected")
        gt_str = f"{gt:,.4f}" if gt is not None else "—"
        claim_str = f"{r['claimed_value']:,.4f}"
        print(
            f"| {r['question']} | {r['ratio']} | {r['ticker']} | {r['period']} | "
            f"{claim_str} | {gt_str} | {_fmt_var(r.get('variance_pct'))} | **{r['status']}** |"
        )

    pass_count = sum(1 for r in rows if r["status"] == "VERIFIED")
    total = len(rows)
    print()
    print(f"**Pass rate: {pass_count}/{total} ({100 * pass_count / total:.0f}%)**")

    return 0 if pass_count == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
