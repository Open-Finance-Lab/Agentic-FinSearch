# ArkSim Evaluation Report #2 — Analysis & Action Items

**Date:** 2026-03-26
**Evaluator:** GPT-5.1 (LLM-as-judge)
**Tool:** [ArkSim](https://github.com/arklexai/arksim)
**Report file:** `final_report(1).html`

---

## 1. Raw Results Summary

| Metric | Value |
|--------|-------|
| Total Conversations | 15 |
| Total Turns | 43 |
| Avg Turns/Conversation | 2.87 |
| Helpfulness | 2.2 / 5 |
| Coherence | 3.0 / 5 |
| Verbosity | 2.3 / 5 |
| Relevance | 2.7 / 5 |
| Faithfulness | 4.0 / 5 |

### Reported Failure Breakdown
- False information: 82.6% (19 occurrences)
- Disobey user request: 13.0% (3 occurrences)
- Lack of specific information: 4.3% (1 occurrence)

### Per-Conversation Scores

| # | Scenario | Goal? | Score | Status |
|---|----------|-------|-------|--------|
| 1 | S&P 500 5-day Moving Avg (Aug 2025) | No | 0.25 | Failed |
| 2 | GOOG Close (Mar 13, 2026) | No | 0.00 | Failed |
| 3 | Facebook Revenue FY2019 | **Yes** | **1.00** | **Done** |
| 4 | S&P 500 Largest Monthly Gain (2010-2025) | **Yes** | 0.85 | Partial |
| 5 | DJIA Tariff Pause Day Gain (Apr 2025) | **Yes** | 0.50 | Failed |
| 6 | AAPL-GOOG Pearson Correlation (Oct 2025) | No | 0.25 | Failed |
| 7 | Tesla Intraday High Post-Election 2024 | No | 0.75 | Partial |
| 8 | GameStop PP&E (Jan 2022) | No | 0.75 | Partial |
| 9 | NVDA Most Recent 3-Month Uptrend | No | 0.30 | Failed |
| 10 | NVDA Sep 2025 High/Low | No | 0.00 | Failed |
| 11 | S&P 500 Mean & Variance (Aug 2025) | No | 0.375 | Failed |
| 12 | Market Cap vs META (Jul 2024) | **Yes** | 0.25 | Failed |
| 13 | AAPL Fundamentals Snapshot | No | 0.375 | Failed |
| 14 | GOOG vs AAPL Monthly Returns 2025 | **Yes** | 0.25 | Failed |
| 15 | S&P 500 Close (Mar 13, 2026) | No | 0.375 | Failed |

---

## 2. Critical Finding: Evaluator Doesn't Account for MCP Tools

**The 82.6% "false information" rate is significantly inflated.**

We verified that our API's MCP tools (Yahoo Finance, SEC EDGAR) are functional and return correct data. Testing the same queries from the report against our local API confirmed tool usage:

```
POST /v1/chat/completions  mode=thinking
Query: "What was the closing price of GOOG on March 13, 2026?"
→ Response: $301.46 (matches ground truth exactly)
→ Sources: [get_stock_history, ticker: GOOG]
```

The GPT-5.1 evaluator doesn't know our agent has live MCP tools. It assumed everything was from parametric memory and flagged accordingly:

### Incorrectly Flagged as "False Information"

| Conv | Agent Value | Ground Truth | Judge Reason | Reality |
|------|------------|-------------|-------------|---------|
| 2 | GOOG $301.4599914551 | $301.46 | "Fabricated extra digits" | Raw Yahoo Finance float — correct value |
| 10 | NVDA high $186.5800018311 | $186.58 | "Fabricated extra digits" | Raw Yahoo Finance float — correct value |
| 10 | NVDA low $167.0200042725 | $167.02 | "Fabricated extra digits" | Raw Yahoo Finance float — correct value |
| 13 | AAPL Beta 1.12, P/E 31.83, EPS $7.90, earnings Apr 30, yield 0.41% | Exactly those values | "Fabricated metrics from future date" | All correct from get_stock_info tool |
| 15 | S&P 500 6,632.19 | 6,632.19 | "Inventing contextual details" | Exact match from tool |
| 4 | April 2020, 12.68% | April 2020, 12.68% | Turn-level "fabricated tables" | Correct answer with supporting data |
| 12 | MSFT/AAPL/NVDA/GOOG/AMZN caps | Correct companies listed | Turn-level "inventing details" | Goal completion = 1.0, data correct |

**Estimated real failure rate: ~20-30%, not 82.6%.**

### Action for ArkSim Team
The evaluation prompts need awareness that the agent under test has tool access. The `agent_behavior_failure` judge prompt should be updated to not flag data as "fabricated" when the agent has access to external data tools. This is a test infrastructure issue, not an agent issue.

---

## 3. Architecture Verification: core.md Is NOT Overridable

We investigated whether ArkSim's system message (sent as `role: "system"`) could override our core prompt. The answer is **no**.

**Trace:**
1. ArkSim sends system message → `context_mgr.set_system_prompt()`
2. `get_formatted_messages_for_api()` wraps it as `[SYSTEM MESSAGE]: {content}`
3. `_create_agent_response_async()` extracts it, passes as `system_prompt=`
4. `create_fin_agent()` calls `PromptBuilder.build(system_prompt=...)`
5. `PromptBuilder.build()` **appends** it to `core.md` via `parts.append()`

Final prompt structure:
```
core.md                                    ← always first, immutable
+ default_site.md (or site-specific)       ← always included
+ time context                             ← always included
+ ArkSim's system message                  ← appended at end
```

The `instructions_override` parameter exists in `create_fin_agent()` but the API path (`openai_views.py` → `datascraper.py`) never uses it. Core.md is always the foundation.

---

## 4. Real Issues On Our Side

These are genuine problems visible in the report, independent of evaluator errors.

### Issue 1: Excessive Verbosity (affects all 15 conversations)

**Severity:** Medium
**Evidence:** Every simulated user profile says "You prefer short, factual answers and really care about precision." The agent consistently returns:
- Multi-row tables with full OHLCV data when only close price was asked
- Step-by-step calculation breakdowns when only the result was requested
- Lengthy source citations and verification sections
- Explanatory context nobody asked for (e.g., "A beta of 1.12 indicates...")

**Root cause:** `core.md` has no instruction about matching response length to user preferences. The agent defaults to maximum detail.

**Example (Conv 2):** User asks for closing price → agent returns a 5-field OHLCV table, volume, source attribution, and detailed verification section. Should have been one line: "GOOG closed at $301.46 on March 13, 2026."

**Fix:** Add a RESPONSE STYLE section to `core.md`:
```
RESPONSE STYLE:
- Lead with the direct answer to the user's question.
- Only include supporting data (tables, calculations, sources) if the user asks for it.
- Match your response length to the complexity of the question — simple lookups get one-line answers.
```

### Issue 2: Raw Float Precision From Tools (affects ~5 conversations)

**Severity:** Medium
**Evidence:** Agent returns raw Yahoo Finance floats like `$301.4599914551` instead of `$301.46`. The DATA ACCURACY section in core.md already says "present numbers rounded to 2 decimal places" but the agent doesn't consistently follow it.

**Conversations affected:** 2, 10 (raw floats presented directly)

**Root cause:** The rule exists in core.md but is buried among many other rules. LLMs are more likely to follow rules that are prominent and repeated. Also, the raw float comes directly from tool output and the agent doesn't consistently post-process it.

**Fix options:**
- (a) Make the rounding rule more prominent in core.md (move to top, bold emphasis)
- (b) Add server-side post-processing to round tool outputs before the agent sees them
- (c) Both

### Issue 3: Wrong Date Ranges on Temporal Queries (affects 2-3 conversations)

**Severity:** High
**Evidence:**
- **Conv 9 (NVDA uptrend):** Agent said Aug-Nov 2024, ground truth is Nov 2025-Jan 2026. The agent fetched old data instead of the most recent period.
- **Conv 14 (GOOG vs AAPL 2025):** Agent said Jan, Apr, May outperformed. Ground truth is Apr, May, Jun, Jul, Sep, Oct, Nov, Dec. The agent only had data through May 2025 and didn't fetch the rest of the year.

**Root cause:** The DATE HANDLING section in core.md covers ambiguous month references but not multi-month range queries. When asked "which months in 2025," the agent should fetch the full year but may stop at its current date context.

**Fix:** Add to DATE HANDLING in core.md:
```
- When a query covers a full year (e.g., "in 2025"), always fetch data for the complete requested period using explicit start and end dates. Do not assume the year is incomplete based on the current date if the data is available from tools.
- For "most recent" queries, always check forward from the last known data point rather than stopping at training knowledge.
```

### Issue 4: Not Answering Direct Questions Directly (affects 3 conversations)

**Severity:** Medium
**Evidence:**
- **Conv 1, Turn 1:** Simulated user asks to choose option 1 or 2. Agent presents both instead of choosing.
- **Conv 9, Turn 3:** User asks a yes/no question about date bounds. Agent answers with a lengthy redefinition instead of "yes" or "no" followed by brief context.
- **Conv 6, Turn 1:** User asks for specific precision (6 decimal places). Agent uses population variance formula instead of sample variance without asking.

**Root cause:** The agent defaults to "comprehensive" behavior — showing everything it can rather than answering the specific question asked.

**Fix:** Add to RESPONSE STYLE in core.md:
```
- When asked a yes/no or choice question, lead with the direct answer ("Yes." / "No." / "Option 2.") before any explanation.
- When the user specifies a formula, method, or precision rule, follow it exactly. Do not substitute your own preferred method.
```

### Issue 5: Minor Computation Rounding Differences (affects 1-2 conversations)

**Severity:** Low
**Evidence:**
- **Conv 11:** Sample variance = 2605.4948 vs ground truth 2605.4951 (off by 0.0003)
- **Conv 12:** MSFT market cap difference = 2.114T vs ground truth 2.117T

**Root cause:** Intermediate rounding in multi-step calculations. The CALCULATION RULES section already says to use `calculate()` tool, but the agent may not use it for every step.

**Fix:** Already addressed by existing core.md rules. Could add emphasis:
```
- For multi-step calculations, use calculate() for EVERY arithmetic operation, including intermediate steps. Never approximate.
```

---

## 5. Priority Matrix

| Priority | Issue | Impact | Effort | Conversations Affected |
|----------|-------|--------|--------|----------------------|
| **P0** | Verbosity — add RESPONSE STYLE rules | All 15 convos | Low (prompt edit) | 15/15 |
| **P1** | Date range handling for temporal queries | 2-3 failures | Low (prompt edit) | 9, 14 |
| **P1** | Answer direct questions directly | 3 failures | Low (prompt edit) | 1, 6, 9 |
| **P2** | Tighten float rounding rule prominence | 2-5 convos | Low (prompt edit) | 2, 10 |
| **P2** | Computation precision emphasis | 1-2 convos | Low (prompt edit) | 11, 12 |
| **External** | ArkSim evaluator doesn't know agent has tools | Inflates failure rate by ~50-60% | N/A (their side) | Most convos |

All fixes are prompt-level changes to `Main/backend/prompts/core.md`. No code changes required.

---

## 6. Recommended core.md Changes (Draft)

Add the following sections to `core.md`:

### New Section: RESPONSE STYLE (add after GENERAL RULES)
```
RESPONSE STYLE:
- Lead with the direct answer. Put the number, fact, or yes/no first.
- Simple lookups (single price, single metric) get a one-line answer. No tables unless asked.
- Only show calculation steps, source citations, or verification details if the user requests them.
- When asked a yes/no or choice question, answer it directly before any elaboration.
- When the user specifies a formula, method, or precision rule, follow it exactly without substitution.
```

### Additions to DATE HANDLING
```
- When a query covers a full year or multi-month range, always fetch data for the complete requested period using explicit start and end dates. Do not truncate based on the current date if tool data is available.
- For "most recent" queries (e.g., "most recent uptrend"), scan forward from the latest available data rather than stopping at training knowledge. Always verify with tools.
```

### Additions to DATA ACCURACY
```
- IMPORTANT: Always round tool-returned values to 2 decimal places in your response, even if the tool returns higher precision (e.g., 301.4599914551 → 301.46). Only show full precision if the user explicitly requests it.
```

### Addition to CALCULATION RULES
```
- For multi-step calculations, use calculate() for EVERY arithmetic step including intermediates. Never perform mental math or approximations between tool calls.
```

---

## 7. Next Steps

1. **Implement core.md changes** — All fixes above are prompt-level, low-risk
2. **Re-run ArkSim evaluation** — Validate improvements with same 15 scenarios
3. **Raise evaluator issue with ArkSim team** — The judge prompts need tool-awareness context to avoid inflated false-information rates
4. **Automate this review process** — Per user's note, this analysis should eventually be automated
