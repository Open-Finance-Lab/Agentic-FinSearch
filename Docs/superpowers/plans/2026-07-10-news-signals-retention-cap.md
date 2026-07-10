# News-signals Artifact Retention Cap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cap `signals_dir` to the N most recent `signals-*.json` artifacts (default N=14), pruning older ones after each successful sweep so droplet storage can't grow unbounded.

**Architecture:** Add a `SIGNALS_KEEP_N` config knob (validated ≥1 at load), a best-effort `prune_artifacts(cfg)` helper that keeps the newest N artifacts by `(mtime, name)`, a single call to it at the end of `run_sweep`, and a small hardening of `run_canary` so a file pruned mid-glob can't raise `FileNotFoundError`. State (`signals_state.json`) is deliberately left untouched.

**Tech Stack:** Python 3 stdlib only (the Heartbeat CI is dependency-free), `unittest` + `unittest.mock`.

**Spec:** `Docs/superpowers/specs/2026-07-10-news-signals-retention-cap-design.md`

## Global Constraints

- **Single-file deploy contract:** `Heartbeat/news_signals.py` must not import `news_heartbeat`; keep all logic self-contained (stdlib only).
- **Dependency-free tests:** no `jsonschema`/third-party imports in `Heartbeat/tests/`; use `unittest` + `unittest.mock` like the existing suite.
- **Write-order contract (spec §6.2):** artifact is written before its state entry; pruning must not reorder or interfere with this — it runs only after the write loop completes.
- **Config-error exit code:** invalid config → `sys.exit(2)` (matches the `HEARTBEAT_WINDOW_HOURS` guard and the README exit-code table).
- **Default retention:** `SIGNALS_KEEP_N` default is **14**, baked into `load_config`.
- **Run tests with:** `cd Heartbeat && python -m pytest tests/test_news_signals.py -v` (or `python -m unittest`). All commands below assume CWD = `Heartbeat/`.

---

### Task 1: Config — `SIGNALS_KEEP_N` (default 14) with a fail-closed guard

**Files:**
- Modify: `Heartbeat/news_signals.py` — `load_config()` (currently lines 107–139)
- Modify: `Heartbeat/.env.heartbeat.example` (after line 35, `# SIGNALS_MAX_FILE_MB=10`)
- Test: `Heartbeat/tests/test_news_signals.py` — class `TestFoundation`, after `test_load_config_defaults_and_fallbacks`

**Interfaces:**
- Produces: `cfg["keep_n"]` — an `int ≥ 1` read from `SIGNALS_KEEP_N` (default 14). `make_cfg()` in the tests already calls `load_config()`, so every `cfg` fixture gains `keep_n` automatically.

- [ ] **Step 1: Write the failing tests**

Add to `TestFoundation` in `tests/test_news_signals.py`, right after `test_load_config_defaults_and_fallbacks`:

```python
    def test_load_config_defaults_keep_n_to_14(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            cfg = ns.load_config()
        self.assertEqual(cfg["keep_n"], 14)

    def test_load_config_honors_keep_n_override(self):
        with unittest.mock.patch.dict(os.environ, {"SIGNALS_KEEP_N": "30"}, clear=True):
            cfg = ns.load_config()
        self.assertEqual(cfg["keep_n"], 30)

    def test_load_config_rejects_non_positive_keep_n(self):
        # A keep_n of 0 or negative would delete every artifact on the next
        # sweep — fail closed at config load rather than wipe the directory.
        for bad in ("0", "-5"):
            with unittest.mock.patch.dict(os.environ, {"SIGNALS_KEEP_N": bad}, clear=True):
                with self.assertRaises(SystemExit) as ctx:
                    ns.load_config()
            self.assertEqual(ctx.exception.code, 2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_news_signals.py::TestFoundation -k keep_n -v`
Expected: FAIL — `test_load_config_defaults_keep_n_to_14` and `_honors_keep_n_override` raise `KeyError: 'keep_n'`; `_rejects_non_positive_keep_n` fails because no `SystemExit` is raised.

- [ ] **Step 3: Implement the config knob and guard**

In `load_config()`, immediately **after** the existing `HEARTBEAT_WINDOW_HOURS` guard block (the `if window_hours < WINDOW_HOURS_MIN:` … `sys.exit(2)`), add:

```python
    keep_n = int(os.environ.get("SIGNALS_KEEP_N", "14"))
    if keep_n < 1:
        # a non-positive cap would prune every artifact on the next sweep;
        # fail closed (exit 2 = config error, README exit-code table)
        log(f"ERROR SIGNALS_KEEP_N must be >= 1, got {keep_n}")
        sys.exit(2)
```

Then add this entry to the returned config dict (place it next to `"max_file_mb": ...`):

```python
        "keep_n": keep_n,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_news_signals.py::TestFoundation -k keep_n -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Document the knob in the env example**

In `Heartbeat/.env.heartbeat.example`, after line 35 (`# SIGNALS_MAX_FILE_MB=10`), add:

```bash
# Rolling retention cap: keep only the N most recent signals-*.json artifacts;
# older ones are pruned after each successful sweep. Bounds signals/ growth.
# SIGNALS_KEEP_N=14
```

- [ ] **Step 6: Run the full suite (no regressions) and commit**

Run: `python -m pytest tests/test_news_signals.py -q`
Expected: PASS (existing tests + 3 new).

```bash
git add Heartbeat/news_signals.py Heartbeat/.env.heartbeat.example Heartbeat/tests/test_news_signals.py
git commit -m "feat(signals): add SIGNALS_KEEP_N retention config (default 14, fail-closed)"
```

---

### Task 2: `prune_artifacts(cfg)` — keep newest N, best-effort delete

**Files:**
- Modify: `Heartbeat/news_signals.py` — add a new function **immediately before** `run_sweep` (currently line 571)
- Test: `Heartbeat/tests/test_news_signals.py` — new class `TestPrune` (place after `TestSweep`)

**Interfaces:**
- Consumes: `cfg["signals_dir"]` (a `Path`), `cfg["keep_n"]` (int ≥1) from Task 1.
- Produces: `prune_artifacts(cfg) -> None`. Deletes `signals-*.json` files beyond the newest `keep_n` (ordered by `(mtime, name)` descending). Never raises; a failed `unlink` logs `WARN` and is skipped. Only touches files matching `signals-*.json`.

- [ ] **Step 1: Write the failing tests**

Add a new class after `TestSweep` in `tests/test_news_signals.py`:

```python
class TestPrune(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.cfg = make_cfg(self.home)
        self.cfg["signals_dir"].mkdir(parents=True, exist_ok=True)

    def _make_artifact(self, name, mtime):
        p = self.cfg["signals_dir"] / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    def test_prune_keeps_newest_n_by_mtime(self):
        self.cfg["keep_n"] = 2
        base = 1_700_000_000
        for i in range(5):  # signals-0 oldest .. signals-4 newest
            self._make_artifact(f"signals-{i}.json", base + i)
        ns.prune_artifacts(self.cfg)
        remaining = sorted(p.name for p in self.cfg["signals_dir"].glob("signals-*.json"))
        self.assertEqual(remaining, ["signals-3.json", "signals-4.json"])

    def test_prune_is_noop_when_at_or_below_cap(self):
        self.cfg["keep_n"] = 5
        base = 1_700_000_000
        for i in range(3):
            self._make_artifact(f"signals-{i}.json", base + i)
        ns.prune_artifacts(self.cfg)
        self.assertEqual(len(list(self.cfg["signals_dir"].glob("signals-*.json"))), 3)

    def test_prune_only_touches_signal_artifacts(self):
        self.cfg["keep_n"] = 1
        base = 1_700_000_000
        self._make_artifact("signals-0.json", base)
        self._make_artifact("signals-1.json", base + 1)
        lock = self.cfg["signals_dir"] / ".lock"
        lock.write_text("", encoding="utf-8")
        other = self.cfg["signals_dir"] / "signals_state.json"  # not signals-*.json
        other.write_text("{}", encoding="utf-8")
        ns.prune_artifacts(self.cfg)
        self.assertTrue(lock.exists(), "the sweep .lock must never be pruned")
        self.assertTrue(other.exists(), "non-artifact files must never be pruned")
        self.assertTrue((self.cfg["signals_dir"] / "signals-1.json").exists())
        self.assertFalse((self.cfg["signals_dir"] / "signals-0.json").exists())

    def test_prune_survives_unlink_failure_without_raising(self):
        self.cfg["keep_n"] = 1
        base = 1_700_000_000
        self._make_artifact("signals-0.json", base)
        self._make_artifact("signals-1.json", base + 1)
        with unittest.mock.patch("pathlib.Path.unlink",
                                 side_effect=OSError("read-only fs")), \
             unittest.mock.patch.object(ns, "log") as fake_log:
            ns.prune_artifacts(self.cfg)  # must not raise
        self.assertTrue(any("WARN" in c.args[0] for c in fake_log.call_args_list))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_news_signals.py::TestPrune -v`
Expected: FAIL — `AttributeError: module 'news_signals' has no attribute 'prune_artifacts'`.

- [ ] **Step 3: Implement `prune_artifacts`**

Insert immediately before `def run_sweep(` in `news_signals.py`:

```python
def prune_artifacts(cfg):
    """Rolling retention cap (spec 2026-07-10): keep only the newest
    cfg["keep_n"] signals-*.json artifacts, unlink the rest. Ordered by
    (mtime, name) descending so "most recent" matches the canary's staleness
    notion, with name as a deterministic tiebreaker. Best-effort: a failed
    unlink logs a WARN and is skipped — the artifacts are already durably
    written, so cleanup failure must not fail the sweep. signals_state.json is
    left untouched by design (deleting a state entry would invite reprocessing).
    Runs under the sweep's flock, so no concurrent sweep races it."""
    artifacts = sorted(cfg["signals_dir"].glob("signals-*.json"),
                       key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for p in artifacts[cfg["keep_n"]:]:
        try:
            p.unlink()
            log(f"pruned old artifact {p.name}")
        except OSError as exc:
            log(f"WARN could not prune {p.name}: {exc}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_news_signals.py::TestPrune -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Heartbeat/news_signals.py Heartbeat/tests/test_news_signals.py
git commit -m "feat(signals): add prune_artifacts helper (keep newest N, best-effort)"
```

---

### Task 3: Wire pruning into `run_sweep` (state entries survive)

**Files:**
- Modify: `Heartbeat/news_signals.py` — `run_sweep()` (currently lines 571–597); add the prune call before the final `return 0`
- Test: `Heartbeat/tests/test_news_signals.py` — class `TestSweep`

**Interfaces:**
- Consumes: `prune_artifacts(cfg)` from Task 2.
- Produces: after a successful sweep, `signals_dir` holds ≤ `keep_n` artifacts; `signals_state.json` entries are unchanged.

- [ ] **Step 1: Write the failing test**

Add to `TestSweep` in `tests/test_news_signals.py`:

```python
    def test_sweep_prunes_to_keep_n_but_leaves_state(self):
        # Pre-seed keep_n old artifacts, then process one fresh digest: the
        # directory must settle at keep_n, and every state entry survives.
        self.cfg["keep_n"] = 2
        base = 1_700_000_000
        for i in range(2):
            p = self.cfg["signals_dir"] / f"signals-old-{i}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
            os.utime(p, (base + i, base + i))
        # seed a matching (stale) state entry that must NOT be pruned
        self.cfg["state_path"].write_text(
            json.dumps({"items-old.jsonl": {"processed_at": base, "status": "ok"}}),
            encoding="utf-8")
        write_items(self.cfg["digests"], [make_story()], name="items-2026-07-06.jsonl")
        self.assertEqual(ns.run_sweep(self.cfg, llm=OK_LLM), 0)
        arts = list(self.cfg["signals_dir"].glob("signals-*.json"))
        self.assertEqual(len(arts), 2, "sweep must hold the directory at keep_n")
        self.assertTrue((self.cfg["signals_dir"] / "signals-2026-07-06.json").exists(),
                        "the freshly written artifact must be one of the survivors")
        state = json.loads(self.cfg["state_path"].read_text())
        self.assertIn("items-old.jsonl", state, "state entries must survive pruning")
        self.assertIn("items-2026-07-06.jsonl", state)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_news_signals.py::TestSweep::test_sweep_prunes_to_keep_n_but_leaves_state -v`
Expected: FAIL — three artifacts remain (2 old + 1 fresh), so `len(arts) == 3 != 2`.

- [ ] **Step 3: Wire the prune call into `run_sweep`**

In `run_sweep()`, replace the final `return 0` (the one after the `for items_path in todo:` loop) with:

```python
    prune_artifacts(cfg)
    return 0
```

(The early `return 0` inside `if not todo:` stays as-is — an idle tick adds no artifacts, so it needs no prune.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_news_signals.py::TestSweep::test_sweep_prunes_to_keep_n_but_leaves_state -v`
Expected: PASS.

- [ ] **Step 5: Run the full TestSweep class (no regressions) and commit**

Run: `python -m pytest tests/test_news_signals.py::TestSweep -v`
Expected: PASS (existing sweep tests unaffected — they write ≤2 artifacts under the default `keep_n=14`, so pruning is a no-op for them).

```bash
git add Heartbeat/news_signals.py Heartbeat/tests/test_news_signals.py
git commit -m "feat(signals): prune artifacts at end of run_sweep, keep state intact"
```

---

### Task 4: Harden `run_canary` against a file pruned mid-glob

**Files:**
- Modify: `Heartbeat/news_signals.py` — `run_canary()` (currently lines 639–661; the `mtimes = [...]` comprehension at 643–644)
- Test: `Heartbeat/tests/test_news_signals.py` — class `TestCanary`

**Interfaces:**
- Produces: `run_canary` no longer raises `FileNotFoundError` when a globbed artifact disappears before its `stat()`; such a file is skipped.

- [ ] **Step 1: Write the failing test**

Add to `TestCanary` in `tests/test_news_signals.py`:

```python
    def test_canary_tolerates_artifact_pruned_mid_glob(self):
        # A concurrent sweep can unlink an artifact between the canary's glob
        # and its stat(). The canary must skip the vanished file, not crash.
        fresh = self.cfg["signals_dir"] / "signals-fresh.json"
        fresh.write_text("{}", encoding="utf-8")
        ghost = self.cfg["signals_dir"] / "signals-ghost.json"  # never created
        with unittest.mock.patch("pathlib.Path.glob", return_value=[ghost, fresh]):
            self.assertEqual(ns.run_canary(self.cfg), 0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_news_signals.py::TestCanary::test_canary_tolerates_artifact_pruned_mid_glob -v`
Expected: FAIL — `ghost.stat()` raises `FileNotFoundError`, propagating out of the list comprehension.

- [ ] **Step 3: Guard the per-file `stat()`**

In `run_canary()`, replace:

```python
    mtimes = [p.stat().st_mtime
              for p in cfg["signals_dir"].glob("signals-*.json")]
```

with:

```python
    mtimes = []
    for p in cfg["signals_dir"].glob("signals-*.json"):
        try:
            mtimes.append(p.stat().st_mtime)
        except FileNotFoundError:
            # pruned by a concurrent sweep between glob and stat — a race,
            # not staleness; skip it
            continue
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_news_signals.py::TestCanary::test_canary_tolerates_artifact_pruned_mid_glob -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest tests/test_news_signals.py -q`
Expected: PASS (all tests, old + new).

```bash
git add Heartbeat/news_signals.py Heartbeat/tests/test_news_signals.py
git commit -m "fix(signals): canary skips artifacts pruned mid-glob (no false CRIT)"
```

---

## Self-Review

**Spec coverage:**
- §1 config `SIGNALS_KEEP_N` default 14 + fail-closed guard → Task 1 ✓
- §2 `prune_artifacts`, `(mtime, name)` ordering, best-effort unlink → Task 2 ✓
- §3 integration into `run_sweep` end-of-loop, under flock → Task 3 ✓
- §4 state untouched → asserted in Task 2 (`test_prune_only_touches_signal_artifacts`) and Task 3 (`..._leaves_state`) ✓
- §5 canary hardening → Task 4 ✓
- §6 tests 1–6 → distributed across Tasks 1–4 ✓
- §7 rollout: `.env.heartbeat.example` documented → Task 1 Step 5 ✓; PR/deploy handled at branch-finish time

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `prune_artifacts(cfg)` defined in Task 2, consumed by name in Task 3; `cfg["keep_n"]` produced in Task 1, consumed in Tasks 2–3; all `signals-*.json` glob patterns and `cfg["signals_dir"]`/`cfg["state_path"]` keys match the existing module.

## Notes for the implementer

- CWD for all test commands is `Heartbeat/`. The suite is stdlib-only; `pytest` is optional — `python -m unittest tests.test_news_signals -v` works too.
- `make_cfg()` builds `cfg` via `load_config()`, so after Task 1 every fixture carries `keep_n=14`; individual prune/sweep tests lower it (`self.cfg["keep_n"] = 2`) to exercise pruning without creating 14+ files.
- Do not touch `signals_state.json` pruning — it is an explicit non-goal (see spec §4).
