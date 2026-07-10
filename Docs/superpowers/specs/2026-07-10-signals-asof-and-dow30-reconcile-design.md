# Design — Signals `as_of` endpoint + Dow-30 watchlist reconcile

- **Date:** 2026-07-10
- **Status:** Approved (design); implementation pending
- **Author:** FlyMiss + Claude
- **Queued tickets:** `finsearch-signals-as-of-endpoint-01` (P1), `finsearch-djia30-watchlist-reconcile-01` (P2, cross-repo)
- **Relates to:**
  - `Main/backend/api/signals_views.py` — the `/api/signals/news/` endpoint (item 1)
  - `Heartbeat/news_signals.py`, `Heartbeat/news_heartbeat.py` — watchlist + signals writer (item 2)
  - `Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md` §4.4 (signals endpoint contract), Decision **D2** (union watchlist)
  - ATL repo `agent-trading-lab` — `dashboard/backend/infrastructure/llm/validator.py` `DJIA_30` (+ 2 stale copies), and its API/data layers (item 2, aligned to this design)

## Motivation

Two items to bring Agentic FinSearch closer to integration into the Agent Trading Lab (ATL). Both are small in code but were previously blocked on facts the code alone doesn't tell you: the on-disk artifact naming scheme, and the *actual* current Dow-30. This spec records those facts and the design that follows.

**Guiding principle for the cross-repo item:** FinSearch's designs are canonical. ATL's API layer and data layer are *aligned to match* — not the other way around.

---

## Ground truth — current Dow-30

Verified 2026-07-10 against three independent sources (S&P Dow Jones Indices press release, Wikipedia components table, stockanalysis.com — all agree; a stale SEO aggregator that disagreed was discarded):

```
AAPL AMGN AMZN AXP BA CAT CRM CSCO CVX DIS
GOOGL GS HD HON IBM JNJ JPM KO MCD MMM
MRK MSFT NKE NVDA PG SHW TRV UNH V WMT
```

Recent composition changes: **2026-06-29** GOOGL replaced VZ; **2024-11-08** NVDA + SHW replaced INTC + DOW; **2024-02-26** AMZN replaced WBA. (HON kept its ticker through its 2026-06-29 aerospace spin-off / rename to "Honeywell Technologies"; the spun-off unit trades as HONA and is *not* a Dow member.)

### Correction to the P2 ticket's premise

The ticket asked to add `{AMGN, CRM, DOW, HON}` to the live watchlist. Reconciling against the real index shows this is itself wrong:

- **DOW is NOT a current member** — removed 2024-11-08. Adding it would inject a new error. (The ticker being literally named "DOW" is the exact trap that "reconcile against a real source, not a broken constant" was meant to catch.)
- **SHW (Sherwin-Williams) is a current member the ticket missed** — added 2024-11-08.

This validated the decision to reconcile against the index, not patch by hand.

---

## Item 1 — `?as_of=YYYY-MM-DD` on `GET /api/signals/news/`

### Current behavior (`signals_views.py`)

`_load_latest()` globs `signals-*.json` in `settings.SIGNALS_DIR`, selects the newest by `max(key=(mtime, name))`, validates (`generated_at` tz-aware, `signals` is a dict), and fails closed to `None` (→ `404 {"error":"no_signals"}`). All three of `_etag`, `_last_modified`, and the view body read the artifact through the memoized `_get_artifact(request)` (one disk load per request, since `@condition` runs the validators before the body). The ETag is `"{generated_at}|{source_items}|{tickers}"`.

On-disk scheme (from `news_signals.py` writer + live droplet): one `signals-YYYY-MM-DD.json` per calendar day; same-day supplemental reruns produce `signals-YYYY-MM-DD-HHMMSS.json`. `SIGNALS_KEEP_N` (default 14) retains the newest N dated artifacts. The droplet currently holds a contiguous ~15-day run — enough history for `as_of` to serve.

### Semantics — point-in-time, on-or-before

`as_of=D` resolves to the newest artifact whose **filename stem date ≤ D**. Rationale: point-in-time is the standard financial "state of knowledge as of date D" semantic — no lookahead, robust to weekend/missed-run gaps (a day-by-day backtester never hits a 404 mid-range), and matches what a live consumer would have had.

- **Resolution basis = filename stem date, not the JSON `generated_at`.** The filename is the authoritative "which day this batch is for"; matching on it avoids opening every candidate and reuses the existing `(mtime, name)` tiebreak for same-day supplementals.
- **Absent `as_of`** → unchanged (newest artifact overall).
- **Malformed `as_of`** (not strict `YYYY-MM-DD`) → `400 {"error":"bad_as_of"}`.
- **`as_of` earlier than all retained artifacts** → `404 {"error":"no_signals"}`.
- **`as_of` in the future / more recent than latest** → returns latest (consistent with on-or-before).
- **Gap detection is the client's job**, via the returned `generated_at` (compare to the requested date). `staleness_hours` keeps its current meaning (relative to `now`) — unchanged contract, no response-schema change.

### Code changes (`Main/backend/api/signals_views.py`)

1. **`_as_of(request)`** — mirrors `_tickers_filter`. Returns `None` (absent), a `datetime.date`, or raises `ValueError` on malformed input. Strict `^\d{4}-\d{2}-\d{2}$` guard then `date.fromisoformat`.
2. **`_stem_date(path)`** helper — parses the leading `YYYY-MM-DD` of `signals-<...>.json`; returns a `date` or `None` (non-dated stems like `signals-a.json` → `None`, skipped).
3. **`_load_latest()` → `_load_artifact(as_of=None)`** — when `as_of` is set, filter `candidates` to `{p : _stem_date(p) is not None and _stem_date(p) <= as_of}` before selection + validation. **Amended 2026-07-10 (plan review):** under `as_of`, selection orders by `(stem_date, mtime, name)` — calendar date first — so a backfilled/reprocessed older-day artifact (rewritten in place with a fresh mtime) can never outrank a newer-dated one inside the window; the no-param path keeps today's pure `(mtime, name)` order.
4. **`_get_artifact(request)`** — parse `as_of` once (catch `ValueError` → treat as `None` artifact so `@condition`'s `_etag`/`_last_modified` never raise); memoize as today.
5. **View body** — before the `404` check, re-parse `as_of`; on `ValueError` return `400 {"error":"bad_as_of"}`.

### Why caching is correct for free

The ETag already keys on `generated_at|source_items`, which uniquely identifies an artifact. `as_of` only changes *which* artifact is selected, so:
- Two `as_of` values resolving to **different** days → different `generated_at` → different ETag/Last-Modified. ✓
- Two `as_of` values resolving to the **same** artifact → identical ETag → correctly share a cache entry / 304. ✓
- Malformed `as_of` → `_etag`/`_last_modified` return `None` (no artifact) → no conditional short-circuit → view returns `400`. ✓

No change to the ETag string is required.

### Tests (`Main/backend/tests/test_signals_endpoint.py`)

`SimpleTestCase` + `override_settings(SIGNALS_DIR=<tmp>)`, building dated fixtures:
- exact-date hit; gap → nearest-earlier artifact; `as_of` before all history → 404; malformed → 400; future `as_of` → latest.
- same-day supplemental (`signals-D-HHMMSS.json`) selected correctly under `as_of=D`.
- ETag differs across `as_of` days; matches when two `as_of` resolve to the same artifact.
- `as_of` + `tickers` compose (filtered signals for a historical day).

### Docs

Update signals-endpoint contract (§4.4 in the 2026-07-06 spec) + `Main/backend` README/API notes to document `?as_of` — including the retention-bounded history depth (`SIGNALS_KEEP_N`, default 14: older dates 404 identically to "never produced") and that the per-date ticker set can change across the retained window (e.g. a watchlist deploy).

---

## Item 2 — Dow-30 watchlist reconcile (cross-repo + droplet)

### Target FinSearch watchlist = default extras ∪ Dow-30 = 34

```
AAPL AMGN AMZN AXP BA BRK-B BTC-USD CAT CRM CSCO CVX DIS GOOGL GS HD HON
IBM JNJ JPM KO MCD META MMM MRK MSFT NKE NVDA PG SHW TRV TSLA UNH V WMT
```

vs. the **live 35** on the droplet: **adds** `{AMGN, CRM, HON, SHW}`, **removes stale** `{INTC, MA, PFE, WBA, XOM}`. The 4 non-Dow extras `{META, TSLA, BRK-B, BTC-USD}` are kept (FinSearch's own community-digest universe). This is a superset of ATL's Dow-30 universe, so ATL can request its subset via `?tickers=`.

### FinSearch encoding — codify in code + parity test

The heartbeat scripts are **stdlib-only, single-file by design** (both docstrings state this; the deploy workflow `heartbeat-tests.yml` fetches exactly `news_heartbeat.py` and `news_signals.py`, each sha256-verified). A shared module would break that contract and require a workflow edit — so instead we duplicate the canonical list and guard it with a parity test, matching the existing `schema-parity` anti-drift idiom (`OUTPUT_CAPS`/`DIAGNOSTIC_FIELDS`).

In **both** `Heartbeat/news_signals.py` and `Heartbeat/news_heartbeat.py`, replace the hardcoded `DEFAULT_WATCHLIST = "AAPL MSFT ... BTC-USD"` string with:

```python
# Dow Jones Industrial Average constituents.
# Source: S&P Dow Jones Indices; effective 2026-06-29 (GOOGL replaced VZ).
# Reconcile against the official index — never a hand-maintained copy — when
# the composition changes. Parity-tested against the sibling heartbeat script.
DOW_30 = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
]
# Non-Dow tickers FinSearch tracks for its own community digests.
WATCHLIST_EXTRAS = ["META", "TSLA", "BRK-B", "BTC-USD"]
DEFAULT_WATCHLIST = " ".join(sorted(set(DOW_30) | set(WATCHLIST_EXTRAS)))
```

**To add/remove a company = edit one list** (`DOW_30` or `WATCHLIST_EXTRAS`); the watchlist is derived. This is the sustainability requirement.

**Addendum (2026-07-10 plan review):** reconciling the watchlist also requires reconciling `news_signals.py`'s `TICKER_ALIASES` — the subject-relevance gate (D8) matches name-based headlines through it, so a ticker missing an alias is only half-added (and `warn_alias_gaps()` WARNs every sweep). Add `AMGN/CRM/HON/SHW` aliases and drop the stale `INTC/MA/PFE/WBA/XOM` entries (user decision), leaving the table keyed by exactly `DOW_30 ∪ WATCHLIST_EXTRAS` (parity-test-guarded).

**Parity test** (`Heartbeat/tests/`): assert `news_signals.DOW_30 == news_heartbeat.DOW_30`, `WATCHLIST_EXTRAS` likewise, `DEFAULT_WATCHLIST` identical; `len(DOW_30) == 30`, all unique/upper; `"AMEX" not in DOW_30`; extras disjoint from `DOW_30`.

Also update `Heartbeat/.env.heartbeat.example` and `Heartbeat/README.md`'s documented union string to the 34-list.

### ATL — collapse 3 `DJIA_30` copies to one source (`agent-trading-lab`)

Current state: `dashboard/backend/infrastructure/llm/validator.py:25` `DJIA_30` (imported by 10 modules), a **byte-identical duplicate** in `dashboard/scripts/backtest_hourly_agent.py:91` (despite a "keep in sync" comment that only imports `TOP_10_STOCKS`), and a **divergent** copy in `docs/examples/simple_trading_agent_backtest.py:34` (uses `AMGN` where the others have `AMEX`). All three are stale (missing `{AMGN, AMZN, CRM, GOOGL, HON, SHW}`, carrying non-members `{MA, PFE, INTC, XOM, AMEX, WBA}`).

> **Correction (2026-07-10 plan review):** the docs-example "third copy" above described the stale working tree of branch `docs/correct-engines-services-deleted-shims`; on `origin/main`, commit `13a2b64` (2026-07-06) rewrote `docs/examples/simple_trading_agent_backtest.py` into a MAG7 example with **zero** `DJIA_30` references. The collapse is therefore **2 copies → 1 source** (validator + `backtest_hourly_agent.py`); the plan's Task B5 verifies the example's non-involvement instead of mirroring a list into it.

- Fix `validator.py` `DJIA_30` to the correct 30 (source/date comment, matching FinSearch's list).
- `backtest_hourly_agent.py`: **import** `DJIA_30` from validator instead of hardcoding (finishes the half-done refactor). 2 copies → 1 source (per the correction above, the docs example on `origin/main` carries none).
- Add a guard test (30 unique, no `AMEX`, no stale divergence).
- **Align ATL's data layer**: ensure price data exists for the newly-added constituents `{AMGN, AMZN, CRM, GOOGL, HON, SHW}` over ATL's backtest window (per user: ATL's data + API layers are aligned to this design).
- **Align ATL's API layer**: `dashboard/backend/api/v2/models.py` (the frozen consumer contract) imports `DJIA_30` from validator, so correcting validator propagates; confirm the contract/models reflect the corrected universe.
- Correcting `DJIA_30` **will change** `equal_weight_index` / `equal_weight_buyhold` / `mean_variance` baseline + leaderboard results (desired, but note it in the ATL PR / changelog).

**Out of scope:** the separate `DJIA_SYMBOLS` constant (2 copies in `backtest.py` / `baselines/paper.py`, a third stale universe with VZ/NFLX/TSLA). Flagged only; leaving it avoids shifting paper-trading baselines. Track as a follow-up if a single ATL universe is later wanted.

---

## Rollout / PR structure

- **PR A (FinSearch):** item 1 — `signals_views.py` `as_of` + tests + endpoint-contract docs.
- **PR B (FinSearch):** item 2 — `DOW_30`/`WATCHLIST_EXTRAS`/`DEFAULT_WATCHLIST` in both heartbeat scripts + parity test + `.env.heartbeat.example` + README union string.
- **PR C (ATL `agent-trading-lab`):** `DJIA_30` unification (fix validator, import in the other two, guard test) + data-layer/API-layer alignment for the 6 new constituents. Links to this spec.
- **Droplet op (manual, prod — confirm before touching):** after PR B auto-deploys the corrected code default and it's verified on the droplet, **remove** the stale `HEARTBEAT_WATCHLIST` override from `/home/deploy/fingpt/envs/.env.heartbeat` (back up first: `.env.heartbeat.bak-<date>`) so code becomes the single source. Sequencing this way avoids a stale window — the existing override covers the gap until correct code lands, then removal hands control to the correct default. Watchlist change takes effect on the next signals sweep (~20 min).

PRs A and B are independent and can land in either order. PR C is independent of A/B on the code side but semantically depends on the corrected Dow-30 defined here.

## Testing summary

- Item 1: endpoint unit tests above (Django `SimpleTestCase`, hermetic tmp `SIGNALS_DIR`).
- Item 2 FinSearch: watchlist parity + invariants test (`python3 -m unittest discover -s Heartbeat/tests`).
- Item 2 ATL: `DJIA_30` guard test + existing backtest/leaderboard suites (expect baseline-number changes; update fixtures/snapshots as needed).

## Decisions log

- **D1** `as_of` semantics = point-in-time on-or-before (not exact-date-404). *User.*
- **D2** Watchlist target = default extras ∪ real Dow-30 = 34 (not Dow-30-only, not additive superset). *User.*
- **D3** Encoding = codify `DOW_30` in code + parity test; single-file contract preserved (no shared module). *User + deploy-topology constraint.*
- **D4** ATL scope = collapse the 3 `DJIA_30` copies only; `DJIA_SYMBOLS` left as flagged follow-up. *User.*
- **D5** ATL API + data layers aligned to this design (FinSearch canonical); removes the price-coverage risk as a *gate*, folds it in as an alignment task. *User.*
