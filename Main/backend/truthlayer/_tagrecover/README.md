# `_tagrecover` — benchmark concept→tag selection conformance harness

Investigation + conformance tooling (NOT imported by the package) used to reconcile
`CONCEPT_REGISTRY` against the benchmark generator's tag-selection rule (spec S2/S13,
the half the `fact_id` hash reconciliation did not cover). Kept because conformance to
the generator's tag choice is what Track-R facts-reached grading depends on — a future
P4-grader author will want to re-prove it.

## Why this exists

`cases_v1_final.json` stores, per concept, exactly one us-gaap tag *inside* each
`gold_fact_path` sha1 — the tag is never in plaintext. If our resolver SELECTS a
different tag than the generator stored, the hashes don't join and Track-R fails even
with a byte-perfect recipe. So the rule had to be recovered by reversing the hashes.

## The three scripts (run in order)

1. `fetch.py` — fetch full **us-gaap** companyfacts for every company referenced by the
   benchmark (197: 182 single-entity + 15 cross-entity comparison tickers) to
   `companyfacts/` (gitignored, ~760 MB). Resumable, rate-limited ~8 req/s. Must be
   **unpruned** us-gaap — the vendored package snapshots are pruned to registry tags and
   therefore hide the competitor tags we are trying to discover.
2. `recover.py` — for every `(case, required_fact value)`, find the us-gaap fact(s)
   reporting that value, hash each candidate with the reconciled recipe, keep the one whose
   hash ∈ `gold_fact_path`. Emits the concept→chosen-tag distribution, the pairwise
   priority constraints (`chosen ≻ sibling`, only countable when one company reports both
   value-equal), and per-company divergence.
3. `validate_full.py` — exhaustive check: does the reconciled `CONCEPT_REGISTRY`
   (first-present-tag-wins over the tags the company reports at the gold fact's period+unit)
   select the SAME tag the generator stored, for every resolvable fact?

## Result (2026-06-27)

- **742 / 742** resolvable single-entity facts: registry reproduces the gold tag. **0
  mismatches** across all 9 single-fact concepts.
- The generator uses a single, **conflict-free global tag-priority order** per concept;
  per-company "divergence" is just which tags a company happens to report. Our
  first-present-tag-wins (`retrieve._select` filters tag by period presence) reproduces it.
- Unmapped keys are only the **derived ratios** (`gross_margin`, `net_margin`,
  `asset_turnover`, `equity_multiplier`) — Track-C computations, not stored facts.

The result is pinned offline (no network) by
`tests/test_truthlayer_benchmark_selection.py` (5 gold anchors + order/alias pins).

## ⚠️ Accession drift — do NOT use this cache for breadth ingest

For a few companies (BLK, CEG, CRWV …, ~33 facts) the **entire** `gold_fact_path` points
to accessions that *live* SEC companyfacts no longer returns — the benchmark's gold was
generated against a **frozen** companyfacts snapshot since drifted. `fact_id` includes
`accession`, so a grader that re-fetches from live SEC will mint different ids than gold
for any drifted fact. **Breadth ingest for the P4 grader must come from the benchmark's
own frozen snapshot (`xbrl.duckdb`), not a live re-fetch.**
