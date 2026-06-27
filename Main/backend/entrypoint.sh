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
