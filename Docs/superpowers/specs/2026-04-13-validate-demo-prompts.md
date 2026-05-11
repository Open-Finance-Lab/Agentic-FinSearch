# Auto-Validate Demo Prompts (wrong-data scenarios)

Each prompt triggers the new auto-fire flow because it contains a validation-intent
phrase ("validate", "verify", "fact-check"). The agent registers the user-supplied
number as `claimed_value`; auto-fire then POSTs `/api/axioms/validate/`, which fetches
ground truth from the local XBRL filing and compares. A wrong claimed_value produces
a red **FAILED** badge inline on the number AND a Validation Report table.

The engine compares the agent's `claimed_value` against XBRL ground-truth inputs
(`revenue`, `cogs`, etc.) — the agent's `formula_inputs` JSON is decorative, not used
for verification. So we only need the agent to commit a wrong number to `claimed_value`.

---

## Primary demo (most reliable): Tesla FY2023 gross margin

[Thinking Mode]  Fact-check this analyst-report claim against the SEC XBRL filing for me. Use the exact figures I'm giving you (do not recompute the margin) so the Validate engine can compare your registered number to the filing.

Claim: Tesla's FY2023 gross margin was **25.20%**, with revenue of $96,773M and cost of revenue of $72,381M (figures as reported in the analyst note).

Ticker TSLA, fiscal-year-end 2023-12-31.

    Expected Results:
Claimed margin: 25.20%
Ground truth from `tsla-20231231.xml`: Revenue $96,773M, CostOfRevenue $79,113M → gross_margin = (96,773 − 79,113) / 96,773 × 100 = **18.2547%**
Engine verdict: **FAILED**, variance ≈ 38.04% (|18.2547 − 25.20| / 18.2547).
UI: the inline "25.20%" gets the red FAILED class; the auto-fired Validation Report row shows
`gross margin | TSLA | 2023-12-31 | 25.2 | 18.255 | 38.043% | FAILED`.

---

## Backup demo: Apple FY2023 current ratio (counter-intuitive)

[Thinking Mode]  Verify this balance-sheet claim against Apple's SEC XBRL filing. Register the number I'm giving you exactly as stated; do not recompute it.

Claim: Apple's FY2023 current ratio was **1.25** (commonly reported in financial-news write-ups).

Ticker AAPL, fiscal-year-end 2023-09-30.

    Expected Results:
Claimed ratio: 1.25
Ground truth from `aapl-20230930.xml`: AssetsCurrent $143,566M / LiabilitiesCurrent $145,308M = **0.9880**
Engine verdict: **FAILED**, variance ≈ 26.52%.
UI: inline "1.25" gets red FAILED class; report row shows
`current ratio | AAPL | 2023-09-30 | 1.25 | 0.988 | 26.518% | FAILED`.

---

## Backup demo: Microsoft FY2023 accounting equation

[Thinking Mode]  Validate this balance-sheet claim against Microsoft's SEC XBRL filing. Use the value I provide as the claimed Total Assets; do not recompute.

Claim: Microsoft's FY2023 Total Assets = **$364,840M** (this is the user-supplied figure; verify against the filing).

Ticker MSFT, fiscal-year-end 2023-06-30.

    Expected Results:
Claimed assets: $364,840M (this is actually MSFT's FY2022 figure, reused as a "transcription error" claim).
Ground truth from `msft-20230630.xml` for 2023-06-30: A=$411,976M, L=$205,753M, TempEq=$0, E=$206,223M → L+TE+E = $411,976M.
Engine verdict: **FAILED**, variance ≈ 11.44% (|411,976 − 364,840| / 411,976).
UI: inline "$364,840 million" (or whatever form the agent renders) gets red FAILED; report row shows
`accounting equation | MSFT | 2023-06-30 | 364.840B | 411.976B | 11.441% | FAILED`.

---

## Reliability notes

- The intent regex matches "fact-check", "verify", and "validate" at the start of the prompt — that's what gates the auto-fire. Don't paraphrase to "is this right?" alone; that doesn't match.
- The "use the figure I'm giving you, do not recompute" instruction is the keystone. Without it, a sufficiently capable model may look up the truth itself, register the *correct* value to `claimed_value`, and the engine will VERIFY (boring demo).
- All three filings used by the engine are local: `Main/backend/mcp_server/xbrl/filings/{aapl-20230930.xml, msft-20230630.xml, tsla-20231231.xml}`. No internet required for the demo.
- If the agent emits `report_claim` for a ratio NOT in the user's question (a "supporting" ratio), that extra claim will also be validated automatically. Live audiences sometimes find this distracting; the system prompt's RELEVANCE GATE rule is supposed to suppress it, but it leaks occasionally.
- If for any reason auto-fire doesn't trigger (network blip, agent skipped report_claim), the manual **Validate** button is still attached and shows the same report.
