# Agentic FinSearch — Positioning Strategy

**Date:** 2026-03-22
**Status:** Draft — leaning toward Approach C, keeping B as complementary
**Context:** SecureFinAI Lab at Columbia is transitioning the project from research artifact to self-sustaining product. The lab currently bears all costs (API, compute, dev time, data access). Published paper anchors academic credibility. Chrome extension live on Web Store. Companies have approached but offered vague "partnerships" without reciprocal value.

---

## Current Technical Strengths

**Best-supported data sources:** Yahoo Finance (9 structured tools) and TradingView (7 tools including exchange-wide screening)

**Core differentiators vs. ChatGPT / Perplexity / other AI tools:**
- **Deterministic numerical accuracy** — all arithmetic goes through `calculate()` AST evaluator, never LLM mental math. QA benchmark: 87.5–95.8% accuracy on numerical financial questions
- **Structured financial data via MCP** — 20+ tools returning verified structured data (not scraped text)
- **Exchange-wide technical screening** — scan all assets on Binance, Bybit, etc. for candlestick patterns, Bollinger Band consolidation, overbought/oversold in natural language
- **SEC filing retrieval** — search and read 10-K, 10-Q, 8-K filings via EDGAR MCP
- **OpenAI-compatible API** — any tool using OpenAI's `/v1/chat/completions` format can point to this backend
- **Site-aware context injection** — Chrome extension auto-scrapes the current page and routes to domain-specific tool priorities
- **Research decomposition engine** — multi-step query analysis with parallel sub-question routing to appropriate data sources

---

## Approach B: Vertical API Product for Fintech

### Core Idea
Position the `/v1/chat/completions` API as a **drop-in financial intelligence backend** for fintech apps, robo-advisors, and trading platforms. "Add a financially-accurate AI analyst to your product in one API call."

### Why This Works
- The OpenAI-compatible API is already built — any app using OpenAI's format can switch endpoints with zero code changes
- The killer feature is provable: **deterministic numerical accuracy** via structured data + `calculate()`. No other AI API guarantees this.
- Fintech companies need AI features but can't build financial data pipelines. This is an infrastructure gap.
- The Chrome extension serves as proof-of-concept; the API is the scalable product

### Target Customers
| Segment | Need | What They'd Use |
|---------|------|-----------------|
| Robo-advisors / wealth apps | Natural language portfolio Q&A | Stock info, history, earnings tools via API |
| Crypto trading platforms | AI-powered technical screening | TradingView MCP tools (exchange scanning, pattern detection) |
| Compliance / legal tech | SEC filing search and analysis | SEC-EDGAR MCP + research mode |
| Financial education platforms | Accurate market data for students | Full API surface for interactive learning |
| Independent research tools | Data retrieval + synthesis | Research mode with source tracking |

### Evidence to Collect
- API call volume and unique integrators
- Accuracy benchmarks (existing QA suite results)
- Latency and reliability metrics
- Number of integrated third-party apps
- Customer testimonials / case studies

### Revenue Model
- Usage-based API pricing (per-request or per-token)
- Tiered plans: free (rate-limited), developer, enterprise
- Premium data source add-ons (when Bloomberg/Refinitiv MCPs exist)

### Resources It Attracts
- **Revenue** from API customers
- **Data partnerships** — providers want distribution through the API
- **Strategic investment** — fintech VCs fund infrastructure plays
- **Enterprise contributors** — customers contribute bug fixes and features

### Risks
- Requires sales effort, enterprise support, SLAs, uptime guarantees — things a university lab isn't built for
- Competes with much larger players (Bloomberg GPT, Perplexity Finance, OpenAI with financial plugins)
- Maintaining production reliability at scale is a full-time operational burden
- Revenue timeline may be too slow to relieve lab costs quickly

### What Would Need to Change
- Authentication and rate limiting per API key (currently no user auth on browser API)
- Usage metering and billing infrastructure
- Uptime monitoring and SLA commitments
- Documentation and developer onboarding experience
- Legal: terms of service, data usage disclaimers (Yahoo Finance ToS compliance)

---

## Approach C: Columbia-Anchored Ecosystem (Recommended)

### Core Idea
Use Columbia as the trust anchor and build a **concentric ecosystem**: the lab publishes research and maintains the core, student teams and external contributors build integrations, and companies pay to participate (data access, sponsored features, co-branded research).

### Why This Is Recommended

**1. Columbia brand = instant credibility.**
When you say "SecureFinAI lab at Columbia," companies cannot dismiss you the way they'd dismiss a random GitHub project. This is the moat against fishing expeditions — you set the terms.

**2. The paper is the anchor, the product is the proof.**
Published paper gives academic legitimacy. Working Chrome extension + API proves it's not vaporware. Together, more compelling than either alone.

**3. Multi-resource flywheel:**

```
                    ┌─────────────────────┐
                    │   Columbia Brand +   │
                    │   Published Paper    │
                    └─────────┬───────────┘
                              │ credibility
               ┌──────────────┼──────────────┐
               ▼              ▼              ▼
        ┌─────────────┐ ┌──────────┐ ┌──────────────┐
        │  Companies   │ │ Students │ │  Cloud/Data  │
        │  sponsor     │ │ contribute│ │  providers   │
        │  features    │ │ dev time │ │  sponsor     │
        └──────┬──────┘ └────┬─────┘ └──────┬───────┘
               │             │              │
               └──────────────┼──────────────┘
                              ▼
                    ┌─────────────────────┐
                    │  Better product →   │
                    │  more evidence →    │
                    │  more resources     │
                    └─────────────────────┘
```

- **Students** contribute dev time (capstone projects, RAs, course projects) → lowers engineering burden
- **Data providers** contribute MCP servers for distribution to Columbia researchers → lowers data cost
- **Cloud providers** sponsor compute for academic open-source (AWS/GCP/Azure all have programs) → lowers hosting cost
- **Companies** sponsor features they need (e.g., "fund a Bloomberg MCP, get early access") → generates revenue AND offloads API cost

**4. You control the terms.**
Companies come to you because association with Columbia research has value. The same companies that were "fishing" now have to bring something to the table.

**5. Evidence is naturally multi-dimensional:**

| Evidence Type | Metric | Who Cares |
|---------------|--------|-----------|
| Academic | Paper citations, student involvement, conference talks | Grant committees, NSF, university |
| Product | Chrome Web Store installs, API call volume, accuracy benchmarks | Companies, investors |
| Community | GitHub stars/forks, external contributors, MCP integrations | Open-source foundations, FINOS |
| Industry | Company sponsors, data partnerships, co-branded outputs | Other companies (social proof) |

### Ecosystem Participation Tiers

| Tier | What They Get | What They Give |
|------|---------------|----------------|
| **Community** (free) | Open-source access, public API (rate-limited) | Bug reports, GitHub activity, word-of-mouth |
| **Academic** (free) | Full API access, research collaboration | Co-authored papers, student contributions |
| **Data Partner** | Distribution to all FinSearch users, co-branding | MCP server integration for their data source |
| **Sponsor** | Early access, feature prioritization, Columbia co-branding | Funding, compute credits, or dedicated engineering support |

---

## Roadmap

### Phase 0 — Harvest Evidence (Now, before building anything new)

Stop adding features. Collect evidence from what already exists:

- Chrome Web Store install count + growth rate (already tracked by Google)
- Pin down QA benchmark at 95%+ (re-test Q9, Q10, Q17, Q20 after prompt fix deploy)
- Submit ArkSim user story with screenshots
- Apply for cloud credits: AWS Activate, Google for Education, Azure for Research — zero dev time, directly offloads hosting costs
- Paper citation tracking (Google Scholar)
- GitHub stars/fork count as baseline metric

**Cost:** Near zero. **Output:** The "evidence" needed for partnerships, grants, and the professor's framing.

### Phase 1 — Open the MCP Layer (Highest leverage move)

Flip the architecture from monolith to platform:

- Publish Yahoo Finance and TradingView MCP servers as **standalone open-source packages** with their own repos, documentation, and pip/npm install
- Create a "Build a FinSearch MCP Server" contributor guide — targeted at Columbia capstone teams and external developers
- Establish a plugin registry: anyone contributes an MCP server, it plugs into the agent automatically via `mcp_server_config.json`

**Why highest leverage:** Every external MCP server contribution = a data source you didn't build + a contributor invested in the project + GitHub activity from their network. Turns architecture into a platform.

### Phase 2 — Narrow the Product, Deepen the Moat

Pick one or two verticals and be the undeniable best, rather than covering all of finance broadly:

| Vertical | Why | What to build |
|----------|-----|---------------|
| **Equity research copilot** | Strongest current tooling (Yahoo Finance + SEC EDGAR). Clear buyer persona (analysts, students). | Tighten earnings + options + insider workflow. Comparison templates. |
| **Crypto technical screening** | No competitor does exchange-wide NL screening. TradingView tools are unique. | Expand exchange coverage, add alerts, standalone screener UI. |

Picking one doesn't mean deleting the other — it means marketing, user stories, conference demos, and partnership conversations all tell the same focused story.

### Phase 3 — Revenue Layer (Approach B, lightweight)

Introduce API access tiers once evidence (Phase 0) and contributor ecosystem (Phase 1) exist:

- **Free:** rate-limited, community support
- **Academic:** full access, requires .edu email
- **Sponsored:** companies pay for priority features or dedicated data sources

Start with manual invoicing for 2-3 sponsor companies. Don't build billing infrastructure for scale you don't have yet.

### What to Cut or Defer

| Item | Reason |
|------|--------|
| Mobile support | High effort, low evidence value |
| New model integrations | 3 models is enough; more doesn't help the story |
| More MCP servers built by the lab | Spend energy making it easy for *others* to build them |
| Advanced research mode improvements | Works for demos; depends on OpenAI API (cost + dependency risk) |

### Roadmap Summary

**Stop building features, start building evidence and ecosystem.** The tech is strong enough. What's missing isn't capability — it's proof of demand and a contributor base that shares the maintenance burden.

### Concrete Next Steps

1. **Collect evidence now:** Chrome Web Store install count, GitHub stars, QA benchmark results, paper citation count
2. **Apply for cloud credits:** AWS Activate, Google for Education, Azure for Research — all offer free compute for academic projects
3. **Formalize FINOS connection:** The project already references FINOS domains; pursue membership for open-source governance credibility
4. **Create a "Partnership Inquiry" page:** Instead of entertaining fishing calls, direct companies to a structured form that asks what they bring to the table
5. **Student onboarding pipeline:** Document the MCP architecture so capstone teams can build new tool servers independently
6. **Conference demo loop:** Each conference presentation should end with a clear CTA: "sponsor a feature" or "contribute an MCP server"

### Risks
- Requires project governance and relationship management (not just code)
- Professor likely needs to drive partnership conversations while lab focuses on engineering
- Columbia IP policies may constrain commercialization — need to clarify tech transfer terms early
- Ecosystem building is slow; short-term costs don't decrease until the flywheel spins up

### How B Complements C
Approach B (API product) can exist as a **revenue layer within the Approach C ecosystem**. The API is offered to sponsors and partners first, then opened more broadly as the product matures. This avoids the full sales/support burden of a standalone API business while still generating revenue. The ecosystem provides the contributors and data partnerships that make the API more valuable, which attracts more API customers, which funds the ecosystem.

---

## Four Most Compelling Use Cases

These are the user stories that sell regardless of approach, based on what the agent can do TODAY with Yahoo Finance and TradingView:

| # | Use Case | Differentiator | Target Audience |
|---|----------|---------------|-----------------|
| 1 | "I asked about AAPL's P/E ratio and got the exact number, not a hallucination" | Deterministic accuracy via structured data + `calculate()`. Provable with QA benchmark. | Compliance teams, financial advisors, anyone with fiduciary duty |
| 2 | "I scanned all of Binance for consolidating assets in one natural language query" | Exchange-wide technical screening via TradingView MCP. Bloomberg Terminal does this at $24k/year. | Crypto traders, quant researchers, hedge fund analysts |
| 3 | "I compared two stocks' earnings, options activity, and insider transactions in one conversation" | Multi-tool parallel orchestration across data types | Equity research analysts, investment students |
| 4 | "I asked about a company's latest 10-K filing and got the actual document, not a 2023 summary" | Real-time SEC EDGAR access, not training data | Compliance, legal researchers, due diligence analysts |

---

## Monetization Analysis

### Constraints

**1. Yahoo Finance Terms of Service**
The core data pipeline runs through yfinance, which scrapes Yahoo Finance. Yahoo's terms prohibit redistribution of their data for commercial purposes. Charging money for an API that returns Yahoo Finance data puts the project in a legal gray zone. This is why nearly every yfinance-based project stays "open source" or "educational."

**2. University IP Ownership**
This was built at Columbia's SecureFinAI lab. Columbia almost certainly has IP claims on work produced by lab members using university resources. Before charging anyone for anything, the tech transfer office needs to clarify whether the lab can commercialize directly or needs to spin out a separate entity.

**3. Upstream API Margin Risk**
Research mode calls OpenAI's API. Models run on Google and OpenAI infrastructure. Charging users for responses that cost the lab API credits requires margins — margins that depend on upstream pricing the lab doesn't control. A price increase from OpenAI directly compresses or eliminates profitability.

### What Works in Our Favor

**1. The value is orchestration, not raw data.**
Nobody pays for Yahoo Finance data (it's free). The value is "ask a question in English, get a verified numerical answer with sources from multiple structured financial databases." That's integration quality + accuracy, not data resale.

**2. Academic/research framing changes the legal picture.**
If the paid tier is framed as "sponsoring research" or "supporting an open-source project" rather than "purchasing a data product," the legal treatment is different. This is how many university projects operate — sponsors fund the lab, they don't buy a service.

**3. MCP servers are the cleanest monetization path.**
Standalone MCP server packages that companies run on their own infrastructure with their own data sidestep the Yahoo Finance ToS issue entirely. The lab sells the tool (the orchestration logic, the field mapping, the error handling), not the data flowing through it.

### Monetization Paths — Ranked by Feasibility

| # | Path | Legal Risk | Revenue Potential | Effort | Timeline |
|---|------|-----------|-------------------|--------|----------|
| 1 | **Sponsorship tiers** — companies pay for association + feature priority + Columbia co-branding | Low — donation/sponsorship, not product sale | $5–50k/year per sponsor | Low — manual invoicing, no billing infra | Immediate |
| 2 | **Standalone MCP server licensing** — package tools for companies to self-host | Low — selling orchestration code, not data | Medium — per-seat or per-org license | Medium — packaging, docs, support | 3–6 months |
| 3 | **Consulting / custom integration** — lab members help companies integrate FinSearch | Low — professional services | Low-medium — time-limited, doesn't scale | High — trades dev time for money | Immediate |
| 4 | **Managed MCP hosting** — run the financial tools for companies who don't want to self-host | Medium — Yahoo Finance ToS gray area for "hosting" vs "reselling" | Medium | Medium — multi-tenant isolation needed | 6–12 months |
| 5 | **Premium data source add-ons** — Bloomberg, Refinitiv MCPs behind a paid tier | Low — proper data licenses obtained | High — if licenses acquired | High — expensive data licenses | 12+ months |
| 6 | **SaaS API with usage-based pricing** — full Approach B | High — Yahoo Finance ToS + Columbia IP + upstream margin risk | Highest if it works | Highest — auth, billing, SLAs, support | 12+ months |

### Recommendation

Start with **sponsorship** (lowest risk, immediate revenue, aligns with Approach C ecosystem model) and work toward **standalone MCP server licensing** (cleanest legal path, leverages the modular architecture). Defer SaaS API pricing until:

1. Columbia IP ownership is clarified with tech transfer office
2. Usage volume is high enough to negotiate proper data provider terms
3. Upstream API costs are predictable enough to set sustainable margins

### Critical Action Item

**Talk to Columbia's tech transfer office early.** This conversation determines which monetization paths are even available. If Columbia claims full IP ownership, the lab may need to negotiate a license-back arrangement or spin out a separate entity. If the lab retains rights (common for open-source research software), sponsorship and licensing can proceed directly. Either way, knowing the answer before pursuing any revenue is essential — discovering IP constraints after signing a sponsor would be damaging.
