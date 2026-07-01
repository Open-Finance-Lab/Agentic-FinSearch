# ArkSim User Story — Agentic FinSearch

**Date:** 2026-03-22
**Status:** Draft — needs re-run of ArkSim with correct configuration for updated screenshots

---

## 1. What agent are you testing?

**Agentic FinSearch** — a financial AI search agent that answers natural language questions about stocks, crypto, and SEC filings using structured data sources instead of web scraping or training data. Built by Columbia University's SecureFinAI Lab, it acts as a site-aware browser copilot (Chrome extension on Chrome Web Store) and exposes an OpenAI-compatible API.

- **Framework:** OpenAI Agents SDK + Django backend
- **Models:** Gemini 3 Flash Preview (default), GPT-5.1, custom HuggingFace endpoint
- **Tools / integrations:**
  - Yahoo Finance MCP server (9 tools: stock prices, history, financials, earnings, options chains, holders, analyst ratings)
  - TradingView MCP server (7 tools: technical analysis, exchange-wide screening, candlestick patterns, Bollinger Band scans)
  - SEC-EDGAR MCP server (filing search and retrieval)
  - Deterministic `calculate()` tool — AST-based safe arithmetic evaluator, ensuring all numerical computations are exact (no LLM mental math)
  - URL scraping + Playwright browser automation for JavaScript-heavy pages
  - Multi-step research decomposition engine (decomposes complex queries into typed sub-questions, routes to appropriate data sources in parallel, synthesizes)

**What makes it different:** The agent guarantees numerical accuracy by routing all financial data through structured MCP tools and enforcing deterministic arithmetic. Every number in a response comes from a verified data source, not from the LLM's parametric memory.

---

## 2. What scenario did you test?

**Phase 1 — ArkSim automated simulation (35 conversations)**

We connected ArkSim to our OpenAI-compatible API endpoint (`/v1/chat/completions`) and ran 35 simulated conversations covering diverse financial scenarios: trending tickers, real-time price lookups with timestamps, S&P 500 level queries, historical date-specific price retrieval, percentage change calculations, multi-stock comparisons, SEC filing lookups, options chain analysis, and more.

ArkSim generated simulated users with distinct profiles (day traders needing exact timestamps, finance professionals expecting sourced data, retail investors asking basic questions) and evaluated responses across helpfulness, coherence, relevance, faithfulness, and goal completion.

**Phase 2 — Manual 24-question benchmark (numerical accuracy deep dive)**

Motivated by ArkSim's findings, we designed a targeted 24-question benchmark spanning three difficulty tiers:

| Category | Questions | Examples |
|----------|-----------|---------|
| Real-time retrieval | Q1–Q8 | "What is today's opening price of the Dow 30?", "What is the percentage change of Alibaba's US stock today?" |
| Historical data lookup | Q9–Q16 | "What was the close price of GOOG on March 13, 2026?", "What was GameStop's property/equipment value on Jan 29, 2022?" |
| Complex computation | Q17–Q24 | "What is the Pearson correlation between AAPL and GOOG in October 2025?", "Calculate the mean and sample variance of S&P 500 from Aug 1–8, 2025" |

We automated this with a ground truth computation script (using yfinance to independently calculate correct answers for all 24 questions) and an API test script that queried the live agent and compared responses against ground truth.

---

## 3. What issue did ArkSim reveal?

### ArkSim results (initial run — March 10, 2026)

ArkSim's evaluation was devastating:

| Metric | Score |
|--------|-------|
| Helpfulness | 1.6 / 5 |
| Coherence | 2.2 / 5 |
| Relevance | 1.5 / 5 |
| Faithfulness | 2.4 / 5 |
| Goal completion | 1/35 conversations (2.9%) |
| "False information" failure rate | 96.8% of evaluated turns |

**Important context:** This initial run had configuration issues — the agent wasn't properly connected to all its MCP tools, causing it to fall back on LLM parametric knowledge (which hallucinates financial data). The scores reflect a misconfigured agent, not the agent's true capability. However, the *pattern* of failures ArkSim identified was real and pointed to systemic issues.

**Key failure patterns ArkSim surfaced:**
- The agent returned plausible-looking but factually wrong financial data across nearly every conversation
- Goal completion was near-zero — simulated users with specific data needs were consistently unsatisfied
- The "false information" category dominated, indicating the core problem was data accuracy, not coherence or communication style

### Deeper investigation (24-question benchmark — March 14-15, 2026)

ArkSim's findings motivated us to build a more rigorous, ground-truth-validated benchmark. The results confirmed the accuracy problem was real: **the agent was only 50% accurate** (12/24 correct) even with tools properly connected.

The failures were subtle — not hallucinations in the traditional sense, but systematic bugs in how the agent retrieved and processed data:

**Failure 1: Stale data retrieval (Q4, Q6, Q7)**
The agent returned prices from months ago instead of today's values. When asked about Alibaba's percentage change today, it used a previous close price from May 2025 ($121.48) instead of March 2026 ($134.20).

*Root cause:* The `get_stock_info` tool was missing critical real-time fields (`regularMarketDayHigh`, `regularMarketDayLow`, `regularMarketPreviousClose`, `regularMarketVolume`). Without them, the agent fell back to web scraping or SEC filings, retrieving stale cached data instead of live market values.

**Failure 2: Off-by-one date boundary errors (Q21, Q23)**
When asked to compute statistics for "Aug 1 to Aug 8, 2025", the agent returned 5 data points instead of 6, excluding Aug 8. This propagated into a Pearson correlation of 0.83 instead of the correct 0.85.

*Root cause:* yfinance's `end` parameter is exclusive (Python convention), but the LLM interprets "Aug 1 to Aug 8" as inclusive on both ends. The handler passed the end date directly without adjustment.

**Failure 3: Ambiguous date resolution (Q9, Q10, Q17, Q20)**
"What was the price on the 13th?" returned data from May 13, 2024 instead of the most recent 13th (March 13, 2026). "In September" resolved to September 2024 instead of September 2025.

*Root cause:* The system prompt provided the current date but had no rules for resolving ambiguous partial date references. The LLM defaulted to dates from its training data.

**Why these were hard to catch manually:** Each failure looked plausible in isolation. The agent returned real data from real dates — just the *wrong* dates. A human reviewer who doesn't independently verify every number would accept the responses as correct. This is exactly the class of bug that automated testing tools like ArkSim are designed to surface.

---

## 4. What change did you make?

### Fix 1: Expanded real-time data fields
Added 7 missing fields to the `STOCK_KEYS` whitelist in `stock_info.py`:
- `regularMarketDayHigh`, `regularMarketDayLow`, `regularMarketOpen`
- `regularMarketPreviousClose`, `regularMarketVolume`
- `regularMarketChangePercent`, `sharesOutstanding`

This ensures the agent always has current-day market data through the structured tool, eliminating fallback to stale scraped data.

### Fix 2: End-date inclusivity correction
Added a +1 day adjustment in `stock_history.py` before passing the end date to yfinance. "Aug 1 to Aug 8" now correctly returns all 6 trading days.

### Fix 3: Date disambiguation rules in system prompt
Added an explicit DATE HANDLING section to the core system prompt (`core.md`) with rules:
- "The 13th" → most recent 13th relative to today
- "September" → most recent completed September
- Always use explicit start/end dates when calling `get_stock_history`
- Verify retrieved data dates match the expected period

### Fix 4: Replaced ambiguous benchmark question
Q14 ("How many points did the Dow jump after Trump softens tone on China?") matched 4+ real events across 2019–2025. Replaced with an unambiguous version specifying the exact April 9, 2025 tariff pause event.

All fixes shipped in commit `8e388162` and deployed to production.

---

## 5. What improved after the fix?

| Metric | Before | After |
|--------|--------|-------|
| Overall accuracy | 12/24 (50%) | 19–23/24 (79–96%) |
| Real-time retrieval (Q1–Q8) | 5/8 correct | 8/8 correct |
| Historical lookup (Q9–Q16) | 4/8 correct | 6–8/8 correct |
| Complex computation (Q17–Q24) | 3/8 correct | 5–7/8 correct |

**Specific improvements:**
- **Zero stale data failures** — all real-time queries now return current-day values from structured tools
- **Correct date boundaries** — variance, correlation, and moving average calculations now use the right number of data points
- **Better temporal reasoning** — the agent resolves "the 13th" and "September" to the most recent occurrence

The remaining uncertainty (79–96% range) is in 4 date-disambiguation questions that depend on the LLM consistently following the new prompt rules.

**Compared to competitors:** Our published benchmark shows Agentic FinSearch at 91.7% accuracy (22/24) on numerical questions versus Perplexity's 41.7% (10/24) on the same test set.

---

## 6. Screenshots

**Available from ArkSim (initial run, pre-fix):**
- ArkSim HTML evaluation report (`final_report.html`) showing 35 conversations with per-turn metrics
- Overall performance dashboard: helpfulness 1.6, coherence 2.2, relevance 1.5, faithfulness 2.4
- 96.8% false information failure rate across evaluated turns
- Individual conversation transcripts with simulated user profiles and agent responses

**Available from manual benchmark:**
- QA Benchmark Report with per-question breakdown and root cause analysis
- Ground truth comparison showing exact numerical discrepancies per question
- Commit diff showing the 20-line, 4-file fix that addressed 11 test failures

**TODO — Re-run ArkSim post-fix:**
- Re-run ArkSim with properly configured MCP tools against the improved agent
- Capture before/after comparison of ArkSim scores
- This will provide the definitive before/after screenshot pair for the user story

---

## 7. One-sentence takeaway

ArkSim's automated simulation flagged that our financial agent was producing false information in 97% of conversations, which led us to discover that it was returning real data from the wrong dates and time periods — subtle failures that looked correct to human reviewers but were systematically wrong, and required ground-truth validation to catch.
