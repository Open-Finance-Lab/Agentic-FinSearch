# XBRL Filing Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the FinSearch agent to verify financial data claims in documents against real XBRL filings stored locally.

**Architecture:** iXBRL parser extracts tagged values from SEC filing HTML/XML into an in-memory lookup. A new MCP tool `query_xbrl_filing` lets the agent query by company + tag name. The agent's prompt is updated with a verification workflow (extract claims, tag, query filing, compare, report).

**Tech Stack:** Python 3.12, xml.etree.ElementTree (stdlib), MCP SDK (already installed), existing XBRL MCP server.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `Main/backend/mcp_server/xbrl/parser.py` | iXBRL parsing: extract facts, resolve contexts/units, company-to-file mapping |
| Create | `Main/backend/mcp_server/xbrl/test_parser.py` | Unit tests for parser against all 3 real filings |
| Modify | `Main/backend/mcp_server/xbrl/server.py` | Add `query_xbrl_filing` tool to MCP server |
| Modify | `Main/backend/prompts/core.md` | Add tool to registry + VERIFICATION workflow |

---

### Task 1: iXBRL Parser — Core Extraction

**Files:**
- Create: `Main/backend/mcp_server/xbrl/parser.py`
- Create: `Main/backend/mcp_server/xbrl/test_parser.py`

This task builds the parser that reads iXBRL files (HTML with embedded XBRL using the `ix:` namespace) and extracts all numeric facts into structured records.

**Key technical details the implementer must know:**

- SEC filings are **Inline XBRL (iXBRL)**: HTML documents where financial data is embedded in `<ix:nonFraction>` tags within the HTML body.
- The actual numeric value is: `display_text * 10^scale`. For example, `"383,285"` with `scale="6"` = 383,285,000,000 (billions). `"14.7"` with `scale="-2"` = 0.147 (a percentage stored as decimal).
- The `contextRef` attribute links to `<xbrli:context>` blocks that define the reporting period (duration or instant) and optional dimensional breakdowns (e.g., by segment).
- The `unitRef` attribute links to `<xbrli:unit>` blocks (e.g., `iso4217:USD`, `xbrli:shares`, `xbrli:pure`).
- Some `<ix:nonFraction>` elements are nested inside other `<ix:nonFraction>` elements (parent wrapping children for display). Only process **leaf nodes** to avoid double-counting.
- Display text may contain commas (`383,285`), em-dashes (`—` or `\u2014` for zero), or be empty. Must clean before parsing.
- The `sign` attribute on an element means the value is negative (e.g., `sign="-"`).
- MSFT uses UUID-style context IDs (`C_7a8e428f-...`), AAPL/TSLA use `c-1`, `c-2`, etc. Parser must handle both.
- The same tag+context pair may appear multiple times in the document (in different HTML sections). Deduplicate by `(name, contextRef)`.
- Tags have a namespace prefix like `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`. The tool query will use just the local name (without prefix). Parser should store both.

**Namespaces used across all 3 filings:**
```python
NAMESPACES = {
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
}
```

- [ ] **Step 1: Write failing tests for parser**

Create `Main/backend/mcp_server/xbrl/test_parser.py`:

```python
"""Tests for iXBRL parser against real SEC filings."""

import pytest
from pathlib import Path
from mcp_server.xbrl.parser import parse_filing, find_filing, query_facts

FILINGS_DIR = Path(__file__).parent / "filings"


class TestParseFiling:
    """Test that parse_filing extracts correct values from real iXBRL files."""

    def test_aapl_effective_tax_rate(self):
        facts = parse_filing(FILINGS_DIR / "aapl-20230930.xml")
        matches = [
            f for f in facts
            if f["tag"] == "EffectiveIncomeTaxRateContinuingOperations"
            and not f["has_dimensions"]
        ]
        # Apple reports 3 years of tax rates in the 10-K
        assert len(matches) >= 3
        # FY2023 (ending 2023-09-30) should be 14.7% = 0.147
        fy2023 = [f for f in matches if f["period_end"] == "2023-09-30"]
        assert len(fy2023) == 1
        assert abs(fy2023[0]["value"] - 0.147) < 0.001
        assert fy2023[0]["unit"] == "pure"

    def test_aapl_revenue(self):
        facts = parse_filing(FILINGS_DIR / "aapl-20230930.xml")
        matches = [
            f for f in facts
            if f["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
            and not f["has_dimensions"]
            and f["period_end"] == "2023-09-30"
            and f["period_start"] is not None  # duration, not instant
        ]
        assert len(matches) == 1
        # Apple FY2023 total revenue = $383,285M
        assert abs(matches[0]["value"] - 383_285_000_000) < 1_000_000
        assert matches[0]["unit"] == "USD"

    def test_msft_revenue(self):
        facts = parse_filing(FILINGS_DIR / "msft-20230630.xml")
        matches = [
            f for f in facts
            if f["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
            and not f["has_dimensions"]
            and f["period_end"] == "2023-06-30"
            and f["period_start"] is not None
        ]
        assert len(matches) == 1
        # Microsoft FY2023 total revenue = $211,915M
        assert abs(matches[0]["value"] - 211_915_000_000) < 1_000_000

    def test_tsla_revenue(self):
        facts = parse_filing(FILINGS_DIR / "tsla-20231231.xml")
        matches = [
            f for f in facts
            if f["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
            and not f["has_dimensions"]
            and f["period_end"] == "2023-12-31"
            and f["period_start"] is not None
        ]
        assert len(matches) == 1
        # Tesla FY2023 total revenue = $96,773M
        assert abs(matches[0]["value"] - 96_773_000_000) < 1_000_000

    def test_facts_have_required_fields(self):
        facts = parse_filing(FILINGS_DIR / "aapl-20230930.xml")
        assert len(facts) > 0
        required_keys = {"tag", "namespace", "value", "unit", "period_start", "period_end", "has_dimensions"}
        for fact in facts[:10]:
            assert required_keys.issubset(fact.keys()), f"Missing keys in {fact}"


class TestFindFiling:
    """Test company name to filing file resolution."""

    def test_find_by_ticker(self):
        path = find_filing("aapl", FILINGS_DIR)
        assert path is not None
        assert "aapl" in path.name

    def test_find_case_insensitive(self):
        path = find_filing("TSLA", FILINGS_DIR)
        assert path is not None
        assert "tsla" in path.name

    def test_find_by_company_name(self):
        # "apple" should match "aapl" via an alias map
        path = find_filing("apple", FILINGS_DIR)
        assert path is not None
        assert "aapl" in path.name

    def test_find_nonexistent_returns_none(self):
        path = find_filing("nonexistent", FILINGS_DIR)
        assert path is None


class TestQueryFacts:
    """Test the high-level query interface."""

    def test_query_aapl_tax_rate(self):
        results = query_facts("apple", "EffectiveIncomeTaxRateContinuingOperations", FILINGS_DIR)
        assert len(results) >= 1
        # Should include FY2023 value
        values = [r["value"] for r in results]
        assert any(abs(v - 0.147) < 0.001 for v in values)

    def test_query_unknown_tag_returns_empty(self):
        results = query_facts("apple", "NonExistentTag123", FILINGS_DIR)
        assert results == []

    def test_query_unknown_company_returns_empty(self):
        results = query_facts("nonexistent", "Revenue", FILINGS_DIR)
        assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Main/backend && python -m pytest mcp_server/xbrl/test_parser.py -v 2>&1 | head -40`

Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server.xbrl.parser'` or `ImportError`

- [ ] **Step 3: Implement parser.py**

Create `Main/backend/mcp_server/xbrl/parser.py`:

```python
"""iXBRL parser for SEC filing verification.

Parses Inline XBRL (iXBRL) HTML/XML files to extract tagged financial facts.
Uses only Python stdlib (xml.etree.ElementTree). No external dependencies.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

NAMESPACES = {
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
}

# Company name/ticker -> filename prefix mapping for common lookups
COMPANY_ALIASES = {
    "apple": "aapl",
    "microsoft": "msft",
    "tesla": "tsla",
}

# Cache parsed filings: filepath -> list of facts
_cache: Dict[str, List[Dict]] = {}


def _parse_contexts(root: ET.Element) -> Dict[str, Dict]:
    """Extract context definitions: id -> {period_start, period_end, has_dimensions}."""
    contexts = {}
    for ctx in root.findall(".//xbrli:context", NAMESPACES):
        cid = ctx.get("id")
        if not cid:
            continue

        period = ctx.find(".//xbrli:period", NAMESPACES)
        if period is None:
            continue

        start_el = period.find("xbrli:startDate", NAMESPACES)
        end_el = period.find("xbrli:endDate", NAMESPACES)
        instant_el = period.find("xbrli:instant", NAMESPACES)

        segment = ctx.find(".//xbrli:segment", NAMESPACES)
        has_dimensions = segment is not None and len(segment) > 0

        if start_el is not None and end_el is not None:
            contexts[cid] = {
                "period_start": start_el.text,
                "period_end": end_el.text,
                "has_dimensions": has_dimensions,
            }
        elif instant_el is not None:
            contexts[cid] = {
                "period_start": None,
                "period_end": instant_el.text,
                "has_dimensions": has_dimensions,
            }

    return contexts


def _parse_units(root: ET.Element) -> Dict[str, str]:
    """Extract unit definitions: id -> human-readable label."""
    units = {}
    for unit in root.findall(".//xbrli:unit", NAMESPACES):
        uid = unit.get("id")
        if not uid:
            continue
        measure = unit.find("xbrli:measure", NAMESPACES)
        if measure is not None and measure.text:
            # "iso4217:USD" -> "USD", "xbrli:pure" -> "pure", "xbrli:shares" -> "shares"
            units[uid] = measure.text.split(":")[-1]
        else:
            # Compound unit (e.g., usdPerShare = USD/shares)
            units[uid] = uid
    return units


def _clean_numeric_text(text: str) -> Optional[float]:
    """Parse display text to a float, handling commas, em-dashes, parentheses."""
    if not text:
        return None
    text = text.strip()
    # Em-dash or long dash = zero
    if text in ("—", "\u2014", "-", ""):
        return 0.0
    # Remove commas and spaces
    text = text.replace(",", "").replace(" ", "")
    # Handle parentheses for negatives: (123) -> -123
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def parse_filing(filepath: Path) -> List[Dict]:
    """Parse an iXBRL file and return all numeric facts.

    Returns list of dicts with keys:
        tag: str - local tag name (e.g., "RevenueFromContractWithCustomerExcludingAssessedTax")
        namespace: str - namespace prefix (e.g., "us-gaap")
        value: float - computed value (display_text * 10^scale)
        unit: str - unit label (e.g., "USD", "pure", "shares")
        period_start: str|None - start date for duration contexts, None for instants
        period_end: str - end date (or instant date)
        has_dimensions: bool - True if context has dimensional breakdowns
    """
    cache_key = str(filepath)
    if cache_key in _cache:
        return _cache[cache_key]

    tree = ET.parse(filepath)
    root = tree.getroot()

    contexts = _parse_contexts(root)
    units = _parse_units(root)

    # Find all ix:nonFraction elements
    all_fractions = root.findall(".//ix:nonFraction", NAMESPACES)

    # Identify parent elements (those that contain child ix:nonFraction)
    parents = set()
    for elem in all_fractions:
        children = elem.findall(".//ix:nonFraction", NAMESPACES)
        if children:
            parents.add(elem)

    # Process only leaf nodes, deduplicate by (name, contextRef)
    seen = set()
    facts = []

    for elem in all_fractions:
        if elem in parents:
            continue

        name = elem.get("name", "")
        ctx_ref = elem.get("contextRef", "")
        dedup_key = (name, ctx_ref)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        # Parse namespace:tag
        if ":" in name:
            ns_prefix, local_tag = name.split(":", 1)
        else:
            ns_prefix, local_tag = "", name

        # Get display text (all text content, ignoring child element tags)
        raw_text = ET.tostring(elem, encoding="unicode", method="text").strip()
        parsed_num = _clean_numeric_text(raw_text)
        if parsed_num is None:
            continue

        # Apply scale
        scale = int(elem.get("scale", "0"))
        value = parsed_num * (10 ** scale)

        # Apply sign attribute
        if elem.get("sign") == "-":
            value = -abs(value)

        # Resolve context
        ctx = contexts.get(ctx_ref, {})
        if not ctx:
            continue

        # Resolve unit
        unit_ref = elem.get("unitRef", "")
        unit_label = units.get(unit_ref, unit_ref)

        facts.append({
            "tag": local_tag,
            "namespace": ns_prefix,
            "value": value,
            "unit": unit_label,
            "period_start": ctx.get("period_start"),
            "period_end": ctx.get("period_end", ""),
            "has_dimensions": ctx.get("has_dimensions", False),
        })

    _cache[cache_key] = facts
    return facts


def find_filing(company: str, filings_dir: Path) -> Optional[Path]:
    """Resolve a company name or ticker to a filing path.

    Checks ticker match first, then alias map, against XML files in filings_dir.
    """
    company_lower = company.lower().strip()

    # Resolve alias to ticker
    ticker = COMPANY_ALIASES.get(company_lower, company_lower)

    # Scan filings directory for a matching file
    for f in filings_dir.glob("*.xml"):
        if f.name.lower().startswith(ticker):
            return f

    return None


def query_facts(
    company: str,
    tag_name: str,
    filings_dir: Path,
) -> List[Dict]:
    """Query facts for a company and tag name.

    Args:
        company: Company name or ticker (e.g., "apple", "AAPL")
        tag_name: XBRL tag local name (e.g., "EffectiveIncomeTaxRateContinuingOperations")
        filings_dir: Path to directory containing iXBRL files

    Returns:
        List of matching facts sorted by period_end descending (most recent first).
        Empty list if company or tag not found.
    """
    filepath = find_filing(company, filings_dir)
    if filepath is None:
        return []

    facts = parse_filing(filepath)
    matches = [f for f in facts if f["tag"] == tag_name]

    # Sort by period_end descending (most recent first)
    matches.sort(key=lambda f: f["period_end"] or "", reverse=True)
    return matches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Main/backend && python -m pytest mcp_server/xbrl/test_parser.py -v`

Expected: All 9 tests PASS. Key values to confirm:
- AAPL tax rate FY2023 = 0.147
- AAPL revenue FY2023 = ~$383.285B
- MSFT revenue FY2023 = ~$211.915B
- TSLA revenue FY2023 = ~$96.773B

- [ ] **Step 5: Commit**

```bash
git add Main/backend/mcp_server/xbrl/parser.py Main/backend/mcp_server/xbrl/test_parser.py
git commit -m "feat: add iXBRL parser for SEC filing verification"
```

---

### Task 2: Add `query_xbrl_filing` Tool to MCP Server

**Files:**
- Modify: `Main/backend/mcp_server/xbrl/server.py`

This task adds a new MCP tool to the existing XBRL taxonomy server. The tool lets the agent query a parsed iXBRL filing by company name and tag name.

**Current state of server.py:** The file already has two tools (`lookup_xbrl_tags`, `validate_xbrl_tag`) registered via `handle_list_tools()` and dispatched in `handle_call_tool()`. We add a third tool following the same pattern.

The `filings/` directory path is: `Path(__file__).parent / "filings"`

- [ ] **Step 1: Add tool definition to `handle_list_tools()`**

In `Main/backend/mcp_server/xbrl/server.py`, add a new import at the top:

```python
from mcp_server.xbrl.parser import query_facts
```

Add a third `types.Tool(...)` entry to the list returned by `handle_list_tools()`, after the existing `validate_xbrl_tag` tool:

```python
        types.Tool(
            name="query_xbrl_filing",
            description=(
                "Query a company's XBRL filing for the reported value of a specific "
                "XBRL tag. Returns all matching values with their reporting periods. "
                "Use this to verify financial claims against the original filing data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": (
                            "Company name or ticker symbol (e.g., 'apple', 'AAPL', "
                            "'microsoft', 'MSFT', 'tesla', 'TSLA')"
                        ),
                    },
                    "tag_name": {
                        "type": "string",
                        "description": (
                            "XBRL tag name to look up (e.g., "
                            "'EffectiveIncomeTaxRateContinuingOperations', "
                            "'RevenueFromContractWithCustomerExcludingAssessedTax'). "
                            "Use lookup_xbrl_tags first to find the correct tag name."
                        ),
                    },
                },
                "required": ["company", "tag_name"],
            },
        ),
```

- [ ] **Step 2: Add tool handler in `handle_call_tool()`**

Add a new `elif` branch in `handle_call_tool()`, after the `validate_xbrl_tag` branch and before the final `return` statement:

```python
    elif name == "query_xbrl_filing":
        company = arguments.get("company", "")
        tag_name = arguments.get("tag_name", "")

        if not company or not tag_name:
            return [types.TextContent(type="text", text="Error: both 'company' and 'tag_name' are required")]

        filings_dir = Path(__file__).parent / "filings"
        results = query_facts(company, tag_name, filings_dir)

        if not results:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"NOT FOUND: No values for tag '{tag_name}' in filings for '{company}'. "
                        "Check that the company name/ticker is correct and the tag name is valid "
                        "(use lookup_xbrl_tags to find correct tag names)."
                    ),
                )
            ]

        lines = [f"Found {len(results)} value(s) for '{tag_name}' in {company}'s filing:\n"]
        for i, r in enumerate(results, 1):
            period = r["period_start"] or ""
            if period:
                period = f"{period} to {r['period_end']}"
            else:
                period = f"as of {r['period_end']}"
            dim_note = " [dimensional breakdown]" if r["has_dimensions"] else ""
            lines.append(
                f"{i}. Value: {r['value']}  (unit={r['unit']}, period={period}){dim_note}"
            )

        return [types.TextContent(type="text", text="\n".join(lines))]
```

Also add `from pathlib import Path` to the imports at the top of server.py (if not already present).

- [ ] **Step 3: Test MCP server starts without errors**

Run: `cd Main/backend && python -c "from mcp_server.xbrl.server import server; print('Server loaded OK')" && echo "PASS"`

Expected: `Server loaded OK` then `PASS`

- [ ] **Step 4: Test tool execution directly**

Run:
```bash
cd Main/backend && python -c "
from mcp_server.xbrl.parser import query_facts
from pathlib import Path
filings_dir = Path('mcp_server/xbrl/filings')
results = query_facts('apple', 'EffectiveIncomeTaxRateContinuingOperations', filings_dir)
for r in results:
    period = f\"{r['period_start']} to {r['period_end']}\" if r['period_start'] else f\"as of {r['period_end']}\"
    print(f\"  {r['value']:.4f} ({r['unit']}, {period}, dim={r['has_dimensions']})\")"
```

Expected output (3 years of tax rates, most recent first):
```
  0.1470 (pure, 2022-09-25 to 2023-09-30, dim=False)
  0.1620 (pure, 2021-09-26 to 2022-09-24, dim=False)
  0.1330 (pure, 2020-09-27 to 2021-09-25, dim=False)
```

- [ ] **Step 5: Commit**

```bash
git add Main/backend/mcp_server/xbrl/server.py
git commit -m "feat: add query_xbrl_filing MCP tool for filing verification"
```

---

### Task 3: Update Agent Prompt with Verification Workflow

**Files:**
- Modify: `Main/backend/prompts/core.md`

This task adds `query_xbrl_filing` to the canonical tool registry and adds a `VERIFICATION` workflow section to the agent's system prompt.

**Current state of core.md:** Lines 29-31 define the XBRL Taxonomy tools section. Lines 74-94 contain the XBRL TAGGING instructions. We add the new tool to the registry and add a new VERIFICATION section after the existing XBRL TAGGING section.

- [ ] **Step 1: Add tool to canonical registry**

In `Main/backend/prompts/core.md`, find the XBRL Taxonomy tools section (currently lines 29-31):

```
XBRL Taxonomy tools:
  - lookup_xbrl_tags: Search the official US-GAAP 2026 XBRL taxonomy for tag names matching a description. Returns ranked candidates.
  - validate_xbrl_tag: Check if an XBRL tag name exists in the official taxonomy.
```

Add after `validate_xbrl_tag`:

```
  - query_xbrl_filing: Query a company's XBRL filing for the actual reported value of a specific tag. Returns values with reporting periods.
```

- [ ] **Step 2: Add VERIFICATION workflow section**

After the existing `XBRL TAGGING:` section (which ends around line 94 with the "CRITICAL: NEVER invent..." paragraph), add a new section:

```markdown
XBRL VERIFICATION:
When asked to VERIFY financial data in a document or contract against a company's filing, follow this process:
1. The user will provide: a company name and text containing financial claims (with time frames).
2. EXTRACT every distinct numerical claim from the text. Identify what each number represents.
3. TAG each claim using the XBRL TAGGING workflow above (lookup_xbrl_tags → select best → validate_xbrl_tag).
4. QUERY the filing: for each validated tag, call query_xbrl_filing with the company name and tag name.
5. COMPARE: match the document's claimed value against the filing's reported value for the correct period.
   - Percentage values: the filing stores percentages as decimals (e.g., 14.7% is stored as 0.147). Convert before comparing.
   - Monetary values: the filing stores values in raw units (e.g., $383.3B is stored as 383285000000). Convert before comparing.
   - Match the time period: use the date range in the document to select the correct period from the filing results.
6. OUTPUT a markdown verification table:
   | Claim | Document Value | XBRL Tag | Filing Value | Period | Status |
   |-------|---------------|----------|-------------|--------|--------|
   | Revenue | $383.3B | RevenueFromContractWithCustomerExcludingAssessedTax | $383.3B | FY2023 | verified |
   | Tax rate | 15.0% | EffectiveIncomeTaxRateContinuingOperations | 14.7% | FY2023 | MISMATCH |

   Use "verified" for matches and "MISMATCH" for discrepancies. After the table, briefly explain any mismatches.
```

- [ ] **Step 3: Verify prompt file is valid**

Run: `wc -l Main/backend/prompts/core.md && head -35 Main/backend/prompts/core.md`

Expected: Line count increased by ~20 lines. First 35 lines should show `query_xbrl_filing` in the XBRL tools list.

- [ ] **Step 4: Commit**

```bash
git add Main/backend/prompts/core.md
git commit -m "feat: add XBRL verification workflow to agent prompt"
```

---

### Task 4: Integration Smoke Test

**Files:**
- No new files. Uses existing MCP server startup and parser.

This task verifies the complete chain works end-to-end: server starts, tool is callable, returns correct data.

- [ ] **Step 1: Verify MCP server starts with new tool**

Run:
```bash
cd Main/backend && timeout 5 python -m mcp_server.xbrl.server 2>&1 || true
```

Expected: Server starts without import errors (will hang waiting for stdio input — the timeout kills it). No tracebacks.

- [ ] **Step 2: Run full parser test suite**

Run: `cd Main/backend && python -m pytest mcp_server/xbrl/test_parser.py -v`

Expected: All 9 tests PASS.

- [ ] **Step 3: Spot-check all 3 companies have queryable data**

Run:
```bash
cd Main/backend && python -c "
from mcp_server.xbrl.parser import query_facts
from pathlib import Path
d = Path('mcp_server/xbrl/filings')
for company, tag in [
    ('apple', 'RevenueFromContractWithCustomerExcludingAssessedTax'),
    ('microsoft', 'RevenueFromContractWithCustomerExcludingAssessedTax'),
    ('tesla', 'RevenueFromContractWithCustomerExcludingAssessedTax'),
]:
    results = query_facts(company, tag, d)
    top = results[0] if results else {}
    print(f'{company}: {top.get(\"value\", \"NOT FOUND\"):,.0f} ({top.get(\"period_start\",\"\")} to {top.get(\"period_end\",\"\")})')
"
```

Expected:
```
apple: 383,285,000,000 (2022-09-25 to 2023-09-30)
microsoft: 211,915,000,000 (2022-07-01 to 2023-06-30)
tesla: 96,773,000,000 (2023-01-01 to 2023-12-31)
```

- [ ] **Step 4: Commit all remaining changes and verify clean state**

```bash
git status
```

Expected: working tree clean (all changes committed in Tasks 1-3).
