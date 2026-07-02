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
    nft -f "$RULES" \
        || { echo "FATAL: nft failed to load egress ruleset" >&2; exit 1; }
    rm -f "$RULES"
    nft list table inet ssrf_egress >/dev/null 2>&1 \
        || { echo "FATAL: ssrf_egress table absent after load" >&2; exit 1; }
    # Active proof the DROP bites AND the own-subnet accept did not strand redis/DNS.
    python -m ops.egress_firewall --self-test \
        || { echo "FATAL: egress firewall self-test failed" >&2; exit 1; }
    echo "SSRF egress firewall loaded and self-tested."
    # :U chowned the runtime mount to root (PID1 is root); hand it to the app user.
    chown -R fingpt:fingpt /app/runtime
    # Drop to uid1001, remove NET_ADMIN from the BOUNDING set (so a compromised app
    # cannot re-arm/flush the firewall), set no_new_privs, and re-exec self as fingpt.
    exec setpriv --reuid=1001 --regid=1001 --init-groups \
         --bounding-set=-net_admin --no-new-privs -- "$0" "$@"
fi
# ---- app phase (uid 1001) continues below, unchanged ----

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

exec "$@"
