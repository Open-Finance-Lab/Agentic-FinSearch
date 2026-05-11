# Numbers + Ratios Layer (Layer 1): 3-Ratio Validate Button

**Date:** 2026-04-13
**Status:** Built and verified (30/30 unit + integration tests pass; benchmark 3/3 VERIFIED)
**Supersedes:** `2026-04-07-axiom-engine-design.md` (same problem, different architecture — kept for history)
**Scope:** Backend + Chrome extension. Minimum-viable implementation of Layer 1 from `knowledge/finsearch-four-layer-architecture.md`.

## Purpose

Implement the user-facing Validate primitive for Layer 1 of the FinSearch four-layer architecture: the user, not the model, triggers a deterministic proof that a numerical claim in an agent response matches XBRL-grounded ground truth. Scoped to three equations for the initial demo.

## What changes versus the 2026-04-07 spec

| Dimension | 2026-04-07 spec | This spec |
|---|---|---|
| Axioms | 1 (`A = L + E`) | 3 (accounting eq., gross margin, current ratio) |
| Claim capture | Parse Yahoo JSON inside tool wrapper | Agent calls `report_claim` MCP tool per ratio emitted |
| Validation trigger | Automatic (every tool output) | Lazy (user clicks Validate) |
| Ground truth | Live Yahoo Finance JSON | Local XBRL filings (SEC 10-K) |
| UI | None | Validate button + inline mismatch marking in Chrome extension |

The earlier spec's tool-wrapper intercept was the right primitive for an internal guardrail. This spec is the right primitive for *user-triggered certification*, which is what Layer 1 in the architecture doc actually asks for.

## The three equations

| # | Equation | Family | Demo question |
|---|---|---|---|
| 1 | `Assets = Liabilities + Equity` | Accounting identity | *"Summarize Tesla's Q4 2023 balance sheet."* |
| 2 | `Gross Margin = (Revenue − COGS) / Revenue` | Profitability | *"What was Apple's gross margin for FY2023?"* |
| 3 | `Current Ratio = Current Assets / Current Liabilities` | Liquidity | *"What is Microsoft's current ratio as of FY2023?"* |

All three resolve from existing local XBRL filings. No new filings required for the demo.

## Architecture

```
User asks demo question
  └─► Agent (thinking mode)
        ├─ calls MCP tools (Yahoo/XBRL) to get data
        ├─ emits narrative response containing ratio(s)
        └─ MUST also call report_claim(ratio, ticker, period, claimed_value, formula_inputs)
             └─► Claim registry (Django cache, session-scoped)

User clicks [Validate]
  └─► POST /api/axioms/validate/ {session_id}
        ├─ Pull all claims for session
        ├─ For each claim:
        │     resolver.fetch_ground_truth(ratio, ticker, period)
        │       └─► existing query_xbrl_filing on local filings
        │     engine.check_*(inputs, claimed_value)
        │     return {status, expected, actual, variance_pct, xbrl_source, formula}
        └─► Response array of per-claim results

Chrome extension
  ├─ Inline mismatch marks on the response (red underline)
  ├─ "Mathematically Verified" / "Mismatch (−X%)" chip
  └─ XBRL filing path appended to Sources popup
```

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Claim capture | `report_claim` MCP tool (structured) | User choice; avoids regex fragility and prompt-drift |
| Validation trigger | Lazy (user clicks) | Matches arch doc principle: "the user, not the model, triggers the proof" |
| Ground truth | Local XBRL filings via `query_xbrl_filing` | Deterministic, no network, publishable |
| Tolerance | `max(0.0001 × |expected|, 0.005)` | 0.01% relative OR 0.5 cent absolute, whichever larger; handles near-zero |
| Scope | Fail fast on unsupported ratios | Return SKIPPED, not SILENT — visible in the validate panel |
| Accounting identity | `A = L + TempEquity + E` (not `A = L + E`) | Tesla-type filers carry redeemable NCI outside both L and E — omitting TempEquity causes a spurious 0.227% FAILED on valid data. TempEquity defaults to 0 for filers without NCI (AAPL, MSFT). |
| NOT_APPLICABLE status | New status distinct from SKIPPED | Current ratio does not apply to unclassified balance sheets (banks, insurance, REITs). Detected deterministically: if a filing lacks `AssetsCurrent` entirely, the BS is unclassified. Keeps the audit log honest — data-extraction failure is a bug; metric-not-defined is a correct refusal. |
| Claim registry storage | Django cache (same as `UnifiedContextManager`) | Re-uses existing infra; TTL 1h |

## Components

### 1. `Main/backend/axioms/engine.py` (~90 lines, pure Python)

```python
@dataclass
class RatioResult:
    status: str                # "VERIFIED" | "FAILED" | "SKIPPED"
    ratio: str                 # "accounting_equation" | "gross_margin" | "current_ratio"
    expected: float | None     # computed from ground truth
    actual: float | None       # claimed by agent
    variance_pct: float | None
    message: str
    formula: str               # human-readable formula

def check_accounting_equation(assets, liabilities, equity, claimed_assets=None) -> RatioResult
def check_gross_margin(revenue, cogs, claimed_margin_pct) -> RatioResult
def check_current_ratio(current_assets, current_liabilities, claimed_ratio) -> RatioResult

TOLERANCE = lambda expected: max(1e-4 * abs(expected), 5e-3)
```

### 2. `Main/backend/axioms/resolver.py` (~80 lines)

Maps `(ratio, ticker, period)` to XBRL facts. The `RATIO_TAG_MAP` constant is the domain-knowledge surface (tag preference orders, unit conventions).

```python
RATIO_TAG_MAP = {
    "accounting_equation": {
        "assets":      ["Assets"],
        "liabilities": ["Liabilities"],
        "equity":      ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                        "StockholdersEquity"],
    },
    "gross_margin": {
        "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
        "cogs":    ["CostOfGoodsAndServicesSold", "CostOfRevenue"],
    },
    "current_ratio": {
        "current_assets":      ["AssetsCurrent"],
        "current_liabilities": ["LiabilitiesCurrent"],
    },
}

def fetch_ground_truth(ratio, ticker, period) -> dict[str, float]
def xbrl_source_url(ticker) -> str     # path to the local filing used
```

### 3. `Main/backend/axioms/registry.py` (~40 lines)

Session-keyed claim storage via Django cache:
```python
def add_claim(session_id: str, claim: dict) -> None
def get_claims(session_id: str) -> list[dict]
def clear_claims(session_id: str) -> None
```

Claim shape:
```json
{
  "ratio": "gross_margin",
  "ticker": "AAPL",
  "period": "2023-09-30",
  "claimed_value": 43.3,
  "formula_inputs": {"revenue": 383285000000, "cogs": 214137000000},
  "emitted_at": "2026-04-13T22:30:00Z"
}
```

### 4. `Main/backend/axioms/tool.py` — native `function_tool`, NOT an MCP server

Pragmatic deviation from the original plan: `report_claim` is a native `agents.function_tool` (same pattern as `calculator_tool`, `url_tools`), bound to the session via a closure in `get_axiom_tools(session_id)`. Spinning up a new stdio MCP server just to write to the Django cache would be pure overhead. `session_id` is threaded through `create_fin_agent(..., session_id=...)` and closed-over inside the tool factory.

Implication: the registry is cleared at the start of each new agent response (in `datascraper._create_agent_response_async` and `create_agent_response_stream`) so the registry always reflects only the most recent response's ratios — this is what makes the per-response Validate button visibility determinate.

### 5. `Main/backend/api/views.py` (two new endpoints)

```
POST /api/axioms/validate/
body: {"session_id": "..."}
response: {
  "claims": [
    {
      "ratio": "gross_margin", "ticker": "AAPL", "period": "2023-09-30",
      "status": "VERIFIED" | "FAILED" | "SKIPPED" | "NOT_APPLICABLE",
      "expected": 44.13, "actual": 44.13, "variance_pct": 0.003,
      "xbrl_source": "mcp_server/xbrl/filings/aapl-20230930.xml",
      "formula": "Gross Margin = (Revenue − COGS) / Revenue",
      "message": "..."
    }
  ],
  "summary": {"total": N, "VERIFIED": n, "FAILED": n, "SKIPPED": n, "NOT_APPLICABLE": n}
}

GET  /api/axioms/has_claims/?session_id=...
response: {"session_id": "...", "has_claims": true|false, "count": N}
```

Routed in `django_config/urls.py`. CSRF-exempt, same auth shape as other `/api/` endpoints. The `has_claims` endpoint gates the per-response Validate button in the Chrome extension (the button only appears when the response emitted at least one ratio claim).

### 6. Prompt additions (`Main/backend/prompts/core.md`)

One new section `RATIO CLAIMS`:

> When your response contains any of these ratios — balance sheet equality (A = L + E), gross margin, or current ratio — you MUST also call `report_claim` with the ticker, period (YYYY-MM-DD of fiscal period end), claimed value, and the exact numerical inputs you used. This enables deterministic verification on user request.

### 7. Chrome extension (`Main/frontend`)

- Validate button in the response toolbar (next to existing actions)
- On click: POST `/api/axioms/validate/` with current `session_id`
- Render results: per-claim chip appended below the response (`✓ Verified` / `✗ Mismatch −2.1%` / `— Skipped`)
- On mismatch: highlight the claimed number inline in red where possible (DOM text match)
- Append XBRL source paths to the Sources popup under a new "Ground Truth" subsection

## Data Flow

```
(1) User query      → Agent
(2) Agent tool loop → MCP Yahoo/XBRL tools (data)
(3) Agent response  → narrative + report_claim(...) calls
(4) Registry        ← claims stored keyed by session_id
(5) [Validate]      → POST /api/axioms/validate/
(6) Resolver        → query_xbrl_filing for each required tag
(7) Engine          → check_* functions (pure Python)
(8) Response        → per-claim verdicts
(9) Extension       → inline marks + XBRL in Sources popup
```

## Testing

- `tests/test_axioms.py` — engine unit tests (exact, tolerance, failure, near-zero, skipped)
- `tests/test_axiom_resolver.py` — resolver against AAPL/MSFT/TSLA filings (end-to-end fact extraction)
- `tests/test_axiom_integration.py` — simulate `report_claim` → `/api/axioms/validate/` full path
- `axioms/benchmark.py` — run each of the 3 demo questions, record VERIFIED pass/fail, emit a markdown table

## Out of scope (deferred)

- Automatic (non-user-triggered) per-claim validation during streaming (Layer 2)
- Long-form report tagging (Layer 2)
- Compliance-as-contract enforcement (Layer 3)
- Multi-period ratios requiring averaging (ROE, ROA)
- Cross-source validation (Yahoo vs XBRL reconciliation)
- Authority: SEC EDGAR live fetching (local filings only for demo)

## Files changed

| Action | File | Notes |
|---|---|---|
| Create | `Main/backend/axioms/__init__.py` | |
| Create | `Main/backend/axioms/engine.py` | pure Python |
| Create | `Main/backend/axioms/resolver.py` | USER contributes `RATIO_TAG_MAP` |
| Create | `Main/backend/axioms/registry.py` | Django cache-backed |
| Create | `Main/backend/axioms/benchmark.py` | demo artifact |
| Create | `Main/backend/mcp_server/axioms/__init__.py` | |
| Create | `Main/backend/mcp_server/axioms/server.py` | `report_claim` tool |
| Modify | `Main/backend/mcp_client/mcp_manager.py` | register axioms server |
| Modify | `Main/backend/api/views.py` | `validate_claims` view |
| Modify | `Main/backend/django_config/urls.py` | route |
| Modify | `Main/backend/prompts/core.md` | `RATIO CLAIMS` section |
| Create | `Main/backend/tests/test_axioms.py` | |
| Create | `Main/backend/tests/test_axiom_resolver.py` | |
| Create | `Main/backend/tests/test_axiom_integration.py` | |
| Modify | `Main/frontend/...` | Validate button + inline marks |
