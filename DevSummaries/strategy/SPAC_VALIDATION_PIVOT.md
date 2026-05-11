# Strategic Pivot: SPAC / Shell Company Deal Validation

**Date:** 2026-03-26
**Status:** P1 — Scoping
**Origin:** Professor directive (phone call, 2026-03-26)
**Context:** Refinement of the output validation pivot discussed in the same session

---

## Background

The lab's strategic pivot centers on: **"Model outputs are probabilistic. Model companies want to win user trust. We have a way to monitor and validate the correctness of model outputs in finance."**

Initial brainstorming explored a browser extension overlay on chatgpt.com / gemini.com / claude.ai that would scrape chatbot outputs and validate financial claims in real time. The professor rejected this approach:

- **"你已经在思考 product 和 serve users"** — Too product-oriented, not research-validation-oriented
- **"工程量没法控制，会把团队拖入泥潭"** — DOM scraping across constantly-changing chatbot UIs is an open-ended engineering tar pit
- **"只选高净值的 1～2个tasks"** — Pick 1–2 high-value, well-scoped validation tasks instead

After a follow-up call, the professor clarified the direction:

> **Target: IPO'd shell companies. The deals/contracts between these shell companies and other companies need validation — the stakes are high, and we only need a handful of clients, not thousands.**

---

## The Opportunity

### What Are IPO'd Shell Companies?

SPACs (Special Purpose Acquisition Companies) and similar blank-check entities that:
1. IPO to raise capital in a trust
2. Find a private target company to merge with (the "de-SPAC" transaction)
3. File extensive SEC documents (S-4, proxy statements, fairness opinions) containing financial projections, comparable company analyses, and historical financial data about both entities

### Why This Is High-Value

- **Legal exposure:** Incorrect financial claims in merger filings can trigger SEC enforcement actions, shareholder lawsuits, and deal collapse
- **Fiduciary duty:** SPAC sponsors, boards, and their advisors have legal obligations to verify the accuracy of financial representations
- **Existing pain point:** Due diligence on merger financials is currently done manually by analysts at law firms, investment banks, and accounting firms — expensive, slow, and error-prone
- **Concentrated market:** ~50–200 active SPAC deals per year in the US. A handful of law firms and advisory shops handle the majority

### Why the Professor Likes This

| Property | Why It Matters |
|----------|---------------|
| **Static demands** | The validation checklist for merger filings is well-defined and doesn't change week-to-week. Revenue figures, projections, comparable company data, historical prices — these are known claim types. |
| **Few customers, high stakes** | 5–10 institutional clients (law firms, SPAC sponsors, advisory firms). Each deal is worth millions; paying $10–50k for validation is trivial relative to deal value. |
| **No VC dependency** | Revenue from service contracts, not equity fundraising. Avoids the Series A → B → C treadmill where growth expectations outpace what an academic lab can deliver. |
| **Minimal proof-of-concept** | Take one real SPAC merger filing → run its financial claims through our validation pipeline → produce a report. That's a demo, not a product. |

---

## Mapping to Existing Capabilities

### What We Already Have

| Existing Capability | Application to SPAC Validation |
|---------------------|-------------------------------|
| **SEC EDGAR MCP tools** | Pull S-4 filings, proxy statements, 10-K/10-Q for both shell company and target |
| **Yahoo Finance MCP tools** | Verify reported stock prices, market cap, financial statement data, comparable company metrics |
| **TradingView MCP tools** | Validate technical claims, price history, market data |
| **Deterministic `calculate()` tool** | Verify all arithmetic in financial projections (revenue growth rates, valuation multiples, discount rates) |
| **Claim decomposition logic** | Parse merger documents into atomic, verifiable claims |
| **QA benchmark methodology** | Same ground-truth comparison approach, applied to deal documents |
| **91.7% accuracy on financial questions** | Proven track record vs. 41.7% for Perplexity on same benchmark |

### What's New (To Build)

| Component | Description | Effort |
|-----------|-------------|--------|
| **Document ingestion pipeline** | Parse SEC filing PDFs/HTML into structured sections | Medium |
| **Financial claim extractor** | LLM-powered module to identify verifiable financial claims in merger documents | Medium |
| **Claim-to-tool router** | Map extracted claims to appropriate MCP tools for verification | Low (extends existing routing) |
| **Validation report generator** | Produce a structured report: claim → source → ground truth → verdict | Medium |
| **SPAC-specific prompts** | Prompt engineering for merger document understanding | Low |

---

## Comparison to Previous Approaches

| Dimension | Chatbot Overlay (Rejected) | SPAC Deal Validation (Current) |
|-----------|---------------------------|-------------------------------|
| **Customer base** | Thousands of retail users | 5–10 institutional clients |
| **Input format** | DOM scraping from chatbot UIs (constantly breaking) | SEC filings — stable, structured format |
| **Revenue model** | Unclear — freemium, ads? | Service contracts, $10–50k per deal |
| **Engineering scope** | Open-ended (multi-site DOM parsing, real-time overlay UI) | Well-scoped (known document types, known claim categories) |
| **Time to demo** | Weeks–months of frontend engineering | Days — run one filing through existing pipeline |
| **VC dependency** | Likely needs funding to scale | Self-sustaining from first client |
| **Validation demands** | Dynamic, real-time, unpredictable | Static, batch, well-defined |
| **Alignment with lab** | Product company disguised as research | Research with direct commercial application |

---

## Proof-of-Concept Plan

### Minimum Viable Demo

1. **Select one recent SPAC merger filing** (S-4 or proxy statement)
2. **Extract 20–50 financial claims** from the document (revenue figures, projections, comparable company data, historical prices)
3. **Run each claim through the existing MCP validation pipeline**
4. **Produce a validation report** showing: claim text → data source queried → ground truth value → match/mismatch → confidence
5. **Calculate accuracy metrics** — what percentage of claims in the filing are verifiable, and what percentage check out?

### Success Criteria

- Demonstrate that we can automatically catch at least 1 material discrepancy in a real filing
- Show that our validation is faster and more comprehensive than manual review
- Produce a report format that a law firm or SPAC sponsor would recognize as useful

---

## Risks and Open Questions

| Risk | Mitigation |
|------|------------|
| SPAC market has cooled since 2021 peak | Market is smaller but deals still happen; quality validation is more valuable when scrutiny is higher |
| Filing formats vary across companies | Start with S-4 filings which have a relatively standard structure |
| Some claims may not be verifiable with our current data sources | Scope the PoC to claim types we know we can verify (prices, financials, ratios) |
| Columbia IP implications for commercial service | Same tech transfer question from positioning strategy — needs early clarification |
| Competition from Bloomberg Terminal, specialized legal tech | Our differentiator is automated claim-level validation, not just data access |

---

## Relationship to Overall Strategic Pivot

This is the **"1–2 high-value tasks"** the professor asked for. The broader thesis remains:

> We validate AI model outputs in finance. The SPAC deal validation use case is the proof-of-concept — it demonstrates the capability on high-stakes documents where accuracy is legally required.

If the PoC succeeds, the same validation engine can expand to:
- Earnings call transcript validation
- Analyst report fact-checking
- Regulatory filing cross-referencing
- And eventually, the original chatbot output validation vision — but with proven technology and paying customers first

---

## Next Steps

- [ ] **P1:** Research SPAC filing landscape — identify 2–3 recent S-4 filings to use as PoC candidates
- [ ] **P1:** Prototype financial claim extraction on one real filing
- [ ] **P2:** Build validation report generator
- [ ] **P2:** Test end-to-end pipeline on selected filing
- [ ] **P3:** Package demo for professor review
- [ ] **Blocking:** Clarify Columbia IP/tech transfer implications (same action item from positioning strategy)
