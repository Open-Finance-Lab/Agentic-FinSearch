#!/usr/bin/env sh
set -eu

# ---- root init phase: load + PROVE the SSRF egress firewall, then drop privilege ----
# Runs only on the first (root-in-userns) invocation; re-execs self as uid1001.
# See ops/egress_firewall.py and Docs/superpowers/specs/2026-07-02-*egress-firewall*.
if [ "$(id -u)" = "0" ]; then
    RULES="$(mktemp)"
    # Temp-file, NOT a pipe: `python ... | nft -f -` fails OPEN under dash (a generator
    # crash yields empty stdin, nft exits 0, set -e never fires -> we serve with NO
    # firewall). Each guard below is fail-closed-and-loud, matching this file's idiom.
    python -m ops.egress_firewall > "$RULES" \
        || { echo "FATAL: egress ruleset generation failed" >&2; exit 1; }
    [ -s "$RULES" ] \
        || { echo "FATAL: egress ruleset empty" >&2; exit 1; }
    grep -q '169.254.0.0/16' "$RULES" \
        || { echo "FATAL: egress ruleset missing metadata drop sentinel" >&2; exit 1; }
    grep -qE 'ip6? daddr [0-9a-fA-F].* accept' "$RULES" \
        || { echo "FATAL: egress ruleset missing own-subnet accept" >&2; exit 1; }
    # Record metadata reachability BEFORE load so the self-test's "unreachable" result is
    # a true reachable->blocked transition on cloud, not a vacuous pass off-cloud.
    if python -m ops.egress_firewall --metadata-reachable; then META_BEFORE=1; else META_BEFORE=0; fi
    nft -f "$RULES" \
        || { echo "FATAL: nft failed to load egress ruleset" >&2; exit 1; }
    rm -f "$RULES"
    nft list table inet ssrf_egress >/dev/null 2>&1 \
        || { echo "FATAL: ssrf_egress table absent after load" >&2; exit 1; }
    # Active proof the DROP bites (fatal); redis/DNS reachability is advisory (non-fatal)
    # so a redis blip at reboot cannot fail-close a correct firewall.
    METADATA_WAS_REACHABLE="$META_BEFORE" python -m ops.egress_firewall --self-test \
        || { echo "FATAL: egress firewall self-test failed" >&2; exit 1; }
    echo "SSRF egress firewall loaded and self-tested."
    # :U chowned the runtime mount to root (PID1 is root); hand it to the app user.
    chown -R fingpt:fingpt /app/runtime
    # Marker (env survives setpriv) so the app phase can refuse to serve if it was ever
    # reached WITHOUT this root-init firewall load (e.g. a mistaken non-root PID1 start).
    # A MISTAKE-GUARD ONLY, not a security boundary: trivially spoofable via -e/--env-file,
    # and uid1001 cannot verify the table for real (nft list itself needs NET_ADMIN).
    export EGRESS_FW_LOADED=1
    # Drop to uid1001, remove NET_ADMIN from the BOUNDING set (so a compromised app
    # cannot re-arm/flush the firewall), set no_new_privs, and re-exec self as fingpt.
    exec setpriv --reuid=1001 --regid=1001 --init-groups \
         --bounding-set=-net_admin --no-new-privs -- "$0" "$@"
fi
# ---- app phase (uid 1001) continues below, unchanged ----

# Fail closed if the app phase is ever reached without the root-init firewall load
# (defends Decision 2: never serve with the SSRF egress firewall absent).
[ "${EGRESS_FW_LOADED:-}" = "1" ] \
    || { echo "FATAL: app phase reached without egress firewall (non-root PID1?)" >&2; exit 1; }

# Deploy pre-cutover gate mode (see backend-deploy.yml): everything the gate needs has
# already run above -- the FULL root-init (generate + sentinel checks + nft load +
# transition-proof self-test) plus the setpriv drop and the marker guard. Exit before
# the app phase's heavy work (store build, collectstatic). Running the gate through
# THIS script means it can never drift from what PID1 actually runs at boot.
# Placement is load-bearing (pinned by test_dockerfile_nonroot): above the root-init
# block this flag would exit 0 with NO firewall loaded -- a vacuous gate.
if [ "${1:-}" = "--egress-check-only" ]; then
    echo "Egress firewall check-only: root-init completed; skipping app phase."
    exit 0
fi

REQUIRE_OPENAI_API_KEY="${REQUIRE_OPENAI_API_KEY:-1}"

if [ "$REQUIRE_OPENAI_API_KEY" = "1" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    cat >&2 <<'EOF'
=======================================================
 FinGPT startup aborted: OPENAI_API_KEY is not set.
-------------------------------------------------------
 Add your real key to Main/backend/.env (used by Docker)
 or export OPENAI_API_KEY before running the container.

 To bypass this check (not recommended), set
   REQUIRE_OPENAI_API_KEY=0
=======================================================
EOF
    exit 1
fi

# Ensure cache directory exists (shared across gunicorn workers)
mkdir -p "${CACHE_FILE_PATH:-/tmp/fingpt_cache}"

# Ensure the XBRL truth-layer store exists ONCE, before gunicorn forks its workers.
# DuckDB's read-write lock is exclusive across processes, so the workers must all open
# the store read-only — which requires it to already exist. retrieve._ensure_built() is
# a no-op fast path when a version-current store is already present (e.g. persisted on
# the /app/runtime volume from a previous start); otherwise it builds into a private
# temp file and atomically renames it into place, so a build killed mid-write can never
# leave a corrupt store on the persistent volume. Its _store_is_current gate also rebuilds
# a volume-persisted store from an older recipe/registry version rather than trusting it.
# Single source of truth with the request-path builder.
echo "Ensuring XBRL truth-layer store is present..."
python -c "from truthlayer import retrieve; retrieve._ensure_built()" || {
    echo "ERROR: failed to build truth-layer store" >&2
    exit 1
}

if [ "${RUN_COLLECTSTATIC:-1}" = "1" ] && [ "${DJANGO_SETTINGS_MODULE:-django_config.settings}" = "django_config.settings_prod" ]; then
    echo "Running collectstatic for production assets..."
    python manage.py collectstatic --noinput
fi

# Runtime verification: Playwright is available
echo "Verifying Playwright runtime..."
python -c "from playwright.async_api import async_playwright; print('✓ Playwright runtime OK')" || {
    echo "ERROR: Playwright not available at runtime" >&2
    exit 1
}

# Deploy pre-cutover FULL-BOOT gate mode (see backend-deploy.yml). By this point the
# ENTIRE boot has already run under the deploy's exact --read-only/tmpfs/cap flags:
# root-init (nft load + sentinels + self-test + setpriv drop), the OPENAI key gate,
# the cache mkdir, the truth-layer store build, collectstatic, and the Playwright
# verify above -- so this mode SUBSUMES the --egress-check-only gate. It then proves
# the two things a later /health/ poll alone can never catch:
#   (a) every writable surface is really writable by uid1001. MCP stdio children
#       write $HOME fail-SOFT (logger.error in mcp_client/apps.py), so a missing or
#       root-owned tmpfs there would pass every health check while silently killing
#       the sec-edgar child -- exactly the EACCES class fixed in #331;
#   (b) the image's own CMD ("$@", byte-identical: the deploy gate passes no
#       positional args) comes up and answers /health/ within a bounded window.
# Placement is load-bearing (pinned by test_dockerfile_nonroot): this branch wraps
# the final exec, and the fall-through path still ends in exec "$@".
if [ "${BOOT_CHECK_ONLY:-}" = "1" ]; then
    echo "Boot check: probing writable surfaces..."
    for dir in /app/staticfiles /app/runtime "${CACHE_FILE_PATH:-/tmp/fingpt_cache}" "$HOME"; do
        probe="$dir/.boot-check-probe"
        ( touch "$probe" && rm -f "$probe" ) 2>/dev/null \
            || { echo "FATAL: boot check: cannot write $dir (missing/mis-owned tmpfs under --read-only?)" >&2; exit 1; }
    done
    echo "Boot check: writable surfaces OK; starting app for /health/ validation..."
    "$@" &
    APP_PID=$!
    HEALTH_OK=0
    tries=0
    # ~90s window (30 x 3s; refused connections fail fast), same python-urllib
    # probe as the Dockerfile HEALTHCHECK.
    while [ "$tries" -lt 30 ]; do
        if python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/', timeout=5)" 2>/dev/null; then
            HEALTH_OK=1
            break
        fi
        kill -0 "$APP_PID" 2>/dev/null \
            || { echo "FATAL: boot check: app exited before /health/ answered" >&2; exit 1; }
        tries=$((tries + 1))
        sleep 3
    done
    # Bounded shutdown: SIGTERM the app, watchdog hard-SIGKILLs if it hangs, so the
    # gate can never wedge the deploy past its own window.
    kill "$APP_PID" 2>/dev/null || true
    ( sleep 20; kill -9 "$APP_PID" 2>/dev/null ) &
    WATCHDOG_PID=$!
    wait "$APP_PID" 2>/dev/null || true
    kill "$WATCHDOG_PID" 2>/dev/null || true
    if [ "$HEALTH_OK" = "1" ]; then
        echo "Boot check: /health/ answered; full boot validated under deploy flags."
        exit 0
    fi
    echo "FATAL: boot check: /health/ did not answer within the ~90s window" >&2
    exit 1
fi

exec "$@"
