# Persist Truth-Layer Store + Silence Gunicorn Control Socket — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the DuckDB truth-layer store once onto the already-mounted `/app/runtime` volume (persisted across restarts) instead of rebuilding ~45s on every restart, and disable the unused gunicorn 25.1 control socket that logs a recurring `Permission denied` warning as the non-root user.

**Architecture:** Make only the *built DB artifact* path env-overridable (`TRUTHLAYER_DB_PATH`), keeping the vendored `companyfacts/` snapshots in-package as the read-only build source. Point the container's env at `/app/runtime`, create+chown that mountpoint in the image, and route the entrypoint's one-time build through the existing atomic `retrieve._ensure_built()` so a killed build can't corrupt the now-persistent store. Disable the gunicorn control socket in `gunicorn.conf.py`. Moving the artifact off `/app` also lets us drop #320's `/app/truthlayer/data` chown, restoring the no-write-under-`/app` invariant.

**Tech Stack:** Python 3.12, DuckDB, gunicorn 25.1, Docker/Podman, pytest, Django `SimpleTestCase` (static Dockerfile guards).

**Spec:** `Docs/superpowers/specs/2026-07-01-persist-truthlayer-store-design.md`
**Branch:** `feat/persist-truthlayer-store` (already created off `origin/main` @ `6d6f5e8`)

**Note on commands:** all `pytest` / build commands run from `Main/backend/` (that's where the `truthlayer` package and `pyproject.toml` live). Each command below includes the `cd`.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `Main/backend/truthlayer/store.py` | Store schema, paths, versioning | `DB_PATH` reads `TRUTHLAYER_DB_PATH` env (default = in-package); `DATA_DIR` unchanged |
| `Main/backend/entrypoint.sh` | One-time pre-fork store build | Build via `retrieve._ensure_built()` (atomic) instead of inline direct build |
| `Main/backend/Dockerfile` | Image + non-root hardening | Create+chown `/app/runtime`; set `TRUTHLAYER_DB_PATH`; drop `/app/truthlayer/data` mkdir+chown; rewrite comment |
| `Main/backend/gunicorn.conf.py` | gunicorn runtime config | Add `control_socket_disable = True` |
| `Main/backend/tests/test_truthlayer_store.py` | Store unit tests | Add env-override test |
| `Main/backend/tests/test_dockerfile_nonroot.py` | Static Dockerfile/deploy guards | Add `/app/runtime` to `RUNTIME_DIRS`; lock persistence wiring; lock `/app/truthlayer/data` no longer writable |
| `Main/backend/tests/test_gunicorn_conf.py` | Static gunicorn-config guard (new) | Assert control socket disabled |
| `Main/backend/tests/test_entrypoint_store_build.py` | Static entrypoint guard (new) | Assert entrypoint builds via `_ensure_built`, not the old direct build |

---

## Task 1: Env-overridable `DB_PATH` in `store.py`

**Files:**
- Modify: `Main/backend/truthlayer/store.py:1-9`
- Test: `Main/backend/tests/test_truthlayer_store.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `Main/backend/tests/test_truthlayer_store.py`:

```python
def test_db_path_honors_env_override(monkeypatch, tmp_path):
    # Production points the built artifact at the /app/runtime volume via
    # TRUTHLAYER_DB_PATH; dev/tests/offline default to the in-package data/ dir.
    # DB_PATH is computed at import, so reload the module under the patched env.
    import importlib

    override = tmp_path / "custom" / "store.duckdb"
    monkeypatch.setenv("TRUTHLAYER_DB_PATH", str(override))
    try:
        importlib.reload(store)
        assert store.DB_PATH == override
    finally:
        # Restore the in-package default so later tests in this session are unaffected.
        monkeypatch.delenv("TRUTHLAYER_DB_PATH", raising=False)
        importlib.reload(store)
    assert store.DB_PATH == store.DATA_DIR / "truthlayer.duckdb"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Main/backend && uv run pytest tests/test_truthlayer_store.py::test_db_path_honors_env_override -v`
Expected: FAIL — `AssertionError` (`store.DB_PATH` still equals the in-package default; the env var is ignored).

- [ ] **Step 3: Write minimal implementation**

In `Main/backend/truthlayer/store.py`, change the imports and the `DB_PATH` definition. Replace lines 1-9:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "truthlayer.duckdb"
```

with:

```python
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent / "data"
# The built store is a writable, regenerable artifact; its path is env-overridable so
# production can place it on the mounted /app/runtime volume (persisted across restarts)
# while dev/tests/offline checkouts default to the in-package data/ dir. DATA_DIR itself
# stays in-package: ingest reads the committed companyfacts/ snapshots from it (the
# read-only build *source*), which must not move onto the initially-empty volume.
DB_PATH = Path(os.environ.get("TRUTHLAYER_DB_PATH", DATA_DIR / "truthlayer.duckdb"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Main/backend && uv run pytest tests/test_truthlayer_store.py -v`
Expected: PASS — the new test plus all existing store tests (they monkeypatch `store.DB_PATH` directly, so the env default does not disturb them).

- [ ] **Step 5: Run the concurrency suite to confirm no regression**

Run: `cd Main/backend && uv run pytest tests/test_truthlayer_concurrency.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Main/backend/truthlayer/store.py Main/backend/tests/test_truthlayer_store.py
git commit -m "$(cat <<'EOF'
feat(truthlayer): make store DB_PATH env-overridable (TRUTHLAYER_DB_PATH)

The built DuckDB artifact path now reads TRUTHLAYER_DB_PATH, defaulting to the
in-package data/ dir. Lets production place the store on the persistent
/app/runtime volume while dev/tests/offline checkouts are unchanged. DATA_DIR
(the vendored companyfacts build source) deliberately stays in-package.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Atomic store build in `entrypoint.sh`

**Files:**
- Modify: `Main/backend/entrypoint.sh:24-35`
- Test: `Main/backend/tests/test_entrypoint_store_build.py` (create)

- [ ] **Step 1: Write the failing test**

Create `Main/backend/tests/test_entrypoint_store_build.py`:

```python
"""Static guard: entrypoint.sh builds the store through the atomic path.

Once the store persists on the /app/runtime volume, a build killed mid-write must
never leave a corrupt file. retrieve._ensure_built() builds into a temp file and
atomically renames it into place; the old inline build_from_vendored() wrote directly
into DB_PATH and is unsafe on a persistent volume. This locks that choice.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
ENTRYPOINT = os.path.join(_HERE, "..", "entrypoint.sh")


def _read():
    with open(ENTRYPOINT, "r", encoding="utf-8") as fh:
        return fh.read()


def test_entrypoint_builds_store_via_ensure_built():
    text = _read()
    assert "retrieve._ensure_built()" in text


def test_entrypoint_does_not_build_store_directly():
    text = _read()
    # The old direct build wrote straight into DB_PATH — unsafe on the persistent volume.
    assert "build_from_vendored()" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Main/backend && uv run pytest tests/test_entrypoint_store_build.py -v`
Expected: FAIL — both tests fail (entrypoint still calls `ingest.build_from_vendored()` and not `_ensure_built()`).

- [ ] **Step 3: Write minimal implementation**

In `Main/backend/entrypoint.sh`, replace lines 24-35 (the build comment block + the `echo` + the `python -c … || { … }` build):

```sh
# Build the XBRL truth-layer store ONCE, before gunicorn forks its workers. DuckDB's
# read-write lock is exclusive across processes, so the workers must all open the
# store read-only — which requires it to already exist. Building here (single process,
# connection closed immediately) avoids a cold-start lock fight between workers.
# Gate on _store_is_current (NOT mere existence): an image-baked store built under an
# older fact_id recipe / registry version must be rebuilt here, not lazily in a
# concurrent post-fork window. This mirrors the request-path gate in retrieve._ensure_built.
echo "Building XBRL truth-layer store..."
python -c "from truthlayer import ingest, retrieve; ingest.build_from_vendored().close() if not retrieve._store_is_current() else print('truth-layer store already current')" || {
    echo "ERROR: failed to build truth-layer store" >&2
    exit 1
}
```

with:

```sh
# Ensure the XBRL truth-layer store exists ONCE, before gunicorn forks its workers.
# DuckDB's read-write lock is exclusive across processes, so the workers must all open
# the store read-only — which requires it to already exist. retrieve._ensure_built() is
# a no-op fast path when a version-current store is already present (e.g. persisted on
# the /app/runtime volume from a previous start); otherwise it builds into a private
# temp file and atomically renames it into place, so a build killed mid-write can never
# leave a corrupt store on the persistent volume. Its _store_is_current gate also rebuilds
# an image-baked store from an older recipe/registry version rather than trusting it.
# Single source of truth with the request-path builder.
echo "Ensuring XBRL truth-layer store is present..."
python -c "from truthlayer import retrieve; retrieve._ensure_built()" || {
    echo "ERROR: failed to build truth-layer store" >&2
    exit 1
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Main/backend && uv run pytest tests/test_entrypoint_store_build.py -v`
Expected: PASS — both tests pass.

- [ ] **Step 5: Sanity-check the build path still works locally (uses the in-package default)**

Run: `cd Main/backend && uv run python -c "from truthlayer import retrieve; retrieve._ensure_built(); print('ensure_built OK')"`
Expected: prints `ensure_built OK` with no traceback (builds into the in-package `truthlayer/data/truthlayer.duckdb` if absent, else no-op).

- [ ] **Step 6: Commit**

```bash
git add Main/backend/entrypoint.sh Main/backend/tests/test_entrypoint_store_build.py
git commit -m "$(cat <<'EOF'
refactor(truthlayer): build store atomically in entrypoint via _ensure_built

Route the one-time pre-fork build through retrieve._ensure_built() (temp file +
atomic rename) instead of writing directly into DB_PATH. On the persistent
/app/runtime volume a build killed mid-write must not leave a corrupt store;
the atomic path prevents that and is a no-op when a version-current store exists.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Persist store on `/app/runtime` in the `Dockerfile`; drop #320 chown

**Files:**
- Modify: `Main/backend/Dockerfile:31-38` (ENV), `:44` (mkdir), `:54-66` (comment + chown)
- Test: `Main/backend/tests/test_dockerfile_nonroot.py:19`, `:48-60`, and a new test method

- [ ] **Step 1: Update the failing tests**

In `Main/backend/tests/test_dockerfile_nonroot.py`, change `RUNTIME_DIRS` (line 19) from:

```python
RUNTIME_DIRS = ["/app/staticfiles", "/app/media", "/app/logs", "/tmp/fingpt_cache"]
```

to:

```python
RUNTIME_DIRS = ["/app/staticfiles", "/app/media", "/app/logs", "/tmp/fingpt_cache", "/app/runtime"]
```

In `test_chown_only_runtime_dirs_not_whole_tree`, after the existing
`self.assertNotIn("/app/.venv", chown)` line (line 60), add:

```python
        # The store no longer builds under /app: /app/truthlayer/data (vendored
        # snapshots) stays root-owned and read-only, restoring no-write-under-/app.
        self.assertNotIn("/app/truthlayer/data", chown)
```

Add a new test method to the `DockerfileNonRootTests` class:

```python
    def test_store_persisted_on_runtime_volume(self):
        # The regenerable DuckDB store must build onto the /app/runtime volume
        # (persisted across restarts) — not the ephemeral image layer — and nothing
        # under /app/truthlayer stays writable.
        self.assertIn("TRUTHLAYER_DB_PATH=/app/runtime/truthlayer.duckdb", self.text)
        mkdir_lines = [l for l in self.lines if "mkdir -p" in l and "/app/runtime" in l]
        self.assertEqual(
            len(mkdir_lines), 1, f"expected /app/runtime in one mkdir line, got {mkdir_lines}"
        )
        # /app/truthlayer/data is no longer referenced in the Dockerfile at all
        # (the snapshots arrive via COPY and stay root-owned / read-only).
        self.assertNotIn("/app/truthlayer/data", self.text)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Main/backend && uv run pytest tests/test_dockerfile_nonroot.py -v`
Expected: FAIL — `test_chown_only_runtime_dirs_not_whole_tree` (chown lacks `/app/runtime`, still has `/app/truthlayer/data`) and `test_store_persisted_on_runtime_volume` (no `TRUTHLAYER_DB_PATH`, no `/app/runtime` mkdir, `/app/truthlayer/data` still present).

- [ ] **Step 3a: Add the `TRUTHLAYER_DB_PATH` env**

In `Main/backend/Dockerfile`, replace the ENV block (lines 31-38):

```dockerfile
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=django_config.settings_prod \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
```

with:

```dockerfile
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=django_config.settings_prod \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRUTHLAYER_DB_PATH=/app/runtime/truthlayer.duckdb
```

- [ ] **Step 3b: Point the mkdir at `/app/runtime`**

Replace line 44:

```dockerfile
RUN mkdir -p /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/truthlayer/data
```

with:

```dockerfile
RUN mkdir -p /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/runtime
```

- [ ] **Step 3c: Rewrite the comment and chown `/app/runtime`**

Replace the comment block + chown (lines 54-66):

```dockerfile
# Create a non-root runtime user and own ONLY the writable runtime dirs.
# The application source under /app stays root-owned so a compromised
# process cannot rewrite code at runtime (P0 Root A.3: no-write-under-/app).
# /app/truthlayer/data is the one deliberate exception: entrypoint.sh builds the
# DuckDB truth-layer store there at startup (from the root-owned, still-read-only
# vendored companyfacts JSON) BEFORE gunicorn forks. That build runs as `fingpt`,
# so the directory must be fingpt-writable -- otherwise duckdb.connect() aborts with
# "Permission denied" and the container exits 1. Only the regenerable .duckdb artifact
# is written here; no application code (.py) becomes writable, so the no-write-under-
# /app code-integrity intent is preserved.
RUN groupadd --system --gid 1001 fingpt \
    && useradd --system --uid 1001 --gid fingpt --no-create-home fingpt \
    && chown -R fingpt:fingpt /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/truthlayer/data
```

with:

```dockerfile
# Create a non-root runtime user and own ONLY the writable runtime dirs.
# The application source under /app stays root-owned so a compromised
# process cannot rewrite code at runtime (P0 Root A.3: no-write-under-/app).
# /app/runtime is the writable runtime-data volume mountpoint: entrypoint.sh builds the
# regenerable DuckDB truth-layer store there (from the root-owned, still-read-only
# vendored companyfacts JSON under /app/truthlayer/data) BEFORE gunicorn forks, and in
# production it is a persistent bind mount (podman -v ...:/app/runtime:U) so the store
# survives restarts. Owning it in the image keeps it writable even with no mount
# (compose/CI/bare docker run). Only this regenerable artifact dir is fingpt-writable;
# no application code (.py) becomes writable, so the no-write-under-/app intent holds.
RUN groupadd --system --gid 1001 fingpt \
    && useradd --system --uid 1001 --gid fingpt --no-create-home fingpt \
    && chown -R fingpt:fingpt /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/runtime
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Main/backend && uv run pytest tests/test_dockerfile_nonroot.py -v`
Expected: PASS — all methods, including the new `test_store_persisted_on_runtime_volume` and the tightened chown guard.

- [ ] **Step 5: Commit**

```bash
git add Main/backend/Dockerfile Main/backend/tests/test_dockerfile_nonroot.py
git commit -m "$(cat <<'EOF'
feat(deploy): persist truth-layer store on /app/runtime volume

Point TRUTHLAYER_DB_PATH at the already-mounted /app/runtime volume and
create+chown that mountpoint in the image, so the DuckDB store is built once
and reused across restarts instead of rebuilt (~45s) every time. Drops the now
-obsolete /app/truthlayer/data chown (#320), restoring the no-write-under-/app
invariant: the vendored snapshots there are read-only build input.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Disable the gunicorn control socket

**Files:**
- Modify: `Main/backend/gunicorn.conf.py:33` (insert after the process-settings block)
- Test: `Main/backend/tests/test_gunicorn_conf.py` (create)

- [ ] **Step 1: Write the failing test**

Create `Main/backend/tests/test_gunicorn_conf.py`:

```python
"""Static guard: the gunicorn config disables the unused control socket.

gunicorn 25.1's control_socket defaults to ./gunicorn.ctl in WORKDIR /app, which is
root-owned; as the non-root runtime user (uid 1001) that path is unwritable and the
arbiter logs a recurring "Failed to start control socket: Permission denied" warning on
every start and reload. We don't use the gunicornc management CLI, so it is disabled.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
GUNICORN_CONF = os.path.join(_HERE, "..", "gunicorn.conf.py")


def _load_gunicorn_conf():
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", GUNICORN_CONF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_control_socket_disabled():
    mod = _load_gunicorn_conf()
    assert mod.control_socket_disable is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Main/backend && uv run pytest tests/test_gunicorn_conf.py -v`
Expected: FAIL — `AttributeError: module 'gunicorn_conf_under_test' has no attribute 'control_socket_disable'`.

- [ ] **Step 3: Write minimal implementation**

In `Main/backend/gunicorn.conf.py`, insert after the process-settings block (after line 33, `tmp_upload_dir = None`, and before the blank line preceding `# ── Memory monitoring hooks ──`):

```python

# gunicorn 25.1+ opens a Unix control socket (default: ./gunicorn.ctl in WORKDIR /app,
# which is root-owned) for the `gunicornc` management CLI we don't use. As the non-root
# runtime user (uid 1001) that path is unwritable, so the arbiter logs a recurring
# "Failed to start control socket: [Errno 13] Permission denied" warning on every start
# and reload. Disable it: removes the noise and the unused socket's small attack surface.
control_socket_disable = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Main/backend && uv run pytest tests/test_gunicorn_conf.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Main/backend/gunicorn.conf.py Main/backend/tests/test_gunicorn_conf.py
git commit -m "$(cat <<'EOF'
fix(gunicorn): disable unused control socket to stop non-root EACCES warning

gunicorn 25.1's control_socket defaults to ./gunicorn.ctl in root-owned WORKDIR
/app; the non-root runtime user can't create it, so the arbiter logs a recurring
"Failed to start control socket: Permission denied" on every start/reload. We
don't use the gunicornc CLI — disable it (removes the noise + a small surface).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend test suite**

Run: `cd Main/backend && uv run pytest tests -q`
Expected: PASS — the whole suite green (the CI `test` job runs exactly this).

- [ ] **Step 2: Run Django system checks + deployment readiness (the CI `build` job's gates)**

Run: `cd Main/backend && uv run python manage.py check && uv run python verify_deployment.py`
Expected: no errors from either (system check identifies no issues; deployment readiness passes).

- [ ] **Step 3: Confirm the git state**

Run: `git log --oneline -5`
Expected: the four feature commits (Tasks 1-4) plus the spec commit, on `feat/persist-truthlayer-store`.

---

## Post-merge validation (manual, after CI push-to-main deploys)

Not part of the branch, but confirm the payoff on the droplet. The real deploy gate
(`backend-deploy` `test` + `deploy`) runs on push-to-main, not on PRs.

- First deploy of this change: `/home/deploy/fingpt/runtime` has no store yet → one
  ~45s build (health check tolerates it via #321's window) → store persisted.
- Then `systemctl --user restart fingpt-api` and check logs:
  - `Ensuring XBRL truth-layer store is present...` followed by a **fast** start
    (no ~45s rebuild; `_ensure_built()` is a no-op fast path).
  - **No** `Failed to start control socket` line.
  - `curl -sf http://localhost:8000/health/` returns 200.

---

## Self-Review

**Spec coverage:**
- Task 1 (store persistence) — store.py env override → Task 1; entrypoint atomic build → Task 2; Dockerfile mkdir/chown/ENV + drop #320 → Task 3; tests → Tasks 1 & 3. ✓
- Task 2 (control socket) — gunicorn.conf.py + test → Task 4. ✓
- Deploy workflow unchanged / #321 kept — noted, no task (correct: nothing to change). ✓
- Testing plan (unit, env-override, dockerfile guard, control-socket) → Tasks 1-5. ✓

**Placeholder scan:** none — every code/edit step shows exact content and exact commands. ✓

**Type/name consistency:** `TRUTHLAYER_DB_PATH`, `store.DB_PATH`, `store.DATA_DIR`, `retrieve._ensure_built()`, `control_socket_disable`, `RUNTIME_DIRS`, `/app/runtime/truthlayer.duckdb` used identically across store.py, entrypoint.sh, Dockerfile, gunicorn.conf.py, and all tests. ✓
