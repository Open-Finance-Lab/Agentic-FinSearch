# XBRL Truth Layer P0/P1 — Deferred Items

Items surfaced by the phase-boundary adversarial self-reviews that were **not** fixed
in-phase, with the defer rationale and a single next-session entry point each. Everything
else found by review was fixed in the same phase. Defer-rule labels follow the AFK-dev-loop
convention (D1 unrelated · D2 harness-blindspot · D3 structural-ceiling · D4 runtime-load-bearing
· D5 adversarial-skeptic-veto · D6 out-of-budget/out-of-this-unit).

---

### §7-benchmark — `_restated_later` misses a restatement that SHIFTS the period_end on the `(fy, fp)` path

**Status: deferred 2026-06-26 (Phase B self-review; defer rule D6, secondary D5)**

**What:** On the benchmark `(fiscal_year, fiscal_period)` read path, `_select` resolves a row
by `(fy, fp)` (then `period_end DESC` to pin the closing period), but `_restated_later` probes for
a later-filed change keyed on the *selected row's* `period_end`. If a restatement moves the
period_end itself (52/53-week fiscal calendars whose closing date drifts year to year) and the
query carries an `as_of` between the two filings, the later filing is at a different `period_end`
and the probe misses it, so `restated_later` is `False` when it should be `True`. The demo
`period_end` path is unaffected (selection and probe key on the same `period_end`).

**Why deferred:** (D6) The correct fix is entangled with the benchmark path's full `(fy, fp)`
resolution design, which is explicitly "(later)" in spec §7 and has no consumer or test yet — the
P4 grader is its first real consumer and keys the gold path on `(fy, fp)`. (D5) The review's
one-line suggestion ("probe on `fy/fp`") was adversarially refuted: real data shows `(fy, fp)` is
**not** unique per `period_end` (one filing stamps current + prior-year comparatives with the same
`fy/fp`), so a `(fy, fp)`-keyed probe would over-trigger `restated_later`. A correct fix must first
canonicalize "which period_end does this `(fy, fp)` target" and make the probe consistent with it —
work that belongs with the benchmark path's first test/consumer.

**Next-session entry point:** `Main/backend/truthlayer/retrieve.py:_restated_later` (and the
`(fy, fp)` branch of `_select`). Re-run with a 52/53-week fixture where the restatement shifts
period_end; reconcile against the P4 gold-path `(fy, fp)` semantics before the benchmark grader.
Est. effort: ~half a day, gated on the benchmark/P4 design.

---

### S2/S13 — `fact_id` canonical recipe reconciliation (encoding + field set)

**Status: ✅ RESOLVED 2026-06-26.** The recipe was reconciled against the benchmark
generator and `make_fact_id` was changed to match byte-for-byte.

**How it was reconciled (the generator source is NOT checked in — only its output
`Materials/XBRL Tree/Benchmark/cases_v1_final.json` + the contributor guide).** The
guide documents the field set; the exact serialization was recovered empirically by
reproducing two real `gold_fact_path` sha1s against their SEC `companyfacts` source
fields (a known-plaintext/known-digest recovery). The generator's recipe is:

```
sha1("|".join(str(x) for x in (cik, concept, period_start, period_end, unit, accession, dim_hash)))
```
- `concept` = the raw us-gaap tag (e.g. `Revenues`); `period_start=None` (instant facts)
  → `str()` → `'None'`; `dim_hash=''` for consolidated companyfacts facts (trailing `|`).
- Verified anchors (pinned in `tests/test_truthlayer_store.py::test_make_fact_id_matches_benchmark_gold_path`):
  NVDA FY2026 `Revenues` → `d7a34159f863a4bbbbe3b092a3f9611070cfe5ad`;
  Meta FY2025 `Assets` → `293b650557be02293fc40c70526e46d6b9096ee2`.

**Deltas applied to our recipe (was `cik|taxonomy|tag|unit|pstart|pend|accn`):** dropped
`taxonomy`; moved `unit` to after the period dates; appended `dim_hash` (a real param,
default `''`, so the deferred raw-XBRL dimensional path can populate it without
re-hashing the consolidated facts). The intra-doc collision guard in `ingest_doc` stays
(the field set still omits `fy/fp/frame/value`, matching the generator). Prose was wrong
twice — the contributor guide omitted `period_start`, the cases guide added `value` — so
the empirical hash match is of record.

**concept→tag SELECTION divergence — ✅ RESOLVED 2026-06-27.** The other half of the
S2/S13 join (tag selection, not the hash) is reconciled and exhaustively validated.

*The NVDA alarm was a false positive.* NVDA does report `Revenues` AND (historically)
`RevenueFromContractWithCustomerExcludingAssessedTax`, but **not** the latter for the
FY2026 consolidated period — so first-tag-wins correctly falls through to `Revenues`,
because `retrieve._select` only considers a tag that reports a fact *for the queried
period*. The registry order was never wrong for revenue.

*How it was reconciled (empirically, not from the guide's `CANONICAL_METRICS` prose —
which proved unreliable twice already).* Reversed every `gold_fact_path` hash in
`cases_v1_final.json` against full us-gaap companyfacts for all 197 referenced companies
(harness: `Main/backend/truthlayer/_tagrecover/`, see its README). Recovered: the
generator uses a single **conflict-free global tag-priority order** per concept;
per-company "divergence" is just which tags each company reports. `validate_full.py`
confirms the reconciled registry reproduces the gold tag on **742/742** resolvable
single-entity facts, 0 mismatches.

*Registry deltas (`truthlayer/registry.py`, REGISTRY_VERSION → 2026-06-27):* added 5
benchmark concepts — `net_income` (`NetIncomeLoss`≻`ProfitLoss`), `operating_income`
(`OperatingIncomeLoss`), `gross_profit` (`GrossProfit`), `research_and_development`
(`ResearchAndDevelopmentExpense`), `cash_and_equivalents`
(`CashAndCashEquivalentsAtCarryingValue`); inserted
`RevenueFromContractWithCustomerIncludingAssessedTax` below `Revenues` (CrowdStrike is
the lone gold fact using it); added `BENCHMARK_CONCEPT_ALIAS` +
`resolve_benchmark_concept` mapping the benchmark's own concept names
(`total_assets`→`assets`, `cogs`→`cost_of_revenue`, `stockholders_equity`→`equity`,
`reported_gross_profit`→`gross_profit`, …). Existing orders (revenue, cogs, equity,
assets) were already correct — verified, unchanged. Pinned offline by
`tests/test_truthlayer_benchmark_selection.py` (5 real gold anchors + order/alias).

**⚠️ NEW open item surfaced during reconciliation — accession DRIFT (gating for breadth
ingest / P4).** For ~33 facts across a few companies (BLK, CEG, CRWV) the *entire*
`gold_fact_path` points to accessions that **live** SEC companyfacts no longer returns:
the benchmark's gold was generated against a **frozen** companyfacts snapshot since
drifted. Because `fact_id` includes `accession`, a grader re-fetching from live SEC mints
different ids than gold for any drifted fact. **Breadth ingest for P4 must load the
benchmark's own frozen snapshot (`xbrl.duckdb`), not a live re-fetch.** This is the next
gating fact-join risk, downstream of (not the) tag selection. Entry point: source the
benchmark `xbrl.duckdb` (487,623 facts / 246 cos — not checked in) before the P4 grader.

**⚠️ Forward risk for breadth ingest — `taxonomy` dropped from the key + multi-taxonomy
docs.** The reconciled recipe drops `taxonomy` (the generator's key is us-gaap-only), but
`ingest.companyfacts_rows` still iterates *every* taxonomy in a companyfacts doc
(`us-gaap`, `dei`, `srt`, …). For the demo trio this is moot — `_prune` keeps only
us-gaap. But an unpruned breadth ingest could mint the same `fact_id` for two facts that
differ only by taxonomy (same tag name + period + unit + accession + value across, say,
`us-gaap` and `srt`). This is **caught loud, not silent**: the intra-doc collision guard
in `ingest_doc` raises rather than `ON CONFLICT DO NOTHING`. Mitigation is to ingest from
the benchmark's frozen `xbrl.duckdb` (already us-gaap facts) rather than re-deriving from
multi-taxonomy companyfacts; if a future path must ingest raw companyfacts, filter to
`us-gaap` first (or fold `taxonomy`/`dim_hash` into the key for the non-benchmark store).

---

### S2/S13 (historical) — original defer rationale

**Status: deferred 2026-06-26 (Phase A + Phase B self-reviews; defer rule D6 — spec-scheduled checkpoint)**

**What:** Two latent properties of `make_fact_id` (`store.py`), both unreachable on the current
vendored AAPL/MSFT/TSLA data (verified: 0 collisions, no `|` in any field):
  1. **Encoding** — `"|".join(str(x))` has no escaping (a field containing `|` would bleed) and
     serializes `None` as the literal `"None"`; an external generator using different escaping/NULL
     conventions would not join byte-for-byte.
  2. **Field set** — the canonical tuple omits `fy/fp/frame/value`, so one accession that tags a
     single period both `FY/CYxxxx` and `Q4/CYxxxxQ4` produces two facts with the *same* `fact_id`.
     Phase B added a fail-loud guard in `ingest_doc` (raises on intra-doc collision) so this can
     never silently drop a row — but folding the distinguishing field into the recipe is the real fix.

**Why deferred:** (D6) Spec S2/S13 explicitly schedule this as a checkpoint *before* the P4 grader:
"diff this recipe against the teammate's case generator and reconcile so `gold_fact_path` joins by
id. (Checkpoint, not this unit.)" Changing the recipe now (escaping or field set) would change every
`fact_id` and risk diverging from the teammate's generator — the exact divergence the reconciliation
exists to prevent. The recipe is therefore left as the simple documented default and **pinned** by a
golden-hash test so it cannot drift silently.

**Next-session entry point:** `Main/backend/truthlayer/store.py:make_fact_id` (docstring documents the
couplings) and `tests/test_truthlayer_store.py::test_make_fact_id_golden` (the pinned hash). Before P4:
reconcile encoding + field set against the teammate's `gold_fact_path` generator; if the field set
gains `fy/fp/frame`, the `ingest_doc` collision guard becomes inert and can be relaxed.

---

### Forward-compat note (not a defect) — canonical-unit policy

**Status: noted 2026-06-26 (Phase B self-review) — works correctly for current scope, no action needed now**

**What:** `_select` prefers `unit = 'USD'` via an `ORDER BY` tiebreak when a tag reports one period in
several units. This is correct for the all-USD demo scope and is a no-op for a per-share concept (no
USD row). When per-share/EPS (`USD/shares`) or multi-currency concepts are added, the unit should
become a first-class part of the concept registry / `Query`, rather than a hard-coded ORDER BY policy.

**Next-session entry point:** `truthlayer/registry.py` (add a unit/units field to `ConceptSpec`) and
`truthlayer/retrieve.py:_select` (thread the concept's expected unit instead of the `'USD'` default).
Trigger: first per-share or non-USD concept added to `CONCEPT_REGISTRY`.
