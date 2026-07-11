# ATL route-contract failures — investigation (Task 3 / §PR-C.2)

Repo: `/mnt/d/Github/agent-trading-lab`, branch `fix/djia30-current-index` (read-only; identical failures confirmed on `origin/main`).

Reproduction command (must scope to the two test files directly — running with `-k` over the whole repo triggers an unrelated pytest INTERNALERROR because `orchestration/FinAgents/memory_testing/latency_test.py` calls `exit()` at import time during collection):

```bash
~/.venvs/atl/bin/python -m pytest \
  dashboard/backend/tests/test_app_composition.py \
  dashboard/backend/tests/test_router_move.py \
  -k "test_backtests_router_contract or test_full_route_contract_unchanged or test_agent_router_route_contract_unchanged" -vv
```

All 3 fail with the same shape: the *live* router/app has extra `(method, path[, name])` triples that the frozen `EXPECTED_*` fixture sets don't list. In every case the diff is "Extra items in the **left** (actual) set" — nothing is missing, nothing changed shape, only additions. That is the signature of an intentional upstream feature landing without the parallel contract-fixture update, not a route regression.

---

## Test 1 — `test_backtests_router_contract` (dashboard/backend/tests/test_app_composition.py:210-211)

**Extra actual route:** `('GET', '/runs/{run_id}/trades', 'get_run_trades')`

**Root cause:** commit `a7c392d` ("1", Jul 10 00:52 -0400, author JinBoatus1) added `get_run_trades` / `GET /runs/{run_id}/trades` to `dashboard/backend/api/routers/backtests.py` (trade-log endpoint for a backtest run). The commit updated the *behavioral* test file `dashboard/backend/tests/test_backtests_router.py` (added fixtures/tests for live-progress status) but never touched `dashboard/backend/tests/test_app_composition.py`, which holds `EXPECTED_BACKTESTS_ROUTES`. Confirmed via `git log -S"get_run_trades" -- dashboard/backend/api/routers/backtests.py` → only `a7c392d`; the contract file's last edit (`d5307c5`, Jul 9 23:36) predates it.

**Verdict:** (a) intentional route addition, contract fixture simply not updated.

**Recommended fix:** add `("GET", "/runs/{run_id}/trades", "get_run_trades")` to `EXPECTED_BACKTESTS_ROUTES` in `test_app_composition.py`.

**Confidence:** high.

---

## Test 2 — `test_full_route_contract_unchanged` (dashboard/backend/tests/test_app_composition.py:218-226)

**Extra actual routes (3):**
- `('GET', '/api/backtest/{run_id}/chart-data')`
- `('PATCH', '/api/v1/agents/{agent_id}')`
- `('GET', '/runs/{run_id}/trades')`

**Root cause — mixed, all three are additive drift already covered above/below, plus one internal inconsistency worth flagging separately:**
- `/runs/{run_id}/trades` — same cause as Test 1 (`a7c392d`).
- `/api/v1/agents/{agent_id}` PATCH — same cause as Test 3 (`30b1b73`, see below).
- `/api/backtest/{run_id}/chart-data` — added by commit `d5307c5` ("Add backtest chart-data API for Playground equity curves.", Jul 9 23:36 -0400). That commit's diff to `test_app_composition.py` shows it added the route to `EXPECTED_BACKTESTS_ROUTES` (line 38) but never added the equivalent entry to `EXPECTED_FULL_CONTRACT` in the *same file* — a partial/incomplete fixture update, not a missed one. (This is why `test_backtests_router_contract` does NOT flag chart-data as extra, but `test_full_route_contract_unchanged` does — the two fixtures live in the same file and simply disagree with each other now.)

**Verdict:** (a) intentional route additions in all 3 cases; the chart-data sub-case is specifically a self-inconsistent contract file (one fixture updated, its sibling in the same commit was not).

**Recommended fix:** add all 3 entries to `EXPECTED_FULL_CONTRACT`: `("GET", "/api/backtest/{run_id}/chart-data")`, `("PATCH", "/api/v1/agents/{agent_id}")`, `("GET", "/runs/{run_id}/trades")`.

**Confidence:** high.

---

## Test 3 — `test_agent_router_route_contract_unchanged` (dashboard/backend/tests/test_router_move.py:152-153)

**Extra actual route:** `('PATCH', '/v1/agents/{agent_id}', 'update_agent')`

**Root cause:** commit `30b1b73` ("Agent Edit UI", Jul 9 01:32 -0400, author JinBoatus1) added `PATCH /{agent_id}` → `update_agent` to `dashboard/backend/api/routers/agents.py` (edit agent name/description, per the commit message: "Persist pipeline config in localStorage and sync name/description via new PATCH /api/v1/agents/{id}"). This is a genuine, described feature. The commit touched only `agents.py`, `repository.py`, `service.py`, and frontend files — it never touched `dashboard/backend/tests/test_router_move.py` (`EXPECTED_AGENT_ROUTES`), which hasn't been edited since `d62f976` (Jul 4), 5 days before the feature landed. Confirmed via `git log -S"def update_agent" -- dashboard/backend/api/routers/agents.py` → only `30b1b73`.

**Verdict:** (a) intentional route addition (explicitly described in the commit message as a new endpoint), contract fixture not updated.

**Recommended fix:** add `("PATCH", "/v1/agents/{agent_id}", "update_agent")` to `EXPECTED_AGENT_ROUTES` in `test_router_move.py`.

**Confidence:** high.

---

## Overall verdict

**Intentional-drift, not a regression**, for all 3 failures. Two feature commits (`a7c392d` trade-log endpoint, `30b1b73` agent edit/PATCH endpoint) each added a real, working route but skipped updating the corresponding frozen route-contract fixture(s); a third commit (`d5307c5`, chart-data) updated one of two sibling fixtures in the same file but not the other. No route was removed, renamed, or behaviorally changed — recommend updating the 3 fixture sets (5 total line additions across `test_app_composition.py` ×2 sets, `test_router_move.py` ×1 set) to match the live route surface, with no changes to router source.
