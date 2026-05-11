# Axiom Engine: Deterministic Financial Validation

**Date:** 2026-04-07
**Status:** Approved
**Scope:** Minimum dev, backend only, no UI changes

## Purpose

Add a deterministic validation layer that checks mathematical relationships between financial numbers before the LLM reasons about them. First axiom: `Assets = Liabilities + Shareholders' Equity`. This positions FinSearch as certification infrastructure for financial AI agents, not a feature addition.

## Architecture Decision

**Approach:** MCP Tool Wrapper intercept (Approach 1 of 3 evaluated).

The axiom check runs as a code-enforced post-execution hook inside `tool_wrapper.py`. When `get_stock_financials` returns balance sheet data, the wrapper:
1. Parses the Yahoo Finance JSON payload already in the tool output
2. Extracts Assets, Liabilities, and Equity from the structured data
3. Runs the axiom check on the live data (pure Python, deterministic)
4. Appends a structured annotation to the tool output before the agent sees it

The axiom validates the data the agent is actually working with, not a separate data source. Local XBRL filings are used exclusively in the benchmark script to prove the axiom against authoritative SEC data.

**Why this approach over alternatives:**
- Dedicated Skill (Approach 2) relies on the LLM choosing to call a validation tool, reintroducing the tool hallucination problem we fixed in April 2026.
- Post-Response Middleware (Approach 3) extracts numbers from free-text LLM output, which is fragile.
- Tool Wrapper intercept is code-enforced: no LLM in the validation path. The SOP is guaranteed by the code, not by prompt compliance.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Validation point | Tool output (before LLM) | Catches data issues before reasoning; existing `validate_numerical_accuracy()` already handles post-response number checking |
| Failure behavior | Flag and force disclosure | Inject warning into tool result text so LLM must disclose discrepancy; retry logic deferred |
| Data source (live) | Yahoo Finance JSON payload | Validates the data the agent actually uses; no temporal mismatch; no file I/O |
| Data source (benchmark) | Local XBRL files (SEC filings) | Proves axiom against authoritative SEC data; publishable artifact |
| Demo scope | 3-5 companies, 1 period each | Enough to demonstrate cross-sector generalization; manageable file count |

## Components

### 1. Axiom Library (`Main/backend/axioms/engine.py`)

Pure Python module, ~50 lines, zero external dependencies.

**`AxiomResult` dataclass:**
```python
@dataclass
class AxiomResult:
    status: str          # "VERIFIED" | "FAILED" | "SKIPPED"
    axiom_name: str      # "balance_sheet_equality"
    message: str         # Human-readable summary
    expected: float | None
    actual: float | None
    variance_pct: float | None
    source_file: str | None
```

**`check_balance_sheet_from_json(json_str: str) -> AxiomResult`:**
- Parses the Yahoo Finance JSON payload from the tool output
- Extracts from the most recent balance sheet period:
  - `"Total Assets"` (A)
  - `"Total Liabilities Net Minority Interest"` (L)
  - `"Total Equity Gross Minority Interest"` (E)
- Falls back to `"Stockholders Equity"` for E only if gross field is absent
- Checks `A == L + E` within 0.01% tolerance
- Returns `VERIFIED`, `FAILED`, or `SKIPPED` (if fields not found in JSON)

**Important: Yahoo Finance field name semantics.** The liabilities field is "net minority interest" (excludes minority interest). To balance, the equity field must be "gross minority interest" (includes minority interest). Using `"Stockholders Equity"` (parent-only) with `"Total Liabilities Net Minority Interest"` produces false failures for companies with non-controlling interests (e.g., Tesla shows 0.53% variance with the wrong pairing).

**`check_balance_sheet_from_xbrl(facts: List[Dict]) -> AxiomResult`:**
- Used by benchmark only, not the live path
- Filters facts for the most recent non-dimensional instant values
- Tag preference order for equity: `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`, then `StockholdersEquity`
- Checks `Assets == Liabilities + Equity` within 0.01% tolerance

**`annotate_with_axioms(tool_name: str, kwargs: dict, text_output: str) -> str`:**
- Entry point called from tool wrapper
- Only triggers for `get_stock_financials` when result contains `"balance_sheet"`
- Calls `check_balance_sheet_from_json(text_output)`, formats annotation, appends
- On VERIFIED: `[AXIOM VERIFIED] Retrieved balance sheet is internally consistent: Assets ($X) = Liabilities ($Y) + Equity ($Z).`
- On FAILED: `[AXIOM FAILED] Retrieved balance sheet has a discrepancy: Assets ($X) != Liabilities ($Y) + Equity ($Z). Variance: X%. You MUST disclose this discrepancy to the user.`
- On SKIPPED: `[AXIOM SKIPPED] Could not extract balance sheet fields for verification.`

### 2. Tool Wrapper Modification (`Main/backend/mcp_client/tool_wrapper.py`)

~15 lines changed. The `annotate_with_axioms` import is added at the top of `tool_wrapper.py` (static, not inside the generated code). The generated function code is modified to call it after text assembly, before returning. The function reference is passed into the local scope via `local_vars`, same pattern as `execute_fn`.

```python
# At top of tool_wrapper.py (static import)
from axioms.engine import annotate_with_axioms

# Added to local_vars dict (line ~130)
"annotate_with_axioms": annotate_with_axioms,

# In generated function code, after text assembly, before return
text_result = annotate_with_axioms(tool_name_var, kwargs, "\n".join(text_output))
return text_result
```

The annotation function handles all conditional logic internally (tool name check, balance sheet data detection, filing resolution). The wrapper calls it for every tool, but it returns the input unchanged for non-financial tools.

### 3. Parser Extension (`Main/backend/mcp_server/xbrl/parser.py`)

Add new entries to `COMPANY_ALIASES` (needed for benchmark and existing `query_xbrl_filing` MCP tool):
```python
COMPANY_ALIASES = {
    "apple": "aapl",
    "microsoft": "msft",
    "tesla": "tsla",
    "jpmorgan": "jpm",
    "jp morgan": "jpm",
    "berkshire": "brk",
    "berkshire hathaway": "brk",
}
```

### 4. Local XBRL Filings

**Existing:** `aapl-20230930.xml`, `msft-20230630.xml`, `tsla-20231231.xml`
**To add:** JPMorgan 10-K, Berkshire Hathaway 10-K (downloaded from SEC EDGAR)

All filings stored at `Main/backend/mcp_server/xbrl/filings/`.

## Data Flow

```
User query ("What's Apple's balance sheet?")
  |
  v
Planner -> FinancialStatementsSkill
  |  tools_allowed = ["get_stock_financials", "get_earnings_info", "calculate"]
  v
Agent calls get_stock_financials(ticker="AAPL")
  |
  v
MCP Yahoo Finance -> JSON with balance_sheet data
  |
  v
tool_wrapper.py: assemble text from MCP result
  |
  v
annotate_with_axioms("get_stock_financials", {"ticker": "AAPL"}, text)
  |  1. json.loads(text) -> extract balance_sheet dict
  |  2. Get most recent period's Total Assets, Total Liabilities, Total Equity
  |  3. check: A == L + E within 0.01% tolerance
  |  4. Append annotation to text
  v
Agent receives: original JSON + "[AXIOM VERIFIED] Retrieved balance sheet is internally consistent: ..."
  |
  v
LLM formats response, includes verification status
  |
  v
User sees proof trail in response
```

**Key properties:**
- Deterministic: axiom check is pure Python math, no LLM involved
- Validates live data: checks the Yahoo Finance payload the agent is actually using, not a separate data source
- Non-blocking on skip: missing fields means SKIPPED, response still goes through
- No file I/O: parses JSON string already in memory
- No temporal mismatch: verification applies to the exact data being presented

## Testing Strategy

### Unit Tests (`Main/backend/tests/test_axioms.py`)
- `check_balance_sheet_from_json()` with synthetic Yahoo JSON: exact match, tolerance match, clear failure, missing fields, minority interest edge case (Stockholders Equity vs Total Equity Gross Minority Interest)
- `check_balance_sheet_from_xbrl()` with synthetic XBRL facts: exact match, failure, missing tags, dimensional filtering, tag preference order (compound equity tag vs simple)
- `annotate_with_axioms()` for VERIFIED, FAILED, SKIPPED outcomes

### Integration Test (`Main/backend/tests/test_axiom_integration.py`)
- Full tool wrapper flow: mock `execute_fn` returning real-format `get_stock_financials` JSON, verify annotation appears
- Non-financial tools pass through unmodified

### Benchmark (`Main/backend/axioms/benchmark.py`)
Standalone script that validates XBRL filings (SEC ground truth) using `check_balance_sheet_from_xbrl()`. This is the publishable artifact proving the axiom against authoritative data:
```
Axiom Benchmark: Balance Sheet Equality (A = L + E)
Source: Local XBRL filings (SEC 10-K)
====================================================
AAPL (2023-09-30)  VERIFIED  Assets: $352.6B = $290.4B + $62.1B  variance: 0.000%
MSFT (2023-06-30)  VERIFIED  Assets: $411.9B = $205.7B + $206.2B  variance: 0.000%
TSLA (2023-12-31)  VERIFIED  Assets: $106.6B = $43.0B + $63.6B   variance: 0.000%
JPM  (20XX-XX-XX)  VERIFIED  ...
BRK  (20XX-XX-XX)  VERIFIED  ...
====================================================
Pass rate: 5/5 (100%)
```

The benchmark uses `check_balance_sheet_from_xbrl()` (XBRL tag extraction), while the live tool wrapper uses `check_balance_sheet_from_json()` (Yahoo JSON extraction). Both check the same axiom; they differ only in data extraction.

## Files Changed

| Action | File | Lines |
|--------|------|-------|
| Create | `Main/backend/axioms/__init__.py` | 0 |
| Create | `Main/backend/axioms/engine.py` | ~50 |
| Create | `Main/backend/axioms/benchmark.py` | ~40 |
| Modify | `Main/backend/mcp_client/tool_wrapper.py` | ~15 added |
| Modify | `Main/backend/mcp_server/xbrl/parser.py` | ~3 added |
| Add | `Main/backend/mcp_server/xbrl/filings/jpm-*.xml` | binary |
| Add | `Main/backend/mcp_server/xbrl/filings/brk-*.xml` | binary |
| Create | `Main/backend/tests/test_axioms.py` | ~80 |
| Create | `Main/backend/tests/test_axiom_integration.py` | ~50 |

**Total new code:** ~110 lines across 3 Python files, ~15 lines modified in 2 existing files.

**What does NOT change:**
- No new MCP server or tool registration
- No planner or skill changes
- No prompt modifications
- No frontend/Chrome extension changes
- No changes to `datascraper.py`
- No changes to `views.py`

## Future Extensions (Not in Scope)

- Cross-source validation: compare Yahoo Finance values against local XBRL filing for the same period (the natural next step, combining both extraction paths)
- Retry logic on axiom failure (fall back to SEC-EDGAR data source)
- Additional axioms: Income Statement Identity, Cash Flow Reconciliation, Put-Call Parity
- Pre-extracted JSON cache for faster XBRL filing lookups
- Chrome extension "Mathematically Verified" badge
- Streaming response annotation support
