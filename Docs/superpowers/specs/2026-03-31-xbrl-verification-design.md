# XBRL Filing Verification System

**Date:** 2026-03-31
**Status:** Approved
**Target:** April 1, 2026 demo

## Problem

When companies acquire shell companies, acquisition contracts reference financial data (revenue, assets, tax rates, debt) from the target's filings. Today, verifying every number in a contract against the original XBRL filing is a manual, error-prone process.

Our agent already tags financial text with correct XBRL tags (anti-hallucination system shipped Mar 29). The next step: use those tags to look up the actual reported value in the company's XBRL filing and compare it against the contract's claim.

## Use Case

User provides:
- A **company name** (e.g., "Apple")
- A **sentence or paragraph** from a contract containing financial data with time frames

Agent returns a verification table:

```
| Claim | Value | XBRL Tag | Filing Value | Status |
|-------|-------|----------|-------------|--------|
| Effective tax rate | 14.7% | EffectiveIncomeTaxRateContinuingOperations | 14.7% | verified |
| Total revenue | $400B | RevenueFromContractWithCustomerExcludingAssessedTax | $383.3B | mismatch |
```

## Architecture

### Verification Pipeline (4 steps)

```
Input: company name + sentence with financial claims
         |
Step 1: EXTRACT - Agent identifies every data claim (value + what it represents)
         |
Step 2: TAG - Agent calls lookup_xbrl_tags() for each claim, selects best match,
         validates with validate_xbrl_tag()
         |
Step 3: VERIFY - Agent calls query_xbrl_filing(company, tag_name) for each tag,
         receives the value from the actual XBRL filing
         |
Step 4: COMPARE - Agent compares document values vs filing values,
         outputs verification table with checkmark/cross status
```

### Filing Storage

Pre-loaded XBRL filings stored locally:

```
mcp_server/xbrl/filings/
  aapl-20230930.xml    # Apple 10-K, FY ending 2023-09-30
  msft-20230630.xml    # Microsoft 10-K, FY ending 2023-06-30
  tsla-20231231.xml    # Tesla 10-K, FY ending 2023-12-31
```

Company name resolution: agent provides a company name (e.g., "apple"), tool scans filenames for a case-insensitive match on the ticker/company prefix.

### iXBRL Parsing

The filings are Inline XBRL (iXBRL) — HTML documents with embedded XBRL data. Key elements:

- `<ix:nonFraction>` — numeric values with attributes:
  - `name`: XBRL tag (e.g., `us-gaap:EffectiveIncomeTaxRateContinuingOperations`)
  - `scale`: power-of-10 multiplier (e.g., scale="6" means millions, scale="-2" means percent-as-decimal)
  - `contextRef`: links to a `<xbrli:context>` block defining the reporting period
  - `unitRef`: links to a `<xbrli:unit>` block (USD, shares, pure)
  - `sign`: "-" for negative values
  - `format`: display formatting hint (e.g., `ixt:num-dot-decimal`)
- `<xbrli:context>` — defines entity + period (duration with start/end dates, or instant)
- `<xbrli:unit>` — defines measurement unit (iso4217:USD, xbrli:shares, xbrli:pure)

**Value computation:** `actual_value = display_text * 10^scale`

Examples from Apple FY2023 filing:
- `14.7` with scale="-2" = 0.147 (14.7% effective tax rate)
- `383,285` with scale="6" = 383,285,000,000 ($383.3B revenue)

### Context Period Matching

Contexts can be:
- **Duration:** `<xbrli:startDate>2022-09-25</xbrli:startDate><xbrli:endDate>2023-09-30</xbrli:endDate>` (full fiscal year)
- **Instant:** `<xbrli:instant>2023-09-30</xbrli:instant>` (balance sheet date)

Many tags have multiple contexts (different fiscal years, quarterly breakdowns, dimensional disaggregations). The tool returns all matches and lets the agent select the appropriate period based on the contract's time frame.

Contexts with dimensional segments (e.g., by geographic region or product line) are flagged so the agent can distinguish aggregate vs. disaggregated values.

## Changes Required

### 1. New file: `mcp_server/xbrl/parser.py`

iXBRL parser using Python's built-in `xml.etree.ElementTree`. No external dependencies.

Responsibilities:
- Parse iXBRL HTML/XML files
- Extract all `<ix:nonFraction>` elements into structured records
- Resolve `contextRef` to period dates
- Resolve `unitRef` to unit labels
- Compute actual values using scale attribute
- Handle text cleaning (commas, em-dashes for zero, format attributes)
- Company-to-file mapping via filename pattern matching
- Cache parsed filings in memory for fast repeated queries

### 2. Modified file: `mcp_server/xbrl/server.py`

Add one new tool: `query_xbrl_filing`

```
Tool: query_xbrl_filing
Inputs:
  - company: string (required) - company name or ticker (e.g., "apple", "AAPL")
  - tag_name: string (required) - XBRL tag name (e.g., "EffectiveIncomeTaxRateContinuingOperations")
Returns:
  - All matching facts: value, unit, period (start/end or instant), dimensional context
  - Or "NOT FOUND" with suggestion to check tag name
```

Added to `handle_list_tools()` and `handle_call_tool()` in the existing MCP server.

### 3. Modified file: `prompts/core.md`

Add `query_xbrl_filing` to the canonical tool registry. Add a `VERIFICATION` section with workflow instructions:

- When asked to verify financial data, follow the 4-step pipeline
- Always tag first (using existing workflow), then query the filing
- Output results as a markdown verification table
- Include the period context for each verified value
- Flag mismatches clearly

### 4. Modified file: `mcp_server_config.json`

No changes needed — the XBRL server entry already exists. The new tool is added to the same server.

## Demo Questions

The 3 original demo questions will be adapted for verification. Instead of "tag this text," the user will say "verify this contract clause against [company]'s filing."

Ground truth for testing:
- Apple FY2023: EffectiveIncomeTaxRateContinuingOperations = 14.7% (0.147)
- Apple FY2023: RevenueFromContractWithCustomerExcludingAssessedTax = $383,285M
- All three filings contain debt instrument data for cross-verification

## Scope Constraints

- Local filings only (no EDGAR API calls during demo)
- Three pre-loaded filings (AAPL, MSFT, TSLA)
- No iXBRL edge cases beyond what these three filings contain
- Agent handles period disambiguation via the contract's stated time frame
- Future: upload arbitrary XBRL files, fetch from EDGAR, batch document processing
