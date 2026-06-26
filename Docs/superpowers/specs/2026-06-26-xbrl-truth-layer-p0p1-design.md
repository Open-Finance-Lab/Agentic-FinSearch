# XBRL Truth Layer — P0/P1 Canonical Truth Layer (Validate-path migration) — Design

- **Date:** 2026-06-26
- **Status:** Approved (design); implementation plan to follow
- **Author:** FlyMiss
- **Scope phase:** P0 (instrument provenance) + P1 (canonical truth layer), first slice
- **Related:** `2026-05-26-xbrl-truth-benchmark-bridge-design`, central-db `knowledge/xbrl-truth-layer-atl-forward-compat.md`, `knowledge/finsearch-four-layer-architecture.md`

## 1. Context & goal

The XBRL Truth Layer is the spine of the four-rung ladder (`retrieve · verify · benchmark · trade`). Today only a Layer-1 *demo* exists: `axioms/resolver.py` parses a single bundled XBRL filing per company (AAPL/MSFT/TSLA FY2023) using a hand-curated `RATIO_TAG_MAP`. A single filing is one point in time and cannot express "what was known as of date D" across restatements, so the as-of axis that every downstream consumer (the P4 grader, the ATL fundamentals-as-`market_snapshot` bridge, the verifiable decision log) depends on does not yet exist in code.

**Goal of this unit:** stand up a real canonical truth layer as a reusable, plain-Python primitive — `retrieve_evidence(entity, concept, period, as_of)` over a DuckDB store of SEC `companyfacts` — and migrate the existing Validate path onto it without regressing the 3/3 demo. Build the layer as the *first consumer's substrate* so the benchmark grader and ATL become additional callers later, not a rewrite.

**Guardrail (advisor memo):** fundamentals are framed as *measurement* — "what was known as of date D" — not alpha-seeking. The as-of contract is the product.

## 2. Locked decisions (and why)

| Decision | Choice | Rationale |
|---|---|---|
| Acceptance gate | **Validate-path migration** (keep 3/3 demo green) | Smallest first slice with a known-good oracle; the live product becomes the first consumer. Benchmark-resolve + grader are deferred *additional callers*. |
| Data source | **SEC `companyfacts` API, vendored snapshot** | Only source carrying per-fact `filed` date across restatements — i.e. the as-of axis and provenance in one source. Vendoring a pinned JSON keeps tests reproducible/offline. |
| Module layout | **New plain-Python `truthlayer/` package** | "As-of as a reusable primitive *of the truth layer*, not buried in the axiom/benchmark code." No Django/MCP deps, so the grader and ATL `import truthlayer` and call it in-process with zero refactor. |
| Restatements | **Never overwrite; coexist by `filed`** | Point-in-time storage already locked in the 2026-05-26 design; `retrieve` picks `max(filed) <= as_of`. |
| Value exactness | **DuckDB `DECIMAL(38,6)` + `value_exact: Decimal`** | A layer named *truth* must not lose precision; float64 is only exact below 2^53. Keep `value: float` for the existing engine (zero migration). |

## 3. Architecture & data flow

```
Main/backend/truthlayer/
  ingest.py     companyfacts JSON -> normalized rows -> DuckDB (idempotent, append-only)
  store.py      DuckDB schema + connection; never-overwrite restatements
  registry.py   concept registry (generalizes RATIO_TAG_MAP) + Concept/Period types
  retrieve.py   retrieve_evidence(Query) / retrieve_evidence_batch(qs)  [THE primitive]
  contracts.py  frozen dataclasses: Evidence, Provenance, ConceptSpec, Period, Query
  data/         vendored companyfacts snapshots (aapl/msft/tsla) + built .duckdb
```

Flow: `vendored companyfacts JSON -> ingest -> DuckDB facts table`, then at query time `axioms/resolver.py -> retrieve_evidence() -> Evidence{value, provenance, as_of}`. No Django/MCP imports anywhere in the package. MCP/HTTP wrappers (XBRL P2 / ATL P2) stay purely additive.

## 4. DuckDB schema (context-keyed; restatements coexist; provenance inline)

```sql
facts(
  fact_id      TEXT PRIMARY KEY,  -- sha1 of the canonical tuple (see S2)
  cik          BIGINT,
  taxonomy     TEXT,              -- 'us-gaap' | 'dei'
  tag          TEXT,              -- e.g. 'Assets'
  unit         TEXT,              -- 'USD' | 'USD/shares' | 'shares'
  value        DOUBLE,            -- compute view
  value_exact  DECIMAL(38,6),     -- exact (truth claim / grader)
  period_start DATE,              -- NULL for instant (balance-sheet) facts
  period_end   DATE,
  fy           INTEGER,
  fp           TEXT,              -- 'FY','Q1'..'Q4'
  form         TEXT,              -- '10-K' | '10-Q'
  accession    TEXT,
  filed        DATE,              -- the as-of axis
  frame        TEXT               -- SEC canonical period frame, e.g. 'CY2023Q4I' (nullable)
)
entities(cik BIGINT PRIMARY KEY, ticker TEXT, name TEXT)
-- reserved (schema only, not populated now): entity_tickers(cik, ticker, valid_from, valid_to)
-- index on (cik, tag, period_end, filed) for the as_of hot path
```

Two facts for the same `(cik, tag, period)` with different `filed`/`accession` (an original and a restatement) both live. companyfacts returns only consolidated headline facts (no segment/member dimensions), so the demo sidesteps dimension filtering; dimensional facts are deferred to a future raw-XBRL ingest path.

## 5. Frozen contracts

```python
@dataclass(frozen=True)
class Provenance:
    fact_id: str        # sha1(canonical tuple); DOCUMENTED scheme — see S2
    cik: int
    accession: str
    filed: date         # the as-of axis — when this value became known
    form: str           # '10-K' | '10-Q'
    taxonomy: str       # 'us-gaap' | 'dei'
    tag: str            # the ACTUAL us-gaap tag matched
    fy: int             # SEC-reported fiscal year of THIS fact
    fp: str             # SEC-reported fiscal period
    frame: str | None   # SEC canonical period frame (None if absent)

@dataclass(frozen=True)
class Evidence:
    concept: str                  # registry concept name, e.g. 'revenue' (NOT the raw tag)
    value: float | None           # compute view (existing engine)
    value_exact: Decimal | None   # exact (grader / truth claim)
    unit: str | None              # 'USD' | 'USD/shares' | 'shares'
    period: Period
    as_of: date | None            # echo of the query cutoff (None = latest known)
    provenance: Provenance | None
    found: bool
    tags_tried: tuple[str, ...]   # candidates attempted — powers "no reliable data"
    restated_later: bool | None   # a filing after as_of changed this value; None if uncomputed

@dataclass(frozen=True)
class Period:
    fiscal_year: int
    fiscal_period: str = "FY"     # 'FY' | 'Q1'..'Q4'
    period_end: date | None = None  # optional exact end-date (demo path)

@dataclass(frozen=True)
class Query:
    entity: str                   # ticker or CIK
    concept: str
    period: Period
    as_of: date | None = None
```

`retrieve_evidence(q: Query) -> Evidence` and `retrieve_evidence_batch(qs: Sequence[Query]) -> list[Evidence]` (ATL backtest hot path). Declared but not built this unit: `retrieve_evidence_history(entity, concept, period) -> list[Evidence]` (the restatement chain), so `Evidence` stays single-valued.

## 6. Concept registry (generalizes `RATIO_TAG_MAP`)

```python
@dataclass(frozen=True)
class ConceptSpec:
    period_type: str               # 'instant' | 'duration'
    tags: tuple[str, ...]          # ordered us-gaap candidates; first match wins

REGISTRY_VERSION = "2026-06-26"
CONCEPT_REGISTRY = {
    "assets":              ConceptSpec("instant",  ("Assets",)),
    "liabilities":         ConceptSpec("instant",  ("Liabilities",)),
    "equity":              ConceptSpec("instant",  ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                                                    "StockholdersEquity")),
    "temporary_equity":    ConceptSpec("instant",  ("TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
                                                    "RedeemableNoncontrollingInterestEquityCarryingAmount")),
    "revenue":             ConceptSpec("duration", ("RevenueFromContractWithCustomerExcludingAssessedTax",
                                                    "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet")),
    "cost_of_revenue":     ConceptSpec("duration", ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")),
    "current_assets":      ConceptSpec("instant",  ("AssetsCurrent",)),
    "current_liabilities": ConceptSpec("instant",  ("LiabilitiesCurrent",)),
}

# Ratios reference CONCEPTS (rename map keeps the engine's input names stable):
RATIO_CONCEPTS = {
    "accounting_equation": {"assets": "assets", "liabilities": "liabilities",
                            "equity": "equity", "temporary_equity": "temporary_equity"},
    "gross_margin":        {"revenue": "revenue", "cogs": "cost_of_revenue"},
    "current_ratio":       {"current_assets": "current_assets", "current_liabilities": "current_liabilities"},
}
```

Lifting tag lists to top-level concepts means `revenue` is defined once and reused by any ratio, the benchmark, and ATL — the shared lingua franca. `REGISTRY_VERSION` pins which vocabulary a case/snapshot was resolved against.

## 7. `as_of` query semantics

```
retrieve_evidence(Query(entity, concept, period, as_of)):
  1. entity -> cik            (entities; accepts ticker or CIK)
  2. concept -> ConceptSpec   (unknown concept -> raise ConceptNotFound)
  3. period predicate:
       match key = period.period_end if given (demo path, exercised now),
                   else fy = period.fiscal_year AND fp = period.fiscal_period (benchmark path, later)
       instant  : period_start IS NULL  AND <match key>
       duration : period_start NOT NULL AND <match key>, prefer longest duration (annual)
  4. as_of predicate:  as_of IS NULL  OR  filed <= as_of
  5. for tag in spec.tags (in order):           # first match wins (preserves today's resolver behavior)
       SELECT ... WHERE cik=? AND taxonomy='us-gaap' AND tag=? AND <period> AND <as_of>
       ORDER BY duration_len DESC, filed DESC LIMIT 1
       -> first tag returning a row wins
  6. restated_later = EXISTS(same cik/tag/period with filed > chosen.filed AND val != chosen.val)
  7. no tag matched -> Evidence(found=False, tags_tried=spec.tags)
```

Selection logic (first-tag-wins, longest-duration, instant-vs-duration) is lifted verbatim from today's `_select_fact`. The only new clauses are `filed <= as_of` and the `restated_later` probe.

## 8. Forward-compat decisions

**Field audit rule for "add now":** a field earns its place only if it is ① fillable from companyfacts, ② wanted by a known future consumer (Validate / P4 grader / ATL snapshot / decision log), and ③ cheap now but expensive to retrofit. Fail any one → defer/reject.

Added: `tag`, `tags_tried`, `cik`, `fy`/`fp`, `frame`, `restated_later`, exact value. Rejected: `decimals`/precision (not in companyfacts — never ship a field the source can't honor). Deferred: `label`/`description` (lookup-able), full restatement history (`retrieve_evidence_history`), segment/dimension facts (raw-XBRL ingest).

**S1 — Value exactness.** Store `DECIMAL(38,6)`; expose `value: float` (engine) + `value_exact: Decimal` (grader).

**S2 — `fact_id` <-> benchmark `gold_fact_path`.** `fact_id = sha1` of the canonical tuple `(cik, taxonomy, tag, unit, period_start, period_end, accession)`. The 410-case `gold_fact_path` is a list of sha1 hashes; before building the P4 grader, **diff this recipe against the teammate's case generator and reconcile** so Track-R facts-reached joins by id. (Checkpoint, not this unit.)

**S3 — Batch seam.** Ship `retrieve_evidence_batch`; declare `retrieve_evidence_history` (not built). ATL's backtest is thousands of point queries (N+1 risk).

**S4 — Entity identity.** Key on `cik`; reserve `entity_tickers(cik, ticker, valid_from, valid_to)` for point-in-time symbols; populate current ticker only.

## 9. Resolver migration (the seam that keeps the demo green)

The migration lives entirely below `resolver.py`'s public surface; `engine.py`, `tool.py`, the endpoints, and the Validate button do not change.
- `RATIO_TAG_MAP` deleted → replaced by `RATIO_CONCEPTS` (rename map) + `CONCEPT_REGISTRY`.
- `_cached_find_filing`/`_cached_parse_filing` (local-filings glob+parse) deleted → lazily-opened DuckDB connection.
- Input resolution: `for input, concept in RATIO_CONCEPTS[ratio].items(): ev = retrieve_evidence(Query(ticker, concept, Period(period_end=period))); inputs[input] = ev.value`.
- Source card reads `ev.provenance` (accession / filed / tag) — same card, richer provenance (sets up, but does not do, the pending `xbrl-source-card-styling` task).

## 10. Error handling
- Concept not in registry → `raise ConceptNotFound` (wiring bug; fail loud).
- Metric not reported (all tags miss) → `found=False, tags_tried` → engine treats `None` inputs as "insufficient data," not a false-fail.
- Entity not ingested → `found=False` + log; degrades to "no reliable data."
- Ingest (`ingest.py`, run manually): SEC `User-Agent` header + ~10 req/s; vendored snapshot means tests never hit the network. Re-ingest idempotent (`fact_id` PK dedups; restatements add rows).

## 11. Testing & acceptance (definition of done)
1. **ingest** — vendored AAPL/MSFT/TSLA snapshots → DuckDB; assert `fact_id` stability + restatements coexist.
2. **retrieve** — each demo concept/period returns the known value with full provenance.
3. **as_of invariant** — a `(cik, tag, period)` with ≥2 filings → `as_of` before/after the restatement returns the two different values; `restated_later` correct. (Synthetic 2-version fixture if the demo trio lacks a clean restatement.)
4. **registry guards** — unknown concept raises; unreported metric → `found=False` + `tags_tried`.
5. **integration (oracle)** — the 3 demo Validate questions pass green through the migrated resolver; the existing 30 axiom tests stay green.

**Done = ** demo 3/3 green via the new layer + the `as_of` test proves restatement-awareness + every found fact carries provenance + ingest is idempotent.

## 12. Deferred (YAGNI, not walled off)
P4 grading harness; benchmark-resolve over the 410 cases; S&P 500 breadth ingest; MCP/HTTP wrapper; historical-ticker resolution; caching; dimensional/segment facts; `retrieve_evidence_history`. Each is an additive caller or column, not a rewrite — the contracts and schema are shaped to admit them.

## 13. Open checkpoint
Before P4: reconcile the `fact_id` recipe (S2) against the teammate's case generator so `gold_fact_path` joins by id.
