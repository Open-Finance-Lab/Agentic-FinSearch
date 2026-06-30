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
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY . .

RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

RUN mkdir -p /app/staticfiles /app/media /app/logs /tmp/fingpt_cache

RUN ln -sf /ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell /usr/bin/chromium-bundled

# Verify Playwright browsers are accessible
RUN echo "Verifying Playwright browsers..." && \
    ls -la /ms-playwright/ && \
    test -d /ms-playwright/chromium_headless_shell-* && \
    echo "✓ Chromium browser found at /ms-playwright"

# Create a non-root runtime user and own ONLY the writable runtime dirs.
# The application source under /app stays root-owned so a compromised
# process cannot rewrite code at runtime (P0 Root A.3: no-write-under-/app).
RUN groupadd --system --gid 1001 fingpt \
    && useradd --system --uid 1001 --gid fingpt --no-create-home fingpt \
    && chown -R fingpt:fingpt /app/staticfiles /app/media /app/logs /tmp/fingpt_cache

USER fingpt

ENTRYPOINT ["/app/entrypoint.sh"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=1m --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')" || exit 1

CMD ["gunicorn", \
    "django_config.wsgi:application", \
    "--config", "gunicorn.conf.py", \
    "--worker-tmp-dir", "/dev/shm"]
