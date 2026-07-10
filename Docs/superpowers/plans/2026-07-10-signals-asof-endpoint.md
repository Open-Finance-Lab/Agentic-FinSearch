# Signals `?as_of=YYYY-MM-DD` Endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `?as_of=YYYY-MM-DD` query parameter to `GET /api/signals/news/` that serves the newest signals artifact dated on or before that day (point-in-time, no lookahead), so ATL's backtester can fetch signals keyed to a historical date instead of always the latest.

**Architecture:** The endpoint already funnels every disk read through one memoized loader (`_get_artifact(request)`) that `@condition`'s `_etag`/`_last_modified` and the view body all share. We rename that loader to `_load_artifact(as_of=None)` and add an `as_of`-aware candidate filter with stem-date-first selection (inside the window, calendar order outranks mtime — a backfilled older day can never shadow a newer day). Because the ETag is keyed on the resolved artifact's `generated_at`/`source_items`, conditional-GET caching becomes as-of-correct with **no ETag change**: different resolved days → different ETag; same resolved artifact → shared cache entry. Absent `as_of` is byte-for-byte the current behavior.

**Tech Stack:** Django (plain function view, not DRF); stdlib `json`/`re`/`pathlib`/`datetime`; tests are `django.test.SimpleTestCase` with `override_settings`.

## Global Constraints

- **Fail-closed:** every failure path returns a 4xx JSON body, never a 500 that leaks pipeline state. Existing shape: `404 {"error": "no_signals"}`. New: `400 {"error": "bad_as_of"}` for a malformed `as_of`.
- **No new response keys, no schema change.** `staleness_hours` keeps its current meaning (relative to `now`); gap detection is the client's job via the returned `generated_at`.
- **Resolution is by filename stem date**, not the JSON `generated_at` — the filename is the authoritative "which day this batch is for." Under `as_of`, candidates order by `(stem_date, mtime, name)`: calendar date first (a backfilled/reprocessed older day never outranks a newer day — reprocessing rewrites `signals-<old-stem>.json` in place with a fresh mtime), with `(mtime, name)` kept as the same-day-supplemental tiebreak — all without opening every file. The no-param path keeps today's pure `(mtime, name)` order.
- **Django only** (`from api import signals_views`); no DRF, no new dependencies.
- **Test command (run from `Main/backend/`):** `uv run pytest tests -q`. Single test: `uv run pytest tests/test_signals_endpoint.py::SignalsEndpointTests::<name> -q`.
- **Semantics:** `as_of=D` → newest artifact with stem-date ≤ D. Absent → newest overall (unchanged). Future/too-recent `D` → latest. `D` before all retained artifacts → 404. Malformed `D` → 400.

---

### Task 1: Rename `_load_latest` → `_load_artifact` (pure refactor, prepares for the `as_of` param)

**Files:**
- Modify: `Main/backend/api/signals_views.py:30` (def), `:69` (call site in `_get_artifact`)
- Modify: `Main/backend/tests/test_signals_endpoint.py:212-213` (the `mock.patch.object(..., "_load_latest", ...)` target)

**Interfaces:**
- Produces: `_load_artifact()` — same zero-arg behavior as today's `_load_latest()` (newest artifact or `None`). Task 2 adds the `as_of` parameter.

- [ ] **Step 1: Rename the function and its one internal call site**

In `signals_views.py`, change the def on line 30:
```python
def _load_artifact():
```
and the call inside `_get_artifact` (line 69):
```python
        request._signals_artifact = _load_artifact()
```
Leave the function body unchanged.

- [ ] **Step 2: Retarget the memoization test's mock**

In `test_signals_endpoint.py`, in `test_artifact_loaded_from_disk_once_per_request` (lines ~211-213), change both references:
```python
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC), \
             mock.patch.object(signals_views, "_load_artifact",
                               wraps=signals_views._load_artifact) as loader:
```

- [ ] **Step 3: Confirm no other references remain**

Run: `grep -rn "_load_latest" Main/`
Expected: no output (all references renamed).

- [ ] **Step 4: Run the full endpoint suite — must stay green (no behavior change)**

Run (from `Main/backend/`): `uv run pytest tests/test_signals_endpoint.py -q`
Expected: all existing tests PASS (same count as before: 17 passed).

- [ ] **Step 5: Commit**

```bash
git add Main/backend/api/signals_views.py Main/backend/tests/test_signals_endpoint.py
git commit -m "refactor(signals): rename _load_latest -> _load_artifact (prep for as_of)"
```

---

### Task 2: Add `?as_of` point-in-time lookup + malformed-input 400

**Files:**
- Modify: `Main/backend/api/signals_views.py` (imports, module docstring, add `_as_of` + `_stem_date`, extend `_load_artifact`, extend `_get_artifact`, extend view)
- Modify: `Main/backend/tests/test_signals_endpoint.py` (add tests)

**Interfaces:**
- Consumes: `_load_artifact()` from Task 1.
- Produces:
  - `_as_of(request) -> datetime.date | None` — parses `?as_of`; raises `ValueError` on malformed input.
  - `_stem_date(path: Path) -> datetime.date | None` — leading `YYYY-MM-DD` of a `signals-*.json` name, else `None`.
  - `_load_artifact(as_of=None)` — when `as_of` set, newest artifact with stem-date ≤ `as_of`, else `None`.

- [ ] **Step 1: Write the failing tests**

Add these methods to `class SignalsEndpointTests` in `test_signals_endpoint.py` (they reuse the file's existing `make_artifact`, `self._write`, `self._recent_iso`, `DEFAULT_SIGNAL`, `URL`, `_HERMETIC`):

```python
    def test_as_of_serves_that_days_artifact(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d05")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d06")}))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d05")

    def test_as_of_falls_back_to_nearest_earlier_on_gap(self):
        # No 07-05 artifact; point-in-time on-or-before resolves to 07-03.
        self._write("2026-07-03", make_artifact(self._recent_iso(80.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d03")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d06")}))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d03")

    def test_as_of_before_all_history_404s(self):
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-01"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_signals"})

    def test_malformed_as_of_400s(self):
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            for bad in ("2026-7-5", "07-05-2026", "yesterday",
                        "2026-13-40", "2026-07-06T00:00:00", "2026/07/06"):
                resp = self.client.get(URL, {"as_of": bad})
                self.assertEqual(resp.status_code, 400, bad)
                self.assertEqual(resp.json(), {"error": "bad_as_of"}, bad)

    def test_as_of_in_future_returns_latest(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d05")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d06")}))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2027-01-01"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d06")

    def test_as_of_picks_newest_same_day_supplemental_by_mtime(self):
        morning = make_artifact(self._recent_iso(8.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="morning")})
        supplemental = make_artifact(self._recent_iso(0.1), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="supplemental")})
        self._write("2026-07-06", morning)
        supp = self.dir / "signals-2026-07-06-153042.json"
        supp.write_text(json.dumps(supplemental), encoding="utf-8")
        now = time.time()
        os.utime(self.dir / "signals-2026-07-06.json", (now - 100, now - 100))
        os.utime(supp, (now, now))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-06"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "supplemental")

    def test_as_of_prefers_newer_stem_date_over_newer_mtime(self):
        # Backfill/reprocess skew: an older-day artifact rewritten in place
        # gets a fresh mtime; the correctly-dated 07-05 artifact must still
        # win under as_of=2026-07-05 (calendar order beats mtime).
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d05")}))
        self._write("2026-07-03", make_artifact(self._recent_iso(80.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d03-backfilled")}))
        now = time.time()
        os.utime(self.dir / "signals-2026-07-05.json", (now - 100, now - 100))
        os.utime(self.dir / "signals-2026-07-03.json", (now, now))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d05")

    def test_as_of_etag_tracks_resolved_artifact(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0)))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            e05 = self.client.get(URL, {"as_of": "2026-07-05"})["ETag"]
            e06 = self.client.get(URL, {"as_of": "2026-07-06"})["ETag"]
            # A future as_of resolves to the same artifact as 07-06 -> its ETag
            # revalidates with a 304.
            revalidated = self.client.get(URL, {"as_of": "2027-01-01"},
                                          HTTP_IF_NONE_MATCH=e06)
        self.assertNotEqual(e05, e06)
        self.assertEqual(revalidated.status_code, 304)

    def test_as_of_composes_with_tickers_filter(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL),
            "AAPL": dict(DEFAULT_SIGNAL, guid="g2")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05", "tickers": "msft"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.json()["signals"]), ["MSFT"])
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_signals_endpoint.py -q -k as_of`
Expected: FAIL — e.g. `test_as_of_serves_that_days_artifact` returns the 07-06 artifact (as_of ignored), `test_malformed_as_of_400s` gets 200/404 instead of 400, `test_as_of_prefers_newer_stem_date_over_newer_mtime` serves the backfilled 07-03 artifact.

- [ ] **Step 3: Implement — imports + parser + stem helper**

In `signals_views.py`, change the imports at the top:
```python
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
```

Extend the module docstring's first paragraph (after the existing "Serves the newest signals-*.json ..." sentence) with:
```
An optional ?as_of=YYYY-MM-DD selects the newest artifact dated on or before
that day (point-in-time — no lookahead, robust to gaps); a malformed value is
a 400 {"error": "bad_as_of"}.
```

Add, just below `_PUBLIC_STRIP = ("generator", "model", "prompt_version")`:
```python
_AS_OF_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


def _as_of(request: HttpRequest):
    """Parse ?as_of=YYYY-MM-DD. Returns None (param absent) or a datetime.date.
    Raises ValueError on any malformed value — the view maps that to a 400."""
    raw = request.GET.get("as_of")
    if not raw:
        return None
    if not _AS_OF_RE.match(raw):
        raise ValueError(f"as_of must be YYYY-MM-DD, got {raw!r}")
    return date.fromisoformat(raw)  # also rejects impossible dates (e.g. 2026-13-40)


def _stem_date(path: Path):
    """The leading YYYY-MM-DD of a signals-<...>.json filename, or None for a
    non-dated stem (e.g. signals-a.json). ?as_of resolves by the date a batch
    is *for* (its filename stem), reusing the (mtime, name) tiebreak below for
    same-day supplemental reruns."""
    head = path.name[len("signals-"):len("signals-") + 10]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None
```

- [ ] **Step 4: Implement — `as_of` filter in `_load_artifact`, wiring in `_get_artifact`, 400 in the view**

Change `_load_artifact`'s signature, add the filter, and make the selection key `as_of`-aware (the validation part of the body stays exactly as-is):
```python
def _load_artifact(as_of=None):
    configured = getattr(settings, "SIGNALS_DIR", "")
    if not configured:
        return None
    directory = Path(configured)
    if not directory.is_dir():
        return None
    candidates = list(directory.glob("signals-*.json"))
    if as_of is not None:
        # Point-in-time: keep only artifacts whose batch date is on or before
        # as_of. Non-dated stems (_stem_date is None) are skipped.
        candidates = [p for p in candidates
                      if (d := _stem_date(p)) is not None and d <= as_of]
    if not candidates:
        return None
    newest = None
    try:
        if as_of is None:
            newest = max(candidates, key=lambda p: (p.stat().st_mtime, p.name))
        else:
            # Calendar order first: a backfilled/reprocessed older-day
            # artifact (rewritten in place -> fresh mtime) must never outrank
            # a newer-dated artifact inside the as_of window; (mtime, name)
            # stays the same-day-supplemental tiebreak. Every candidate here
            # has a non-None stem date (filtered above).
            newest = max(candidates, key=lambda p: (
                _stem_date(p), p.stat().st_mtime, p.name))
        artifact = json.loads(newest.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(artifact["generated_at"])
        if generated.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        if not isinstance(artifact["signals"], dict):
            raise ValueError("signals must be a JSON object")
        return artifact
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("signals: unreadable artifact %s: %s",
                     newest.name if newest else "<vanished>", exc)
        return None  # fail closed: unreadable == no signals
```

Change `_get_artifact` to parse and pass `as_of` (a malformed value resolves to `None` here so `@condition`'s validators never raise):
```python
def _get_artifact(request: HttpRequest):
    """One disk load per request: @condition calls _etag and _last_modified
    before the view body runs, and all three need the artifact. A malformed
    ?as_of resolves to None here so the conditional validators never raise;
    the view re-parses and returns 400."""
    if not hasattr(request, "_signals_artifact"):
        try:
            as_of = _as_of(request)
        except ValueError:
            request._signals_artifact = None
        else:
            request._signals_artifact = _load_artifact(as_of)
    return request._signals_artifact
```

Add the 400 guard at the very top of the view body (before the `_get_artifact` call):
```python
def news_signals(request: HttpRequest) -> JsonResponse:
    try:
        _as_of(request)
    except ValueError:
        return JsonResponse({'error': 'bad_as_of'}, status=400)
    artifact = _get_artifact(request)
    if artifact is None:
        return JsonResponse({'error': 'no_signals'}, status=404)
    # ... rest of the view body unchanged ...
```

- [ ] **Step 5: Run the new tests — must pass**

Run: `uv run pytest tests/test_signals_endpoint.py -q -k as_of`
Expected: all `*as_of*` tests PASS.

- [ ] **Step 6: Run the FULL endpoint suite — no regressions**

Run: `uv run pytest tests/test_signals_endpoint.py -q`
Expected: all tests PASS (original 17 + the 9 new = 26).

- [ ] **Step 7: Commit**

```bash
git add Main/backend/api/signals_views.py Main/backend/tests/test_signals_endpoint.py
git commit -m "feat(signals): ?as_of=YYYY-MM-DD point-in-time lookup on /api/signals/news/"
```

---

### Task 3: Document the parameter in the endpoint contract

**Files:**
- Modify: `Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md` (§4.4, the signals-endpoint contract)

- [ ] **Step 1: Add the `?as_of` paragraph to §4.4**

Locate the §4.4 endpoint contract section (search the file for `4.4` and `/api/signals/news/`). Append a bullet/paragraph:
```markdown
- **`?as_of=YYYY-MM-DD` (optional):** returns the newest artifact whose batch
  date is on or before the given day (point-in-time — no lookahead, robust to
  weekend/missed-run gaps). Resolution is by the artifact's filename stem date
  — candidates order by `(stem date, mtime, name)`, so a backfilled older day
  never outranks a newer day; `(mtime, name)` stays the same-day-supplemental
  tiebreak. Absent → newest overall.
  A date earlier than all retained artifacts → `404 {"error": "no_signals"}`;
  a future date → the latest artifact; a malformed value → `400 {"error":
  "bad_as_of"}`. History depth is bounded by retention (`SIGNALS_KEEP_N`,
  default 14 dated artifacts): a date older than the oldest retained artifact
  404s identically to "never produced" — callers cannot distinguish the two
  from the response alone. The per-date ticker set is whatever the watchlist
  was when that artifact was produced — it can change across the retained
  window (e.g. a watchlist deploy). Callers detect a gap by comparing the
  requested date to the returned `generated_at`. `staleness_hours` is
  unchanged (relative to now). Composes with `?tickers=`.
```

- [ ] **Step 2: Commit**

```bash
git add Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md
git commit -m "docs(signals): document ?as_of on the /api/signals/news/ contract (§4.4)"
```

---

## Self-Review

- **Spec coverage:** as_of semantics (point-in-time on-or-before) → Task 2; malformed → 400 → Task 2 `test_malformed_as_of_400s`; before-history → 404 → Task 2; future → latest → Task 2; stem-date resolution + same-day supplemental → Task 2; ETag correctness for free → Task 2 `test_as_of_etag_tracks_resolved_artifact`; tickers compose → Task 2; docs → Task 3. All spec §"Item 1" points covered. 2026-07-10 review amendments folded in: cross-day backfill/mtime-skew guard → Task 2 `test_as_of_prefers_newer_stem_date_over_newer_mtime` + stem-date-first selection key; retention-bounded history depth + per-date universe note → Task 3 §4.4 paragraph.
- **Placeholder scan:** none — every step has real code/commands.
- **Type consistency:** `_load_artifact` (Task 1 rename) → `_load_artifact(as_of=None)` (Task 2) consistent; `_as_of`/`_stem_date` return `date | None`; mock retarget matches the rename.
- **Rename ripple:** grep-verified 4 references (def, call, 2 in the mock) all handled in Task 1.
