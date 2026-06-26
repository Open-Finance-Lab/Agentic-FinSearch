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
