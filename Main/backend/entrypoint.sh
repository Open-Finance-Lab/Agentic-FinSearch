#!/usr/bin/env sh
set -eu

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
