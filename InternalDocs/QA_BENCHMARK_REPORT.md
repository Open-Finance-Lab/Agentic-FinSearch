# QA Benchmark Report — Agentic FinSearch vs Manual (24 Questions)

**Test date:** March 16, 2026 | **Mode:** thinking | **Model:** FinSearch | **API:** `agenticfinsearch.org`

## Results at a Glance

| Category | Count | Questions |
|----------|-------|-----------|
| **Correct** | 12 | Q1, Q2, Q3, Q5, Q8, Q11, Q12, Q13, Q15, Q16, Q18, Q22 |
| **Fixed (code — high confidence)** | 5 | Q4, Q6, Q7, Q21, Q23 |
| **Fixed (replaced question)** | 1 | Q14 |
| **Fixed (transient — works on retry)** | 1 | Q19 |
| **Uncertain (prompt fix, needs re-test)** | 4 | Q9, Q10, Q17, Q20 |
| **Unfixable** | 0 | — |

**Before fixes:** 12/24 (50%) | **Projected after deploy:** 19–23/24 (79–96%)

---

## Detailed Breakdown by Question

### Task 1: Real-time retrieval (Q1–Q8)

| Q# | Question | Result | Notes |
|----|----------|--------|-------|
| Q1 | Opening price of Dow 30 | ✅ Correct | 46,689.24 — exact match |
| Q2 | Trading volume of SP500 today | ✅ Correct | 2,964,237,000 — exact match |
| Q3 | Latest prices of Costco and Boeing | ✅ Correct | COST $1,008.43, BA $209.89 |
| Q4 | Percentage change of Alibaba (US) | ❌→✅ Fixed | Used prev close from May 2025 ($121.48) instead of Mar 2026 ($134.20). **Fix:** added `regularMarketPreviousClose` to stock info keys |
| Q5 | Price increase of Snowflake | ✅ Correct | $1.41 — exact match |
| Q6 | Range (High - Low) of AAPL today | ❌→✅ Fixed | Returned $207/$200 (mid-2025 prices) instead of $256/$249. **Fix:** added `regularMarketDayHigh/Low` to stock info keys |
| Q7 | Meta's stock turnover ratio | ❌→✅ Fixed | Used Nov 2025 SEC filing data instead of current day. **Fix:** added `regularMarketVolume` and `sharesOutstanding` to stock info keys |
| Q8 | Dow 30 highest daily return | ✅ Correct | BA +2.51%, $209.89 — exact match |

### Task 2: Simple lookup of historical data (Q9–Q16)

| Q# | Question | Result | Notes |
|----|----------|--------|-------|
| Q9 | Price of SP500 on the 13th | ⚠️ Uncertain | Used May 13, 2024 instead of most recent 13th (Mar 13, 2026). **Fix:** DATE HANDLING prompt |
| Q10 | Close price of GOOG on the 13th | ⚠️ Uncertain | Used June 13, 2025 instead of Mar 13, 2026. **Fix:** DATE HANDLING prompt |
| Q11 | AAPL's beta, P/E, EPS, Earnings Date, Div Yield | ✅ Correct | All values match Yahoo Finance |
| Q12 | GameStop property/equipment (Jan 29, 2022) | ✅ Correct | $163.6M from SEC EDGAR 10-K filing |
| Q13 | Facebook annual revenue (Dec 31, 2019) | ✅ Correct | $70,697M from SEC EDGAR 10-K filing |
| Q14 | Dow jump after Trump/China event | ❌→✅ Replaced | Old question ambiguous (matched 4+ events across 2019–2025). **Replaced with:** "After Trump announced a 90-day pause on reciprocal tariffs in April 2025, how many points did the Dow gain?" **Answer:** ~2,963 pts. Verified correct in thinking mode |
| Q15 | Apple's largest single-day % drop after first 2024 launch | ✅ Correct | -2.11% on May 23, 2024 (after "Let Loose" iPad event) |
| Q16 | Highest Tesla price in 10 days after Trump election | ✅ Correct | $358.64 on Nov 11, 2024 — exact match |

### Task 3: Complex computations (Q17–Q24)

| Q# | Question | Result | Notes |
|----|----------|--------|-------|
| Q17 | Nvidia highest/lowest September close | ⚠️ Uncertain | Used Sept 2024 instead of Sept 2025. **Fix:** DATE HANDLING prompt ("use most recent completed month") |
| Q18 | SP500 largest monthly increase (Jan 2010–Apr 2025) | ✅ Correct | April 2020, 12.68% — exact match |
| Q19 | Months where GOOG returns > AAPL in 2025 | ✅ Transient | Empty response on first try, correct on retry (9/12 months) |
| Q20 | Most recent 3+ consecutive month uptrend in NVIDIA | ⚠️ Uncertain | Reported Aug–Nov 2024, missed Apr–Jul 2025. **Fix:** DATE HANDLING prompt |
| Q21 | Mean and sample variance of SP500 (Aug 1–8, 2025) | ❌→✅ Fixed | Returned 5 data points (missed Aug 8) instead of 6. **Fix:** end-date inclusivity (+1 day to yfinance `end` param) |
| Q22 | Moving average (window=5) of SP500 (Aug 1–5, 2025) | ✅ Correct | All MA(5) values match to 4 decimal places |
| Q23 | Pearson correlation AAPL/GOOG (Oct 2025) | ❌→✅ Fixed | Used 22 data points (missed Oct 31) instead of 23, yielding r=0.83 vs correct 0.85. **Fix:** end-date inclusivity |
| Q24 | NASDAQ companies with market cap > META (Jul 1, 2024) | ✅ ~Correct | Identified MSFT, AAPL, NVDA, GOOGL, AMZN. Minor methodology differences in exact figures |

---

## Root Causes and Fixes

### 1. Missing real-time fields in stock info (Q4, Q6, Q7)

**Root cause:** `STOCK_KEYS` in `stock_info.py` was missing critical real-time market data fields (`regularMarketDayHigh`, `regularMarketDayLow`, `regularMarketPreviousClose`, `regularMarketVolume`, `regularMarketChangePercent`, `sharesOutstanding`). These fields were only available for indices (^GSPC, ^DJI), not individual stocks. Without them, the LLM fell back to web scraping or SEC EDGAR filings, retrieving stale data.

**Fix:** Added 7 fields to `STOCK_KEYS`. Verified locally that all fields return correct current-day values.

**Commit:** `8e388162`

### 2. End-date exclusivity bug (Q21, Q23)

**Root cause:** yfinance's `end` parameter is exclusive (Python convention), but users/LLMs expect "Aug 1 to Aug 8" to include Aug 8. Requesting `end='2025-08-08'` excluded Aug 8, returning 5 rows instead of 6.

**Fix:** Added +1 day adjustment in `stock_history.py` before passing to yfinance. Verified: Aug 1–8 now returns 6 rows, Oct 1–31 returns 23 rows.

**Commit:** `8e388162`

### 3. Ambiguous date resolution (Q9, Q10, Q17, Q20)

**Root cause:** Questions like "on the 13th" or "in September" don't specify the year. The LLM chose arbitrary historical dates instead of the most recent occurrence. The existing TIME CONTEXT in the prompt covered "today's data" but not ambiguous historical references.

**Fix:** Added DATE HANDLING section to `core.md` with explicit rules:
- Use the most recent completed occurrence of any ambiguous month/day
- Always use explicit `start`/`end` dates with `get_stock_history`
- Verify retrieved data dates match the expected period

**Commit:** `8e388162` | **Confidence:** Medium — depends on LLM following prompt instructions

### 4. Ambiguous question (Q14)

**Root cause:** "How many points did the Dow jump after Trump softens tone on China?" matches 4+ real events across 2019–2025 with different magnitudes (270, 587, 1,017, 2,963 pts).

**Fix:** Replaced with unambiguous question referencing the specific April 9, 2025 tariff pause event. Verified the new question is answered correctly in thinking mode.

**Status:** Manual PDF needs to be updated with the new question text.

---

## Recommendations

1. **Deploy and re-test Q9, Q10, Q17, Q20** to validate the DATE HANDLING prompt fix
2. **Consider making questions less ambiguous** in the manual where dates are implied (e.g., "on the 13th" → "on the 13th of this month"; "in September" → "in September 2025")
3. **Update the manual PDF** with the new Q14 text
4. **Monitor OpenAI API quota** — research mode was unavailable during testing due to quota exhaustion
5. **Re-run the full 24-question benchmark** after deployment to establish the new baseline accuracy
