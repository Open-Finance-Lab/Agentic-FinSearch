# XBRL Truth Infrastructure — Technology Stack & Architecture Decisions

**Date:** 2026-05-26
**Status:** Living reference
**Companion to:** `2026-05-26-xbrl-truth-benchmark-bridge-design.md` (the *what/why*; this is the *what it's built on*)

This document is the technology stack and the load-bearing architectural decisions for the XBRL Truth Infrastructure — the dual-use substrate (**retrieve · verify · benchmark**) that grounds FinSearch agent outputs in authoritative SEC/XBRL data. It lists technologies and decisions, **not specific code files**.

**Two organizing principles:**
1. **Local-first → cloud-later.** Build on a local stack (DuckDB, local MCP) until the benchmark proves value; migrate to the cloud stack only at Phase 5. Each technology below lists both.
2. **Reuse the shipped stack.** The infra extends the existing FinSearch backend (Django + OpenAI Agents SDK + MCP/FastMCP), not a greenfield rebuild.

---

## Stack at a glance

| Layer | Local / current (build here first) | Target / cloud (Phase 5 hand-off) | New or reused |
|---|---|---|---|
| **0. Data sources** | SEC EDGAR `companyfacts` / `companyconcept` / `frames` APIs; bulk `companyfacts.zip` + Financial Statement Data Sets; raw iXBRL; FASB US-GAAP taxonomy | (same; mirrored into S3) | reused (`sec-edgar-mcp`, `requests`) + new bulk ingest |
| **1. Ingestion & normalization** | Python 3.12; `requests`/throttle; existing iXBRL parser (`xml.etree` / `beautifulsoup4`+`lxml`); config-driven concept-normalization | AWS Lambda (event-driven on new filings) + Glue (bulk transform) + Step Functions | reused parser + new ingest/normalize |
| **2. Canonical Truth Layer (storage)** | **DuckDB** (columnar fact store) + Parquet cold storage; relationships + tree as SQL views | Amazon **Aurora** (tabular facts), **Neptune** (graph), **OpenSearch** (hybrid text/table retrieval), **S3** (raw + parsed artifacts) | **new** |
| **3. PV service layer** | **MCP-native** via `mcp[cli]` + **FastMCP**; Django REST endpoints (`/api/...`); native `openai-agents` `function_tool`s | Amazon **API Gateway** + **Lambda/ECS**; **DynamoDB** cache for hot queries | reused MCP pattern + new services |
| **4. Agent integration** | **OpenAI Agents SDK** (`openai-agents`); planner + skills; multi-model (OpenAI / Anthropic / Gemini); `tool_wrapper` interception | Amazon **Bedrock** (orchestration), **Step Functions** (multi-agent) | reused |
| **5. Benchmark (construct + grade)** | Python templating; structured trace capture; deterministic graders; `networkx` (DAG/path equivalence); QuantEval-style backtest harness (stretch) | SageMaker (eval at scale); public leaderboard + submission service | **new** |
| **6. Observability & governance** | Python logging; provenance + audit fields in the fact store | **CloudWatch** (monitoring), **CloudTrail** (audit trail) | reused + cloud |
| **Frontend** | Chrome extension (TypeScript, **Bun** + Babel); inline verification marks + Validate button | (same) | reused |

---

## Layer detail

### 0. Data sources
- **SEC EDGAR structured APIs** are the primary ground truth. `companyfacts` (all facts per company, provenance baked in) is the breadth ingest; `frames` (one concept across all companies in a period) powers cross-entity questions; `companyconcept` is the runtime hot path.
- **Bulk** (`companyfacts.zip`, Financial Statement Data Sets `sub/num/pre/tag.txt`) for rate-limit-free full S&P 500 backfill.
- **Raw iXBRL** only for dimensional/segment facts and footnote text blocks (a *depth* tool, deferred).
- **FASB US-GAAP taxonomy (2026)** anchors the tagging/normalization step.
- The repo already ships `sec-edgar-mcp`; the truth layer supersedes ad-hoc EDGAR polling with the ingested fact store.

### 1. Ingestion & normalization
- **Language/runtime:** Python 3.12 (matches backend).
- **Fetch:** `requests` with throttling (~10 req/s) + `User-Agent` (SEC fair-access), or one-shot bulk zip.
- **Parse:** `companyfacts` JSON directly for breadth; the existing iXBRL parser (`beautifulsoup4` + `lxml` / `xml.etree`) for dimensional depth.
- **Concept normalization:** config-driven canonical-metric → ordered-acceptable-tags map (generalizes the shipped `RATIO_TAG_MAP`); honest `NOT_APPLICABLE` for metrics that don't exist for a filer (banks/insurers/REITs).
- **Document structure** (for the statement-tree view / table extraction): Docling / PageIndex-class tools (per the Amazon vision) — deferred with the depth tier.
- **Orchestration:** local scripts/cron now; AWS Lambda + Step Functions (event-driven on new filings) later.

### 2. Canonical Truth Layer (storage)
- **Local: DuckDB.** Columnar, single-file, analytical queries over millions of facts, reads Parquet directly — ideal for the benchmark/analytics workload with zero infra. Cold storage in **Parquet**.
- **Storage primitive:** a **context-keyed fact table** (`cik, concept, period, unit, dimensions → value + provenance`), with relationships (temporal, peer, calc-arc) and the filing/period tree as **views** (see ADR-01, ADR-02).
- **Cloud target:** **Aurora** (tabular facts/derived metrics), **Neptune** (graph: entities, metrics, periods, filings, evidence paths), **OpenSearch** (hybrid retrieval over sections/tables/text), **S3** (raw filings + parsed artifacts).

### 3. PV service layer (Provenance–Validation)
- **MCP-native** (`mcp[cli]`, **FastMCP**) — connectivity is commoditized (MCP → Linux-Foundation Agentic AI Foundation, Dec 2025); we ride the standard and differentiate on verified grounding.
- Services: **`RetrieveEvidence`** (facts/tables/text + provenance, with `as_of`), **`TraceClaim`** (claim → source fact + filing section), **`ValidateMetric`** (value/computation check; generalizes the Axiom Engine).
- **Local:** FastMCP servers + native `function_tool`s + Django REST (`/api/axioms/validate/` pattern). **Cloud:** API Gateway + Lambda/ECS, DynamoDB caching. Ingestion-time compute is separated from cheap runtime validation ("ingest once, validate millions").

### 4. Agent integration
- **OpenAI Agents SDK** (`openai-agents`) with the existing planner + skills + `tool_wrapper` interception (code-enforced guardrails, no LLM in the validation path).
- **Multi-model:** OpenAI, Anthropic, Gemini.
- The agent-under-test consumes the PV services as its grounded-retrieval path; the **generate → validate → refine** loop turns validation from post-hoc audit into part of reasoning.
- **Cloud:** Amazon Bedrock + Step Functions for orchestration and multi-agent coordination.

### 5. Benchmark (construction + grading)
- **Construction:** Python templating engine walks the fact store + relationships → `(question, gold answer, gold fact-path, required facts, K, difficulty, as_of)`. Validity controls borrow QuantEval's recipe (difficulty, de-dup, leakage scan, human spot-check gate).
- **Grading:** structured tool-call trace capture; **`networkx`** (or equivalent) for fact-path set/DAG equivalence; deterministic re-computation via `ValidateMetric`; QuantEval-style deterministic backtest harness for the strategy-coding stretch sub-mode.
- **Storage:** versioned benchmark cases as JSON/Parquet.

### 6. Observability & governance
- Provenance + audit fields (`accession, filed_date, taxonomy_version`) live in every fact row — auditability is structural, not a feature.
- **Cloud:** CloudWatch (monitoring), CloudTrail (audit) — maps to EU AI Act / FINOS auditability requirements.

---

## Key architectural decisions (ADRs)

> Format: **Decision** · *Why* · *Consequence*. These are the load-bearing choices — change one and the design shifts.

**ADR-01 — Context-keyed fact table, NOT tag→value.**
*Why:* one US-GAAP tag maps to many facts in a single filing (comparative years + segment/geographic dimensions). A tag→value map silently collapses the exact ambiguity verification exists to resolve.
*Consequence:* the key is `(cik, concept, period_type, period_start, period_end, unit, dimensions)`; value carries `decimals` + provenance.

**ADR-02 — Flat facts are the truth; the filing/period tree is a *view*.**
*Why:* a fact lives at the intersection of *(entity × concept × period × dimensions)* — 4-dimensional; a single tree forces one hierarchy and loses the others.
*Consequence:* store flat facts; expose period-tree and per-filing statement-tree (from the presentation linkbase) as materialized views/indexes for navigation and `RetrieveEvidence`.

**ADR-03 — Store every filing's facts; never overwrite on restatement.**
*Why:* a 10-K/A can report a different value for the same `(cik, concept, period)`. Overwriting destroys both as-of/leakage-control and restatement detection.
*Consequence:* `filed_date` is part of the key; "the" value is an as-of query (`filed_date ≤ D`, latest). Restatement becomes a benchmark question type.

**ADR-04 — Breadth-first `companyfacts` ingest; S&P 500; iXBRL demoted to a depth tool.**
*Why:* `companyfacts` gives a company's full pre-parsed history with provenance in one call; aggregate facts cover most of Tracks R/C. Dimensional depth is a minority need.
*Consequence:* fast path to benchmark scale; the existing iXBRL parser is reserved for segment-level facts (deferred tier).

**ADR-05 — Local-first (DuckDB) → cloud-later (Aurora/Neptune/OpenSearch).**
*Why:* prove the benchmark's value before paying cloud complexity; DuckDB handles millions of facts with zero infra.
*Consequence:* Phase 5 cloud migration is a clean hand-off to the advisor's vision/grant track, not a prerequisite.

**ADR-06 — MCP-native PV services.**
*Why:* MCP is the de-facto standard (now Linux-Foundation governed); connectivity is commoditized, verified grounding is not.
*Consequence:* services plug into any MCP agent; reuses the repo's FastMCP pattern. We compete on verification + benchmark, never on data access.

**ADR-07 — Concept-normalization layer.**
*Why:* companies tag the same economic concept differently; FinTagging measured LLMs at 17% semantic-tag / 0% full-taxonomy accuracy — agents cannot self-ground.
*Consequence:* canonical-metric → ordered-tag maps with explicit, honest unresolvable handling (`NOT_APPLICABLE`).

**ADR-08 — Determinism: no LLM in the validation or grading path.**
*Why:* a verifier that hallucinates is worthless; the moat is *deterministic* checking against the primary source.
*Consequence:* `ValidateMetric` / axiom checks / grading are pure code with `decimals`-aware tolerance; LLMs only generate, never adjudicate (no LLM-as-judge).

**ADR-09 — Dual-use substrate (retrieve · verify · benchmark).**
*Why:* the same canonical layer that verifies runtime claims is the oracle that grades the benchmark and the store agents retrieve from.
*Consequence:* one investment, three deliverables; the benchmark is a byproduct of the verification infra, not a separate build.

**ADR-10 — Verifiability by construction.**
*Why:* questions generated *from* grounded facts carry their gold answer and gold fact-path automatically.
*Consequence:* no human answer-keys, no LLM-judge ambiguity; value-, path-, and computation-verifiability are inherent to each case.

**ADR-11 — Grade on facts reached (set/DAG equivalence), not literal tool order.**
*Why:* a multi-hop question can have multiple valid solution paths; exact-sequence grading punishes correct alternates.
*Consequence:* the gold path is *a sufficient* path; process accuracy measures intermediate facts reached, not tool-call order.

**ADR-12 — `decimals`-aware tolerance for value matching.**
*Why:* XBRL facts carry precision (`decimals`/`scale`); a fixed tolerance produces false mismatches near zero or at coarse precision.
*Consequence:* tolerance = `max(relative, absolute)` scaled by the fact's reported precision (the shipped Numbers-Ratios convention, generalized).

---

## What we deliberately do NOT use (and why)

| Avoided | Why |
|---|---|
| A pure vector-DB RAG store as the source of truth | We need *exact* facts with provenance, not nearest-neighbor text chunks. Vectors/OpenSearch are a *retrieval aid* over the fact store, never the ground truth. |
| LLM-as-judge for grading | Non-deterministic and disputable; defeats the verifiability thesis (ADR-08, ADR-10). |
| `tag → value` map | Collapses the context that disambiguates facts (ADR-01). |
| Overwrite-on-latest fact storage | Destroys leakage control and restatement detection (ADR-03). |
| Cloud-first build | Premature complexity before the benchmark proves value (ADR-05). |
| Re-parsing iXBRL at runtime per query | The whole point of "ingest once, validate millions" — parsing happens once at ingest. |

---

## Cross-cutting invariants
- **Provenance everywhere:** every fact → `(accession, tag, period, unit, context, filed_date, taxonomy_version)`.
- **Leakage control:** every benchmark query and validation respects an `as_of` date via `filed_date` filtering.
- **Determinism:** validation and grading are code-enforced; the LLM is the subject, never the judge.
