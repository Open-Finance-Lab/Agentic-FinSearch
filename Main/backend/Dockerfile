FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    software-properties-common \
    gpg-agent \
    curl \
    && add-apt-repository ppa:deadsnakes/ppa \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    nodejs \
    nftables \
    iproute2 \
    util-linux \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

RUN npm install -g @modelcontextprotocol/server-filesystem

COPY pyproject.toml uv.lock ./

# Install dependencies from the committed lock file for reproducible builds
RUN uv venv --python 3.12 && \
    uv sync --frozen --no-dev && \
    echo "Verifying Playwright installation..." && \
    .venv/bin/python -c "from playwright.async_api import async_playwright; print('✓ Playwright package installed')"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=django_config.settings_prod \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRUTHLAYER_DB_PATH=/app/runtime/truthlayer.duckdb \
    HOME=/home/fingpt

COPY . .

RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

RUN mkdir -p /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/runtime

RUN ln -sf /ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell /usr/bin/chromium-bundled

# Verify Playwright browsers are accessible
RUN echo "Verifying Playwright browsers..." && \
    ls -la /ms-playwright/ && \
    test -d /ms-playwright/chromium_headless_shell-* && \
    echo "✓ Chromium browser found at /ms-playwright"

# Create the non-root runtime user and own ONLY the writable runtime dirs.
# The application source under /app stays root-owned so a compromised process cannot
# rewrite code at runtime (P0 Root A.3: no-write-under-/app). PID1 (entrypoint.sh) runs
# as root-IN-USERNS -- rootless podman keeps that host-unprivileged -- solely to load the
# SSRF egress firewall (needs CAP_NET_ADMIN) and chown the runtime mount, then
# setpriv-drops to fingpt (uid1001, NET_ADMIN removed from the bounding set, no_new_privs)
# so the long-running app is non-root and cannot flush its own firewall. The
# no-write-under-/app intent holds: the app process is uid1001 and /app is root-owned;
# only the brief pre-app init is root and it writes nothing under /app except chowning the
# designated writable runtime mount (see entrypoint.sh; there is deliberately no USER line).
# /app/runtime is the writable runtime-data volume mountpoint: entrypoint.sh builds the
# regenerable DuckDB truth-layer store there BEFORE gunicorn forks; in production it is a
# persistent bind mount (podman -v ...:/app/runtime:U,Z) so the store survives restarts.
# Owning these dirs in the image keeps them writable with no mount (compose/CI/bare run).
# HOME (ENV above) + a real, fingpt-owned home dir are LOAD-BEARING: PID1 starts as
# root-in-userns, and setpriv drops the uid WITHOUT touching the environment -- so
# before the ENV pin the app (and every MCP stdio child, which inherits HOME via the
# SDK's get_default_environment) ran with HOME=/root it could not write. edgartools
# mkdirs ~/.edgar AT IMPORT, which killed the sec-edgar MCP child with EACCES on every
# boot from the root-init redesign (Jul 1) until this fix; yfinance's ~/.cache tz store
# degraded the same way, just non-fatally. --no-create-home stays (system-user idiom);
# the dir is created explicitly and owned alongside the other writable runtime dirs.
RUN groupadd --system --gid 1001 fingpt \
    && useradd --system --uid 1001 --gid fingpt --no-create-home fingpt \
    && mkdir -p /home/fingpt \
    && chown -R fingpt:fingpt /app/staticfiles /app/media /app/logs /tmp/fingpt_cache /app/runtime /home/fingpt

ENTRYPOINT ["/app/entrypoint.sh"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=1m --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')" || exit 1

CMD ["gunicorn", \
    "django_config.wsgi:application", \
    "--config", "gunicorn.conf.py", \
    "--worker-tmp-dir", "/dev/shm"]
