# Persist the truth-layer store + silence the gunicorn control socket

**Date:** 2026-07-01
**Base:** `origin/main` @ `6d6f5e8` (#321) — contains the truth layer (#318), the
`/app/truthlayer/data` chown (#320), and the widened deploy health window (#321).
**Branch:** `feat/persist-truthlayer-store`

Two runtime cleanups left from the truth-layer deploy work:

1. **Persist the DuckDB store on the `/app/runtime` volume** so it is built once and
   reused across container restarts, instead of rebuilt (~45s) on every restart.
2. **Silence the gunicorn 25.1 control socket** so its recurring
   `Failed to start control socket: … Permission denied` warning stops.

---

## Task 1 — Persist the store on `/app/runtime`

### Problem

`truthlayer/store.py` sets `DB_PATH = DATA_DIR / "truthlayer.duckdb"`, where
`DATA_DIR = <package>/data`. That path resolves to `/app/truthlayer/data/…` inside
the container — the image's **writable layer**, which podman discards and recreates on
every `podman run` (i.e. every `systemctl --user restart fingpt-api`). So the one-time
`entrypoint.sh` build runs on **every** restart, not just on deploys — a ~45s cold
start each time.

Meanwhile the deploy already mounts a **persistent host volume** that nothing uses for
the store:

```
-v /home/deploy/fingpt/runtime:/app/runtime:U
```

(`:U` makes rootless podman recursively chown the host dir to the in-container uid
1001, so the non-root `fingpt` process can write it.)

### Root cause

The built artifact lives under `/app` (ephemeral image layer) instead of on the
mounted `/app/runtime` volume. `retrieve._store_is_current()` already knows how to
skip a rebuild when a valid, version-current store exists — it just never sees one
persist, because the file it checks is thrown away on every restart.

### Design

Move **only the built DB artifact** onto the volume. Keep `DATA_DIR` (and therefore
`ingest.CF_DIR = DATA_DIR/"companyfacts"`, the committed snapshots that are the build
*source*) in-package. This separation is load-bearing: if `DATA_DIR` itself moved to
`/app/runtime`, the build would read an empty `companyfacts/` dir and produce an
**empty store**.

#### 1. `truthlayer/store.py` — make only the DB path env-overridable

```python
import os
# ...
DATA_DIR = Path(__file__).resolve().parent / "data"          # vendored snapshots (read-only source)
DB_PATH = Path(os.environ.get("TRUTHLAYER_DB_PATH",
                              DATA_DIR / "truthlayer.duckdb"))  # built artifact (writable)
```

- Default is unchanged, so dev runs, the test suite, and offline fresh checkouts keep
  building into the in-package `data/` dir. Only production (where the env var is set)
  redirects the artifact to the volume.
- `retrieve.py` and `ingest.py` reference `store.DB_PATH` at call time, so nothing else
  changes. The temp build file `DB_PATH.with_name(".building-<pid>-…")` and its `.wal`
  sidecar are created next to `DB_PATH`, i.e. on the same `/app/runtime` filesystem, so
  the `os.replace()` rename stays atomic (no cross-device copy).

#### 2. `entrypoint.sh` — build once through the atomic path

Replace the inline build:

```sh
python -c "from truthlayer import ingest, retrieve; ingest.build_from_vendored().close() if not retrieve._store_is_current() else print('truth-layer store already current')"
```

with the atomic builder the request path already uses:

```sh
python -c "from truthlayer import retrieve; retrieve._ensure_built()"
```

`retrieve._ensure_built()` already:
- gates on `_store_is_current()` — a **fast no-op** when the persisted store exists and
  its `meta` stamp matches the running `build_versions()`;
- otherwise builds into a private `.building-<pid>-truthlayer.duckdb` temp, runs
  `CHECKPOINT` to fold in the WAL, closes the RW handle, then `os.replace()`s the temp
  into place (atomic on POSIX) and cleans up the temp + `.wal`.

Why this matters once the store is persistent: the old inline build wrote **directly**
into `DB_PATH`. That was fine while the store was ephemeral (a killed build just got a
fresh image layer next start), but on a persistent volume a build killed mid-write
(OOM, host restart) could leave a **corrupt `DB_PATH`** that wedges every subsequent
start. The temp-then-rename path never exposes a partial `DB_PATH`, and `os.replace`
overwrites even a pre-existing corrupt file, so the store self-heals.

Keep a human-readable `echo` around the call for log continuity (the previous
"already current" line moves into an `echo` in the shell, since `_ensure_built()`
returns silently).

#### 3. `Dockerfile`

- Add `/app/runtime` to the `mkdir -p` line and the `chown -R fingpt:fingpt` line, so
  the mountpoint exists and is writable by uid 1001 **even when no volume is mounted**
  (local `docker compose`, CI, bare `docker run`). Without a mount the store builds
  into the image layer (ephemeral, but functional); with the prod volume mounted, the
  same path is the persistent host dir.
- Set `ENV TRUTHLAYER_DB_PATH=/app/runtime/truthlayer.duckdb` (flat under the volume
  root; the `.building-*`/`.wal` siblings land there too).
- **Remove** `/app/truthlayer/data` from both the `mkdir -p` (line 44) and `chown -R`
  (line 66) lines, and rewrite the explanatory comment (lines 54–63). The store no
  longer writes under `/app`, so #320's chown of `/app/truthlayer/data` is obsolete;
  removing it **restores** the clean "no writable paths under /app" (P0 Root A.3)
  invariant. `/app/truthlayer/data/companyfacts/` stays root-owned and read-only — it
  is only *read* at build time.

#### 4. `.github/workflows/backend-deploy.yml`

No change. The `-v /home/deploy/fingpt/runtime:/app/runtime:U` mount already exists, and
#321's widened health window stays: the **first-ever** start on an empty volume and any
**version-drift** restart still pay the ~45s build, so the health check must still
tolerate a cold start. Steady-state restarts become fast.

### Correctness preserved

- **Version-drift invalidation is unchanged.** `_store_is_current()` compares the
  persisted store's `meta` stamp against `build_versions()` (`fact_id_recipe_version`,
  `registry_version`). A recipe/registry bump shipped in a new image still forces a
  rebuild — but now on the *first* start after the bump, not every start.
- **Concurrency is unchanged.** The store is still built single-process before gunicorn
  forks; workers still open it read-only.

---

## Task 2 — Silence the gunicorn control socket

### Problem / root cause

gunicorn 25.1 added a control socket for its `gunicornc` management CLI. Its
`control_socket` setting defaults to the **relative** path `gunicorn.ctl`, created in
the working directory — which is `WORKDIR /app`, root-owned. The container runs as
uid 1001 (`USER fingpt`), so the arbiter cannot create the socket and logs, at every
start and reload:

```
Failed to start control socket: [Errno 13] Permission denied
```

The arbiter catches this and continues, so it is non-fatal — just recurring noise.

### Design

Disable the control socket in `gunicorn.conf.py`:

```python
# gunicorn 25.1+ opens a Unix control socket (default: ./gunicorn.ctl in WORKDIR
# /app, which is root-owned) for the `gunicornc` management CLI we don't use. As
# non-root (uid 1001) that path is unwritable, so the arbiter logs a recurring
# "Failed to start control socket: Permission denied" warning. Disable it: removes
# the noise and the unused socket's (small) attack surface.
control_socket_disable = True
```

Disable rather than relocate: the feature is unused, disabling needs no writable path,
and it drops a small attack surface.

---

## Files changed

| File | Change |
|------|--------|
| `Main/backend/truthlayer/store.py` | `import os`; `DB_PATH` reads `TRUTHLAYER_DB_PATH` env, default unchanged |
| `Main/backend/entrypoint.sh` | build via `retrieve._ensure_built()`; keep an `echo` for log continuity |
| `Main/backend/Dockerfile` | add `/app/runtime` to `mkdir`+`chown`; `ENV TRUTHLAYER_DB_PATH=…`; drop `/app/truthlayer/data`; rewrite comment |
| `Main/backend/gunicorn.conf.py` | `control_socket_disable = True` + comment |
| `Main/backend/tests/test_dockerfile_nonroot.py` | add `/app/runtime` to `RUNTIME_DIRS` |
| `Main/backend/tests/test_truthlayer_store.py` (or new test file) | new test: `TRUTHLAYER_DB_PATH` overrides `DB_PATH` |
| gunicorn config test | new lightweight test: `control_socket_disable is True` |

## Testing

- **Unit:** `uv run pytest tests -q` green, including the existing
  `test_truthlayer_store.py` / `test_truthlayer_concurrency.py` (they monkeypatch
  `store.DB_PATH`, so the env-based default does not disturb them), the updated
  `test_dockerfile_nonroot.py`, and the two new tests.
- **Env-override test:** with `TRUTHLAYER_DB_PATH` set, `importlib.reload(store)` yields
  `store.DB_PATH == Path(env value)`; unset → default in-package path.
- **Local:** `docker compose build && docker compose up` — first start builds the store
  into the (unmounted) image-layer `/app/runtime` and serves; logs show **no**
  control-socket warning.
- **Prod (post-merge, CI push-to-main):** first deploy = one ~45s build → persisted on
  the volume; a subsequent `systemctl --user restart fingpt-api` logs "store already
  current", starts fast, health returns 200; no control-socket `EACCES` line.

## Out of scope

- The deploy workflow's volume mount (already correct) and #321's health window (still
  required for cold/first/drift builds).
- Persisting anything else on `/app/runtime` (YAGNI) — only the store.
- Silencing other logs (e.g. the benign gunicorn control-socket line is the only one
  targeted).
