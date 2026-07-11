# Deferred items — as_of endpoint (PR A #340) + Dow-30 reconcile (PR B/C)

Deferred-items log for the 2026-07-10 plan pair (`2026-07-10-signals-asof-endpoint.md`, `2026-07-10-dow30-watchlist-reconcile.md`), per the AFK-loop defer rules (D1–D6).

### §PR-A.1 — Producer retention (`prune_artifacts`) is mtime-ordered, not calendar-aware

**Status: RESOLVED 2026-07-11 — prune sort aligned with `(stem_date, mtime, name)` (option a), branch `fix/prune-calendar-aware`.**

**What:** `Heartbeat/news_signals.py:598-613` prunes to `SIGNALS_KEEP_N` newest artifacts by raw `(mtime, name)`. After an old day's artifact is rewritten in place with a fresh mtime (state-file surgery, or crash-recovery reprocessing after extended downtime), the next prune (needs ≥15 artifacts on disk) can evict a calendar-newer artifact while keeping the backfilled older day — a `?as_of=<evicted day>` then 404s for a date inside the nominal retention window. Verified CONFIRMED but narrow: no automated path reprocesses a state-recorded items file; PR A's read path is already hardened (`(stem_date, mtime, name)` selection).

**Why deferred:** D1 — the producer is untouched by PR #340 (`git diff origin/main...feat/signals-asof -- Heartbeat/` was empty); the design spec deliberately scoped retention out (spec §"backfilled/reprocessed" note). Pre-existing property of PR #339.

**Resolution (2026-07-11):** option (a) taken — `prune_artifacts` now sorts by `(stem_date, mtime, name)` via a producer-side `_stem_date` twin (name-string input); 3 `TestPrune` additions; VERSION `2026-07-11.1` + fixture regen; §4.4 retention sentence amended. The canary's staleness notion stays pure-mtime by design.

### §PR-A.3 — Pre-existing ResourceWarning: unclosed `.lock` file in heartbeat suite

**Status: RESOLVED 2026-07-11 — `main()` context-manages the `.lock` handle, branch `fix/heartbeat-lock-resourcewarning`. The leak was in the production path, not the test: `main()` opened the handle and closed it on no exit path (all three of held-lock/missing-key/sweep leaked; finalizer batching made the warnings surface 3× inside `test_held_lock_exits_three`). The with-block spans the whole sweep so flock semantics are unchanged — the lock still guards the full run, releasing on close instead of process-exit teardown. Regression test captures warnings around the held-lock path and asserts no ResourceWarning; suite 151, warning-free. VERSION `2026-07-11.2` + fixture regen (deployed-file change; artifacts identify their generator).**

**What:** `TestMain.test_held_lock_exits_three` emits 3× `ResourceWarning: unclosed file ...signals/.lock` in the full heartbeat suite run. Unrelated to the §PR-A.1 change; breaks pristine-output discipline the same way §PR-A.2 did for the backend suite.

**Next-session entry point:** `Heartbeat/tests/test_news_signals.py` (`TestMain.test_held_lock_exits_three`) — close or context-manage the lock file handle the test (or the code path it exercises) leaves open. Effort: ~15 min.

### §PR-A.2 — Flaky pre-existing DeprecationWarning in test output

**Status: RESOLVED 2026-07-11 — `get_running_loop()` with quiet-zero fallback, branch `fix/resource-monitor-event-loop`. Extension: twin `views.py:565/748` save/restore captures fixed in the same PR; remaining call sites logged as §PR-A.4.**

**What:** `Main/backend/api/utils/resource_monitor.py:63` — `asyncio.get_event_loop()` raises `DeprecationWarning: There is no current event loop` during `test_artifact_loaded_from_disk_once_per_request` in some runs (nondeterministic; also the lone warning in the 675-test full-suite run). Breaks pristine-output discipline.

**Why deferred:** D1 — `resource_monitor.py` is untouched by PR #340; the triggering test predates the branch.

**Next-session entry point:** `Main/backend/api/utils/resource_monitor.py:63` — replace with `asyncio.get_running_loop()` inside try/except or `asyncio.new_event_loop()` per intent. Effort: ~15 min.

### §PR-A.4 — Two remaining get_event_loop call sites with get-or-create semantics

**Status: deferred 2026-07-11 (found during §PR-A.2 execution; neither warns in the current suite)**

**What:** `Main/backend/datascraper/openai_search.py:794` and `Main/backend/mcp_client/mcp_manager.py:73` still call `asyncio.get_event_loop()`. Unlike the fixed sites, these *rely* on get-or-create semantics (`mcp_manager` stores `self._loop` for later use), so a mechanical `get_running_loop()` swap could change behavior; each needs its own intent analysis. Python 3.14 makes the implicit creation an error, so these must be revisited before any 3.14 upgrade.

**Next-session entry point:** trace how `openai_search.py:794`'s `loop` and `mcp_manager.py:73`'s `self._loop` are consumed; replace with explicit `new_event_loop()`/`asyncio.run()` ownership or a running-loop requirement per intent. Effort: ~1h.

### Triage log — reviewed and intentionally not changed (not deferrals)

- **Split malformed-`as_of` handling** (`signals_views.py` `_get_artifact` swallows → None for `@condition`; view re-parses → 400): plan-mandated verbatim, single caller, pairing documented in both docstrings; inherent to Django's validators-must-not-raise constraint. WONTFIX (polish option: request-stashed parse-error flag).
- **Double `_as_of` parse per request / double `_stem_date` per candidate:** pure CPU string parses, N≤~30, no I/O; restructuring risks the frozen selection-key semantics. WONTFIX.
- **ETag excludes the resolved as_of date:** REFUTED as a defect — `source_items` structurally embeds the batch stem (producer `news_signals.py:626-627` derives the artifact filename from the same string), so distinct days cannot collide. Optional defense-in-depth (fold `newest.name` into the ETag) noted, not needed.
- **Non-dated stems excluded under `as_of` but not under no-param:** intentional per plan; unreachable from the producer (all stems date-prefixed); §4.4 now says "latest **dated** artifact".
- **Test `os.utime` skew boilerplate ×3:** style nit; a `_touch()` helper if the convention ever changes.

### §PR-C.1 — ATL: differently-named Dow-ish copies remain (out of guard scope)

**Status: RESOLVED 2026-07-11 — AgenticTrading PR #94 opened (awaiting human merge), branch `fix/dow-copies-reconcile`, plan `2026-07-11-atl-dow-copies-reconcile.md`.** All four copies collapsed onto canonical `validator.DJIA_30` (`paper.py`/`backtest.py` → `list(DJIA_30)`, committee bot default → `list(DJIA_30)` still ⭐-customizable, app.js `djia` preset → canonical 30); guard extended (any Dow-ish name via AST in the 3 scripts + paper.py, JS preset pinned textually). Ship-gate READY TO MERGE, suite 885/2skip. Baselines refresh NOT run: prod Render still served pre-#91 code (old universe in `/api/v2/schema`) — after redeploy, `curl "https://agentictrading.onrender.com/api/v1/leaderboard?refresh=true"` (idempotent; INSERT OR REPLACE on deterministic run_ids) replaces `refresh_leaderboard_baselines.py` for the unchanged contest window.

**What:** `DJIA_SYMBOLS` in `dashboard/backend/domain/backtesting/baselines/paper.py:28` and `dashboard/scripts/backtest.py:55`, the list in `dashboard/scripts/alpaca_trader_with_committee.py:33-40` (carries never-members NFLX/TSLA), and the frontend `djia` preset in `dashboard/frontend/app.js:1959` (GE/INTC). All pre-existing and already divergent from the OLD list; the new AST guard covers the `DJIA_30` name only. Also noted: in-flight runs live across the deploy keep their stored symbol allowlist while bar-fetching switches to the new universe; and stored leaderboard baselines need `refresh_leaderboard_baselines.py` re-run post-merge.

**Next-session entry point:** after #91 merges — grep `DJIA_SYMBOLS|djia` in ATL, reconcile or explicitly label each copy, extend the guard; run `dashboard/scripts/refresh_leaderboard_baselines.py`. Effort: ~2h.

### §PR-C.2 — ATL: 3 pre-existing route-contract test failures on origin/main

**Status: deferred 2026-07-11 (defer rule D1 — pre-existing on main, unrelated to the universe change)**

**What:** `test_backtests_router_contract`, `test_full_route_contract_unchanged`, `test_agent_router_route_contract_unchanged` fail identically on untouched origin/main (baseline 3 failed/865 passed) and on the PR head (3 failed/869 passed).

**Next-session entry point:** ATL repo, run the three tests; they assert route contracts that drifted upstream. Effort: unknown; belongs to ATL maintainers.

**Investigation (2026-07-11, read-only — full report: `2026-07-11-atl-route-contract-investigation.md`):** all three are **intentional drift, not regressions** — real feature commits added routes without updating the frozen contract fixtures in `test_app_composition.py`/`test_router_move.py`: `d5307c5` (chart-data API, Jul 9; updated `EXPECTED_BACKTESTS_ROUTES` but not the sibling `EXPECTED_FULL_CONTRACT` in the same file), `30b1b73` (Agent Edit UI, Jul 9; `PATCH /v1/agents/{agent_id}` missing from `EXPECTED_AGENT_ROUTES`), `a7c392d` (runs/trades endpoint, Jul 10; missing from `EXPECTED_BACKTESTS_ROUTES`). Fix = add the missing route triples to the three `EXPECTED_*` fixtures (~15 min, ATL maintainers). No route was removed/renamed/behavior-changed; confidence high. Repro caveat: repo-wide `pytest -k` hits an unrelated INTERNALERROR (`orchestration/FinAgents/memory_testing/latency_test.py` calls `exit()` at import); scope to the two test files.
