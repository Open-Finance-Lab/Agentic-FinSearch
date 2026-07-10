# News-signals artifact retention cap — design

**Date:** 2026-07-10
**Component:** `Heartbeat/news_signals.py`
**Task:** `finsearch-nts-signals-retention-cap-01` (P2)
**Related:** `Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md` (the pipeline this caps)

## Problem

The news→signals sweep (`run_sweep`, fired by a 20-min systemd timer) writes one
`signals-<stem>.json` artifact per unprocessed digest into `signals_dir`, then
records the digest in `signals_state.json`. Nothing ever deletes artifacts, so
the directory grows ~1 file/day without bound. The only existing size control is
`SIGNALS_MAX_FILE_MB`, which caps a single *input* digest — it does nothing about
*accumulation*. On the droplet this is a live storage risk; it is currently held
in check by a manual hold at 14 artifacts.

## Goal

Bound `signals_dir` to the **N most recent** `signals-*.json` artifacts
(default **N = 14**), pruning older artifacts after each successful sweep, with
no change to pipeline correctness, the write-order contract, or the canary's
staleness semantics.

Non-goals: pruning digests (`items-*.jsonl`, owned by `news_heartbeat.py`);
time-based / TTL retention; compaction of `signals_state.json`.

## Design

### 1. Config — `load_config()`

Add one entry, read from the environment with a baked-in default of 14:

```python
"keep_n": int(os.environ.get("SIGNALS_KEEP_N", "14")),
```

Validate at load time, mirroring the existing `window_hours` floor guard: if
`keep_n < 1`, log an error and `sys.exit(2)` (config error, per the README
exit-code table). **Fail closed, don't clamp:** a `keep_n` of 0 or negative
would delete *every* artifact on the next sweep, so a misconfigured cap must
refuse to start rather than silently wipe the directory.

### 2. `prune_artifacts(cfg)` — new helper

- Glob `signals-*.json` in `cfg["signals_dir"]`.
- Sort by `(mtime, name)` **descending**; keep the newest `cfg["keep_n"]`,
  unlink the remainder.
- **Ordering:** mtime is the primary key — "most recent" matches both the user's
  wording and the canary's existing recency notion (`run_canary` keys staleness
  off `max(mtime)`). `name` is a deterministic tiebreaker; ISO-date stems already
  sort chronologically, so ties resolve stably.
- **Best-effort:** wrap each `unlink` in `try/except OSError`; a failed delete
  (permission, race) logs a `WARN` and is skipped. Pruning never raises, never
  changes the sweep's exit code — the artifacts are already durably written, so
  a cleanup failure is not a pipeline failure.

### 3. Integration — `run_sweep(cfg, ...)`

Call `prune_artifacts(cfg)` **once**, at the end of `run_sweep`, after the write
loop completes. It runs inside the existing exclusive `flock` (`signals_dir/.lock`),
so no concurrent sweep can race it. It runs only when there was work — the early
`if not todo: return 0` short-circuits an idle tick — which is exactly when new
artifacts were added, so end-of-sweep pruning is sufficient to hold the cap.

### 4. `signals_state.json` — left untouched (deliberate)

The task offered optional pruning of state entries; this design **does not** do
it. State is keyed by `items-*.jsonl` digest name and exists only to prevent
reprocessing. Deleting an artifact while keeping its state entry is safe.
Deleting the *state* entry while its digest still exists would make the next
sweep **reprocess** that digest → regenerate the old artifact with a fresh mtime
→ churn (and pollute the "most recent" set). State entries are tiny
(`{processed_at, status}`, ~1/day) and are not the storage concern; the multi-MB
artifacts are. So: **prune artifacts only.**

### 5. Canary hardening — `run_canary(cfg, ...)`

`run_canary` currently computes `[p.stat().st_mtime for p in glob("signals-*.json")]`
lock-free (by design — it must not block on an in-flight sweep's LLM call). Once a
sweep can *delete* artifacts, a file can vanish between the glob and its `stat()`,
raising `FileNotFoundError` → a false CRIT staleness alert. Fix: guard the per-file
`stat()` and skip a file that disappears mid-iteration. Small, targeted hardening
that is a direct consequence of introducing deletion; it does not otherwise change
canary behavior.

## Error handling summary

| Condition | Behavior |
|---|---|
| `SIGNALS_KEEP_N < 1` | `sys.exit(2)` at config load (fail closed) |
| `unlink` fails during prune | `WARN` logged, file skipped, sweep continues, exit code unchanged |
| Artifact vanishes during canary glob | Skipped, canary uses remaining files |
| ≤ N artifacts present | Prune is a no-op |

## Tests (TDD, `tests/test_news_signals.py`, unittest)

1. Prune keeps exactly N, deletes the oldest beyond N (ordered by mtime).
2. Prune with ≤ N artifacts deletes nothing.
3. `SIGNALS_KEEP_N < 1` → `SystemExit(2)` at config load.
4. A sweep that writes new artifacts leaves the directory at ≤ N.
5. State entries survive pruning (artifact deleted, `signals_state.json` key kept).
6. Canary tolerates a file vanishing during its glob (no crash, correct newest).

## Rollout

- Document `SIGNALS_KEEP_N` (default 14) in `Heartbeat/.env.heartbeat.example`.
- TDD implementation → PR to `Agentic-FinSearch` `main`.
- Droplet picks it up via the Heartbeat deploy job; the manual 14-artifact hold
  can then be released (code default already matches).
