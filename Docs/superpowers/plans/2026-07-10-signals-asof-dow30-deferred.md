# Deferred items — as_of endpoint (PR A #340) + Dow-30 reconcile (PR B/C)

Deferred-items log for the 2026-07-10 plan pair (`2026-07-10-signals-asof-endpoint.md`, `2026-07-10-dow30-watchlist-reconcile.md`), per the AFK-loop defer rules (D1–D6).

### §PR-A.1 — Producer retention (`prune_artifacts`) is mtime-ordered, not calendar-aware

**Status: RESOLVED 2026-07-11 — prune sort aligned with `(stem_date, mtime, name)` (option a), branch `fix/prune-calendar-aware`.**

**What:** `Heartbeat/news_signals.py:598-613` prunes to `SIGNALS_KEEP_N` newest artifacts by raw `(mtime, name)`. After an old day's artifact is rewritten in place with a fresh mtime (state-file surgery, or crash-recovery reprocessing after extended downtime), the next prune (needs ≥15 artifacts on disk) can evict a calendar-newer artifact while keeping the backfilled older day — a `?as_of=<evicted day>` then 404s for a date inside the nominal retention window. Verified CONFIRMED but narrow: no automated path reprocesses a state-recorded items file; PR A's read path is already hardened (`(stem_date, mtime, name)` selection).

**Why deferred:** D1 — the producer is untouched by PR #340 (`git diff origin/main...feat/signals-asof -- Heartbeat/` was empty); the design spec deliberately scoped retention out (spec §"backfilled/reprocessed" note). Pre-existing property of PR #339.

**Next-session entry point:** `Heartbeat/news_signals.py:607` — align the prune sort with `(stem_date, mtime, name)` (mirroring `Main/backend/api/signals_views.py:_load_artifact`), or document mtime-based retention in the §4.4 contract. Effort: ~1h incl. a regression test.

### §PR-A.2 — Flaky pre-existing DeprecationWarning in test output

**Status: deferred 2026-07-11 in PR #340 (defer rule D1 — unrelated module untouched by the PR)**

**What:** `Main/backend/api/utils/resource_monitor.py:63` — `asyncio.get_event_loop()` raises `DeprecationWarning: There is no current event loop` during `test_artifact_loaded_from_disk_once_per_request` in some runs (nondeterministic; also the lone warning in the 675-test full-suite run). Breaks pristine-output discipline.

**Why deferred:** D1 — `resource_monitor.py` is untouched by PR #340; the triggering test predates the branch.

**Next-session entry point:** `Main/backend/api/utils/resource_monitor.py:63` — replace with `asyncio.get_running_loop()` inside try/except or `asyncio.new_event_loop()` per intent. Effort: ~15 min.

### Triage log — reviewed and intentionally not changed (not deferrals)

- **Split malformed-`as_of` handling** (`signals_views.py` `_get_artifact` swallows → None for `@condition`; view re-parses → 400): plan-mandated verbatim, single caller, pairing documented in both docstrings; inherent to Django's validators-must-not-raise constraint. WONTFIX (polish option: request-stashed parse-error flag).
- **Double `_as_of` parse per request / double `_stem_date` per candidate:** pure CPU string parses, N≤~30, no I/O; restructuring risks the frozen selection-key semantics. WONTFIX.
- **ETag excludes the resolved as_of date:** REFUTED as a defect — `source_items` structurally embeds the batch stem (producer `news_signals.py:626-627` derives the artifact filename from the same string), so distinct days cannot collide. Optional defense-in-depth (fold `newest.name` into the ETag) noted, not needed.
- **Non-dated stems excluded under `as_of` but not under no-param:** intentional per plan; unreachable from the producer (all stems date-prefixed); §4.4 now says "latest **dated** artifact".
- **Test `os.utime` skew boilerplate ×3:** style nit; a `_touch()` helper if the convention ever changes.

### §PR-C.1 — ATL: differently-named Dow-ish copies remain (out of guard scope)

**Status: deferred 2026-07-11 in AgenticTrading#91 (defer rule D1 — modules untouched by the PR; plan-scoped out per spec D4)**

**What:** `DJIA_SYMBOLS` in `dashboard/backend/domain/backtesting/baselines/paper.py:28` and `dashboard/scripts/backtest.py:55`, the list in `dashboard/scripts/alpaca_trader_with_committee.py:33-40` (carries never-members NFLX/TSLA), and the frontend `djia` preset in `dashboard/frontend/app.js:1959` (GE/INTC). All pre-existing and already divergent from the OLD list; the new AST guard covers the `DJIA_30` name only. Also noted: in-flight runs live across the deploy keep their stored symbol allowlist while bar-fetching switches to the new universe; and stored leaderboard baselines need `refresh_leaderboard_baselines.py` re-run post-merge.

**Next-session entry point:** after #91 merges — grep `DJIA_SYMBOLS|djia` in ATL, reconcile or explicitly label each copy, extend the guard; run `dashboard/scripts/refresh_leaderboard_baselines.py`. Effort: ~2h.

### §PR-C.2 — ATL: 3 pre-existing route-contract test failures on origin/main

**Status: deferred 2026-07-11 (defer rule D1 — pre-existing on main, unrelated to the universe change)**

**What:** `test_backtests_router_contract`, `test_full_route_contract_unchanged`, `test_agent_router_route_contract_unchanged` fail identically on untouched origin/main (baseline 3 failed/865 passed) and on the PR head (3 failed/869 passed).

**Next-session entry point:** ATL repo, run the three tests; they assert route contracts that drifted upstream. Effort: unknown; belongs to ATL maintainers.
