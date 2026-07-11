# Deferred Items Execution Plan (§PR-A.1, §PR-A.2, §PR-C.2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the actionable deferred items from `Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md`: calendar-aware producer retention (§PR-A.1), the `asyncio.get_event_loop()` DeprecationWarning (§PR-A.2), and a root-cause investigation of the 3 pre-existing ATL route-contract test failures (§PR-C.2). §PR-C.1 is **out of scope**: it is gated on AgenticTrading#91 merging, which is still OPEN awaiting human review as of 2026-07-11.

**Architecture:** Tasks 1 and 2 are independent fixes in `fingpt_rcos`, each on its own branch → own PR (they hit different deploy paths: `Heartbeat/**` → heartbeat deploy; `Main/backend/**` → backend build+test+deploy). Task 3 is read-only diagnosis in the ATL repo (`/mnt/d/Github/agent-trading-lab`) — no code changes there.

**Tech Stack:** Python stdlib (`Heartbeat/` is dependency-free by CI design), Django + pytest via `uv` (`Main/backend`), pytest via `~/.venvs/atl` (ATL).

## Global Constraints

- Repo root: `/mnt/d/fingpt/Github/fingpt_rcos`. **Git quirk:** `git log main` fails (ambiguous with `Main/` dir on case-insensitive FS) — always use `refs/heads/main`.
- Heartbeat tests: `cd Heartbeat && python3 -m unittest discover -s tests -v` — system python3, **stdlib only, no new dependencies** (CI runs with zero setup).
- Backend tests: `cd Main/backend && uv sync --frozen && uv run pytest tests -q` (675 passed / 1 skipped baseline).
- Pristine test output: full suites must end with **zero warnings** in the summary.
- Selection-key semantics are frozen: `(stem_date, mtime, name)` exactly as in `Main/backend/api/signals_views.py:_load_artifact` (lines 88–89). Do not restructure that read path.
- `_list_signals_artifacts` has a second caller (the canary at `Heartbeat/news_signals.py:702`, mtimes only). **Do not change its `(mtime, name, path)` tuple shape.**
- Commit style: `fix(heartbeat): …`, `fix(api): …`, `docs(signals): …`.
- VERSION bump convention: behavior-changing `Heartbeat/news_signals.py` PRs bump `VERSION` (line 27) and regenerate the golden fixture via `cd Heartbeat && python3 tests/fixtures/make_signals_fixture.py` (the fixture pins `"generator": "news_signals.py/<VERSION>"`).

---

### Task 1: Calendar-aware producer retention (§PR-A.1)

**Branch:** `fix/prune-calendar-aware` off `refs/heads/main`.

**Files:**
- Modify: `Heartbeat/news_signals.py` (import at line 24, `VERSION` at line 27, new `_stem_date` helper before `prune_artifacts`, `prune_artifacts` at lines 611–626)
- Modify: `Heartbeat/tests/test_news_signals.py` (`TestPrune` class, after line 855)
- Regenerate: `Heartbeat/tests/fixtures/signals-fixture.json`
- Modify: `Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md` (§4.4, sentence at lines 171–172)
- Modify: `Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md` (§PR-A.1 status line)

**Interfaces:**
- Consumes: `_list_signals_artifacts(signals_dir) -> list[(mtime, name, path)]` (unchanged).
- Produces: `_stem_date(name: str) -> datetime.date | None` (module-level helper, name-string input — note the read-path twin takes a `Path`).

- [ ] **Step 1: Write the failing tests** — add to `TestPrune` in `Heartbeat/tests/test_news_signals.py` (unittest style, use the existing `self._make_artifact(name, mtime)` helper):

```python
    def test_prune_is_calendar_aware_backfill_cannot_evict_newer_day(self):
        # §PR-A.1 regression: an old day's artifact rewritten in place
        # (state-file surgery / crash-recovery reprocessing -> fresh mtime)
        # must not outrank calendar-newer artifacts at prune time; otherwise
        # ?as_of=<evicted day> 404s inside the nominal retention window.
        self.cfg["keep_n"] = 2
        base = 1_700_000_000
        self._make_artifact("signals-2026-07-08.json", base + 1)
        self._make_artifact("signals-2026-07-09.json", base + 2)
        # oldest calendar day, but backfilled -> newest mtime
        self._make_artifact("signals-2026-07-07.json", base + 99)
        ns.prune_artifacts(self.cfg)
        remaining = sorted(p.name for p in self.cfg["signals_dir"].glob("signals-*.json"))
        self.assertEqual(
            remaining, ["signals-2026-07-08.json", "signals-2026-07-09.json"])

    def test_prune_non_dated_stems_rank_oldest(self):
        # Unreachable from this producer (all stems are date-prefixed), but a
        # hand-placed non-dated stem must not crash the sort and must rank
        # oldest (pruned first) regardless of mtime — mirroring the read
        # path's "latest dated artifact" contract (spec §4.4).
        self.cfg["keep_n"] = 1
        base = 1_700_000_000
        self._make_artifact("signals-2026-07-09.json", base)
        self._make_artifact("signals-manual.json", base + 99)
        ns.prune_artifacts(self.cfg)
        remaining = [p.name for p in self.cfg["signals_dir"].glob("signals-*.json")]
        self.assertEqual(remaining, ["signals-2026-07-09.json"])

    def test_prune_same_day_supplementals_tiebreak_by_mtime(self):
        # Pin (passes before and after): same stem date falls back to mtime,
        # matching the read path's same-day-supplemental tiebreak.
        self.cfg["keep_n"] = 1
        base = 1_700_000_000
        self._make_artifact("signals-2026-07-09.json", base + 5)
        self._make_artifact("signals-2026-07-09-supplemental.json", base + 9)
        ns.prune_artifacts(self.cfg)
        remaining = [p.name for p in self.cfg["signals_dir"].glob("signals-*.json")]
        self.assertEqual(remaining, ["signals-2026-07-09-supplemental.json"])
```

- [ ] **Step 2: Run to verify RED**

Run: `cd Heartbeat && python3 -m unittest tests.test_news_signals.TestPrune -v`
Expected: `test_prune_is_calendar_aware_backfill_cannot_evict_newer_day` FAILS (current code keeps 07-07+07-09, evicts 07-08); `test_prune_non_dated_stems_rank_oldest` FAILS (current code keeps `signals-manual.json`); the tiebreak pin PASSES; all pre-existing `TestPrune` tests PASS.

- [ ] **Step 3: Implement** — in `Heartbeat/news_signals.py`:

Line 24: `from datetime import datetime, timezone` → `from datetime import date, datetime, timezone`
Line 27: `VERSION = "2026-07-10.1"` → `VERSION = "2026-07-11.1"`

Add above `prune_artifacts`:

```python
def _stem_date(name):
    """The leading YYYY-MM-DD of a signals-<...>.json filename, or None for a
    non-dated stem. Mirrors Main/backend/api/signals_views.py:_stem_date
    (which takes a Path) so retention ranks artifacts exactly the way the
    read path resolves ?as_of."""
    head = name[len("signals-"):len("signals-") + 10]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None
```

Replace `prune_artifacts` body and the ordering sentences of its docstring:

```python
def prune_artifacts(cfg):
    """Rolling retention cap (spec 2026-07-10): keep only the newest
    cfg["keep_n"] signals-*.json artifacts, unlink the rest. Ordered by
    (stem date, mtime, name) descending — calendar date first, mirroring the
    read path's ?as_of selection (signals_views._load_artifact) — so an older
    day rewritten in place with a fresh mtime (state-file surgery,
    crash-recovery reprocessing) can never evict a calendar-newer artifact
    from the retention window (deferred item §PR-A.1). (mtime, name) stays
    the same-day-supplemental tiebreak; non-dated stems (unreachable from
    this producer) rank oldest and are pruned first. The canary's staleness
    notion stays pure-mtime — staleness is about write recency, retention
    about calendar coverage. Best-effort: a failed unlink logs a WARN and is
    skipped — the artifacts are already durably written, so cleanup failure
    must not fail the sweep. signals_state.json is left untouched by design
    (deleting a state entry would invite reprocessing). Runs under the
    sweep's flock, so no concurrent sweep races it."""
    artifacts = sorted(
        _list_signals_artifacts(cfg["signals_dir"]),
        key=lambda t: (_stem_date(t[1]) or date.min, t[0], t[1]),
        reverse=True,
    )
    for _, _, p in artifacts[cfg["keep_n"]:]:
        try:
            p.unlink()
            log(f"pruned old artifact {p.name}")
        except OSError as exc:
            log(f"WARN could not prune {p.name}: {exc}")
```

- [ ] **Step 4: Regenerate the golden fixture** (VERSION is pinned inside it)

Run: `cd Heartbeat && python3 tests/fixtures/make_signals_fixture.py`
Expected: `tests/fixtures/signals-fixture.json` diff shows only the `generator` line changing to `news_signals.py/2026-07-11.1`.

- [ ] **Step 5: Run to verify GREEN**

Run: `cd Heartbeat && python3 -m unittest discover -s tests -v`
Expected: full heartbeat suite passes (was 147; now 150), zero warnings. Note `test_prune_keeps_newest_n_by_mtime` still passes: its stems are non-dated, so they tie on `date.min` and fall back to mtime.

- [ ] **Step 6: Update docs** — in `Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md` §4.4, after "History depth is bounded by retention (`SIGNALS_KEEP_N`, default 14 dated artifacts):" amend the sentence so the passage reads:

> History depth is bounded by retention (`SIGNALS_KEEP_N`, default 14 dated artifacts; since 2026-07-11 the producer prunes by the same `(stem date, mtime, name)` order as resolution, so a backfilled older day can never evict a calendar-newer artifact from the window): a date older than the oldest retained artifact 404s identically to "never produced" …

And in `Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md`, change the §PR-A.1 status line to:

> **Status: RESOLVED 2026-07-11 — prune sort aligned with `(stem_date, mtime, name)` (option a), branch `fix/prune-calendar-aware`.**

- [ ] **Step 7: Commit**

```bash
git add Heartbeat/news_signals.py Heartbeat/tests/test_news_signals.py Heartbeat/tests/fixtures/signals-fixture.json Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md
git commit -m "fix(heartbeat): calendar-aware artifact retention (stem date before mtime)"
```

---

### Task 2: Quiet, non-deprecated asyncio task count (§PR-A.2)

**Branch:** `fix/resource-monitor-event-loop` off `refs/heads/main` (NOT stacked on Task 1).

**Files:**
- Modify: `Main/backend/api/utils/resource_monitor.py:60-67`
- Test: `Main/backend/tests/test_resource_snapshot_enhanced.py` (existing file — match its conventions)

**Interfaces:**
- Consumes/Produces: `ResourceSnapshot._get_asyncio_task_count() -> int` — signature unchanged; sync-context result stays `0`, now warning-free.

- [ ] **Step 1: Write the tests** — in `Main/backend/tests/test_resource_snapshot_enhanced.py` (pytest, `asyncio_mode = "auto"`; `ResourceSnapshot.__new__` + `pid` keeps the probe surgical — the full constructor stats memory/fds/processes):

```python
def test_asyncio_task_count_sync_context_is_quiet_zero(recwarn):
    """§PR-A.2 regression: asyncio.get_event_loop() in a thread with no set
    loop emitted 'DeprecationWarning: There is no current event loop'
    (nondeterministically — it depends on whether an earlier test left a
    loop set). get_running_loop() never warns: no running loop -> 0."""
    snap = ResourceSnapshot.__new__(ResourceSnapshot)
    snap.pid = os.getpid()
    assert snap._get_asyncio_task_count() == 0
    assert [w for w in recwarn.list
            if issubclass(w.category, DeprecationWarning)] == []


async def test_asyncio_task_count_inside_running_loop_counts_current_task():
    """The happy path still counts tasks on the running loop (at least the
    task executing this test)."""
    snap = ResourceSnapshot.__new__(ResourceSnapshot)
    snap.pid = os.getpid()
    assert snap._get_asyncio_task_count() >= 1
```

- [ ] **Step 2: Run to verify RED (best-effort)**

Run: `cd Main/backend && uv run pytest tests/test_resource_snapshot_enhanced.py -v -W error::DeprecationWarning`
Expected: the sync-context test FAILS if the environment reproduces the no-current-loop condition. **The RED here is environment-dependent (that is the flakiness being fixed)** — if both new tests pass pre-fix, record that in the report and proceed; the enforced deliverables are (a) the deprecated call is gone, (b) the full suite ends warning-free.

- [ ] **Step 3: Implement** — replace `_get_asyncio_task_count` in `Main/backend/api/utils/resource_monitor.py`:

```python
    def _get_asyncio_task_count(self) -> int:
        """Get count of running asyncio tasks."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread (sync context) -> no tasks.
            # asyncio.get_event_loop() here would emit DeprecationWarning
            # ("There is no current event loop") and create a stray loop.
            return 0
        try:
            return len(asyncio.all_tasks(loop))
        except Exception:
            return 0
```

- [ ] **Step 4: Run to verify GREEN**

Run: `cd Main/backend && uv run pytest tests/test_resource_snapshot_enhanced.py -v -W error::DeprecationWarning`
Expected: PASS. Then the full suite:
Run: `uv run pytest tests -q`
Expected: 677+ passed / 1 skipped, **no warnings summary block** (the flaky `DeprecationWarning` from `test_artifact_loaded_from_disk_once_per_request` runs can no longer occur — `resource_monitor.py` no longer contains `get_event_loop`).

- [ ] **Step 5: Update the deferred log** — in `Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md`, change the §PR-A.2 status line to:

> **Status: RESOLVED 2026-07-11 — `get_running_loop()` with quiet-zero fallback, branch `fix/resource-monitor-event-loop`.**

- [ ] **Step 6: Commit**

```bash
git add Main/backend/api/utils/resource_monitor.py Main/backend/tests/test_resource_snapshot_enhanced.py Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md
git commit -m "fix(api): drop deprecated asyncio.get_event_loop() from resource monitor"
```

---

### Task 3: §PR-C.2 investigation — 3 pre-existing ATL route-contract failures (READ-ONLY)

**Repo:** `/mnt/d/Github/agent-trading-lab` (currently on branch `fix/djia30-current-index`, clean — the failures are identical on `origin/main` and this branch per the deferred log; **do not switch branches, do not commit, do not modify any file in this repo**).

**Files:**
- Create (report only): `/tmp/claude-1000/-mnt-d-fingpt/a178ce60-dbd1-484b-a535-714084f5b2cc/scratchpad/atl-route-contract-investigation.md`

**Interfaces:**
- Produces: a diagnosis report (root cause per failing test + recommended fix + who should fix it) folded into the controller's final summary and the deferred-log §PR-C.2 entry.

- [ ] **Step 1: Reproduce** — run exactly the three tests:

```bash
cd /mnt/d/Github/agent-trading-lab
~/.venvs/atl/bin/python -m pytest -k "test_backtests_router_contract or test_full_route_contract_unchanged or test_agent_router_route_contract_unchanged" -v 2>&1 | tail -40
```

Expected: 3 failures (baseline per deferred log: same 3 fail on untouched origin/main).

- [ ] **Step 2: Diagnose** — for each failure: read the assertion diff, locate the contract fixture/constant it compares against, and `git log -n 5 --oneline -- <router file>` to identify the upstream commit that drifted the routes. Answer: (a) is the drift an intentional upstream route change where the contract test was simply not updated, or (b) a real contract regression?

- [ ] **Step 3: Write the report** to the scratchpad path above: per-test root cause, the drifting commit(s), the one-line recommended fix (update contract fixture vs. revert route), and confidence. No code changes.

---

### Post-plan (controller, not subagent tasks)

- Per branch: final review (ship gate), push, open PR, wait CI green, squash-merge, watch the triggered deploy to completion (Task 1 → heartbeat deploy; Task 2 → backend deploy run — build → test → deploy gates).
- Merge order: Task 2's PR first or second — no interaction; sequential merges so each deploy is watched individually.
- Update `.superpowers/sdd/progress.md` ledger per task.
- Final summary to user: PRs, deploy runs, C.2 findings, C.1 still blocked on #91.

---

### Task 4: Remove deprecated get_event_loop from views.py streaming save/restore (§PR-A.2 extension)

**Branch:** continue on `fix/resource-monitor-event-loop` (second commit, same PR — same bug class, same warning-free-suite deliverable).

**Context:** Task 2 revealed the full backend suite still is not warning-free: `Main/backend/api/views.py:565` (`asyncio.get_event_loop()`) warns deterministically via `test_agent_budget_enforce.py::TestStreamSlotRelease::test_release_on_midstream_raise`; `views.py:748` is an identical twin block. Both are save/restore-politeness captures around a private streaming loop (`new_event_loop` + `set_event_loop`, restored in the `finally` at lines 598/788). The old code's implicit loop-creation (the warning source) was pointless — it created a throwaway loop merely to save and restore it. Two OTHER call sites (`datascraper/openai_search.py:794`, `mcp_client/mcp_manager.py:73`) have get-or-create/store semantics, do NOT warn in the suite, and are explicitly OUT of this task's scope (deferred as §PR-A.4).

**Files:**
- Modify: `Main/backend/api/views.py:563-567` and `:746-750` (both twin blocks, identically)
- Modify: `Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md` (append §PR-A.4 entry, exact text below)

**Interfaces:** none — `previous_loop` stays a local restored by the existing `finally: asyncio.set_event_loop(previous_loop)`.

- [ ] **Step 1: Verify RED**

Run: `cd Main/backend && uv run pytest tests/test_agent_budget_enforce.py::TestStreamSlotRelease::test_release_on_midstream_raise -q -W error::DeprecationWarning`
Expected: FAILS/ERRORS on the `get_event_loop` DeprecationWarning raised as an error (it is not a RuntimeError, so the `except RuntimeError` does not swallow it). If this command does NOT go red, record the exact output in your report and proceed — the enforced deliverable is the warning-free full suite.

- [ ] **Step 2: Implement** — replace BOTH blocks (lines 563–567 and 746–750), which currently read:

```python
                previous_loop = None
                try:
                    previous_loop = asyncio.get_event_loop()
                except RuntimeError:
                    previous_loop = None
```

with:

```python
                # Save the thread's loop binding to restore in the finally.
                # get_running_loop(), not deprecated get_event_loop(): no
                # loop is ever *running* in this sync streaming path, and
                # get_event_loop()'s only extra behavior was auto-creating a
                # throwaway loop (with a DeprecationWarning) merely to be
                # saved and restored. Nothing in this backend sets a
                # not-running loop on worker threads, so restoring None
                # (= unset) is the correct, Python-3.14-forward binding.
                try:
                    previous_loop = asyncio.get_running_loop()
                except RuntimeError:
                    previous_loop = None
```

- [ ] **Step 3: Verify GREEN**

Run: `uv run pytest tests/test_agent_budget_enforce.py -q -W error::DeprecationWarning` → all pass.
Run: `uv run pytest tests -q` → full suite passes (678/1 skipped) with **no warnings summary block**; paste the untrimmed tail in your report.

- [ ] **Step 4: Append §PR-A.4 to the deferred log** — add after the §PR-A.2 section:

```markdown
### §PR-A.4 — Two remaining get_event_loop call sites with get-or-create semantics

**Status: deferred 2026-07-11 (found during §PR-A.2 execution; neither warns in the current suite)**

**What:** `Main/backend/datascraper/openai_search.py:794` and `Main/backend/mcp_client/mcp_manager.py:73` still call `asyncio.get_event_loop()`. Unlike the fixed sites, these *rely* on get-or-create semantics (`mcp_manager` stores `self._loop` for later use), so a mechanical `get_running_loop()` swap could change behavior; each needs its own intent analysis. Python 3.14 makes the implicit creation an error, so these must be revisited before any 3.14 upgrade.

**Next-session entry point:** trace how `openai_search.py:794`'s `loop` and `mcp_manager.py:73`'s `self._loop` are consumed; replace with explicit `new_event_loop()`/`asyncio.run()` ownership or a running-loop requirement per intent. Effort: ~1h.
```

Also extend the §PR-A.2 **Status** line by appending: ` Extension: twin `views.py:565/748` save/restore captures fixed in the same PR; remaining call sites logged as §PR-A.4.`

- [ ] **Step 5: Commit**

```bash
git add Main/backend/api/views.py Docs/superpowers/plans/2026-07-10-signals-asof-dow30-deferred.md
git commit -m "fix(api): drop deprecated asyncio.get_event_loop() from streaming loop save/restore"
```
