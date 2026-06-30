# Security P0 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 4 pre-launch P0 security blockers in Agentic FinSearch (agentic RCE, SSRF, spend/abuse, ungated deploy) so the backend is safe to expose to a public community.

**Architecture:** Surgical, layered fixes — remove the unused writable filesystem MCP (+ server-side tool deny-list + non-root container), add one code-level SSRF egress validator at every fetch sink, bound the anonymous agent with a global concurrency + daily-run budget keyed through a swap-ready identity seam, and gate the CI deploy to `main`. No request-contract changes; main chat stays loginless but the per-caller key is built to become per-user when a login system lands.

**Tech Stack:** Django 5 (backend, `manage.py test` runner), `openai-agents` SDK, MCP (`mcp` SDK + npm `@modelcontextprotocol/server-filesystem`), `django_ratelimit`, gunicorn (gthread), Caddy edge, GitHub Actions, podman on the droplet.

**Source spec:** `Docs/superpowers/specs/2026-06-29-security-audit-remediation.md` (findings + approved design decisions).

**Test convention:** new backend tests live in `Main/backend/tests/` and run with `uv run python manage.py test tests.<module>` from `Main/backend/`. Pure-function tests use `django.test.SimpleTestCase` (no DB).

**Build order:** F-gate → Root A → Root B → Root C. Implement in small batches (the 16GB dev box OOM-froze at ~71 parallel agents — keep any fan-out ≤50).

---

## Task 1: Root F-gate — restrict backend deploy to `main`

**Files:**
- Modify: `.github/workflows/backend-deploy.yml` (the `deploy:` job, ~line 126)

- [ ] **Step 1: Add the branch gate to the deploy job**

In `.github/workflows/backend-deploy.yml`, find the `deploy:` job header and add a job-level `if` so `workflow_dispatch` from a non-main branch cannot deploy:

```yaml
  deploy:
    runs-on: ubuntu-latest
    if: ${{ github.ref == 'refs/heads/main' }}
    needs:
      - build
      - test
    permissions:
      contents: read
```

- [ ] **Step 2: Verify the gate locally with `act` or by inspection**

Run: `grep -n "if: \${{ github.ref == 'refs/heads/main' }}" .github/workflows/backend-deploy.yml`
Expected: one match on the `deploy` job. Confirm the sibling `concierge-tests.yml` / `heartbeat-tests.yml` use the same idiom (they do).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/backend-deploy.yml
git commit -m "ci(security): gate backend deploy to refs/heads/main (P0 cicd-supplychain-2)"
```

---

## Task 2: Root A.1 — disable the filesystem MCP server

**Files:**
- Modify: `Main/backend/mcp_server_config.json:3-10`
- Test: `Main/backend/tests/test_mcp_filesystem_disabled.py`

`mcp_manager.py:92` already honors `"disabled": true` (skips connecting the server), so this removes the tool from the agent entirely.

- [ ] **Step 1: Write the failing test**

```python
# Main/backend/tests/test_mcp_filesystem_disabled.py
import json
from pathlib import Path
from django.test import SimpleTestCase

CONFIG = Path(__file__).resolve().parent.parent / "mcp_server_config.json"


class FilesystemMcpDisabledTest(SimpleTestCase):
    def test_filesystem_server_is_disabled(self):
        cfg = json.loads(CONFIG.read_text())
        fs = cfg["mcpServers"].get("filesystem")
        # Either removed entirely, or explicitly disabled.
        self.assertTrue(fs is None or fs.get("disabled") is True,
                        "filesystem MCP must be removed or disabled (no RW /app for the public agent)")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python manage.py test tests.test_mcp_filesystem_disabled -v 2`
Expected: FAIL — `filesystem` currently has `"disabled": false`.

- [ ] **Step 3: Disable the server in config**

In `Main/backend/mcp_server_config.json`, set the filesystem block's flag:

```json
    "filesystem": {
      "command": "npx",
      "args": [
        "@modelcontextprotocol/server-filesystem",
        "/app"
      ],
      "disabled": true
    },
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run python manage.py test tests.test_mcp_filesystem_disabled -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Main/backend/mcp_server_config.json Main/backend/tests/test_mcp_filesystem_disabled.py
git commit -m "fix(security): disable filesystem MCP server — unused, RW /app = agentic RCE (P0 Root A)"
```

---

## Task 3: Root A.2 — server-side tool deny-list (defense-in-depth)

Even with the server disabled, enforce that dangerous tool names can never execute or attach, so a future re-enable or new server can't silently reopen the hole.

**Files:**
- Create: `Main/backend/mcp_client/tool_policy.py`
- Modify: `Main/backend/mcp_client/mcp_manager.py` (`execute_tool`, ~line 215) and `Main/backend/mcp_client/agent.py` (~line 209, after tool collection)
- Test: `Main/backend/tests/test_tool_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# Main/backend/tests/test_tool_policy.py
from django.test import SimpleTestCase
from mcp_client.tool_policy import is_denied_tool, filter_denied


class ToolPolicyTest(SimpleTestCase):
    def test_filesystem_write_tools_denied(self):
        for name in ["write_file", "edit_file", "create_directory", "move_file", "read_file", "list_directory"]:
            self.assertTrue(is_denied_tool(name), f"{name} must be denied")

    def test_finance_tools_allowed(self):
        for name in ["get_stock_info", "scrape_url", "navigate_to_url", "get_filing"]:
            self.assertFalse(is_denied_tool(name), f"{name} must be allowed")

    def test_filter_drops_denied_objects(self):
        class T:
            def __init__(self, n): self.name = n
        kept = filter_denied([T("get_stock_info"), T("write_file"), T("scrape_url")])
        self.assertEqual([t.name for t in kept], ["get_stock_info", "scrape_url"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run python manage.py test tests.test_tool_policy -v 2`
Expected: FAIL with `ModuleNotFoundError: mcp_client.tool_policy`.

- [ ] **Step 3: Implement the policy module**

```python
# Main/backend/mcp_client/tool_policy.py
"""Server-side allow/deny policy for agent tools (defense-in-depth).

Deny-by-default for filesystem-mutating / file-access tools. The public agent
never legitimately needs local file I/O; this guarantees that even a
re-introduced filesystem MCP cannot be reached.
"""
from typing import Iterable, List

# Exact tool names exposed by @modelcontextprotocol/server-filesystem.
_DENIED_TOOLS = frozenset({
    "read_file", "read_text_file", "read_media_file", "read_multiple_files",
    "write_file", "edit_file", "create_directory", "list_directory",
    "list_directory_with_sizes", "directory_tree", "move_file",
    "search_files", "get_file_info", "list_allowed_directories",
})


def is_denied_tool(name: str) -> bool:
    return name in _DENIED_TOOLS


def filter_denied(tools: Iterable) -> List:
    """Drop any tool object whose .name is denied."""
    return [t for t in tools if not is_denied_tool(getattr(t, "name", ""))]
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run python manage.py test tests.test_tool_policy -v 2`
Expected: PASS.

- [ ] **Step 5: Enforce at execution (mcp_manager.execute_tool)**

In `Main/backend/mcp_client/mcp_manager.py`, at the top of `execute_tool` (after the docstring, before logging, ~line 217):

```python
        from mcp_client.tool_policy import is_denied_tool
        if is_denied_tool(tool_name):
            self._log(f"[MCP TOOL DENIED] Policy blocked tool '{tool_name}'", force=True)
            raise PermissionError(f"Tool '{tool_name}' is denied by server-side policy")
```

- [ ] **Step 6: Enforce at attachment (agent.py)**

In `Main/backend/mcp_client/agent.py`, immediately after the MCP tools are appended and before the `allowed_tools` filter (~line 209), strip denied tools unconditionally:

```python
        # Defense-in-depth: never attach denied (filesystem/file-access) tools,
        # even when a skill passes allowed_tools=None.
        from .tool_policy import filter_denied
        tools = filter_denied(tools)
```

- [ ] **Step 7: Add a regression test that the agent never attaches denied tools**

```python
# append to Main/backend/tests/test_tool_policy.py
    def test_filter_is_idempotent_and_total(self):
        class T:
            def __init__(self, n): self.name = n
        only_denied = filter_denied([T("write_file"), T("edit_file")])
        self.assertEqual(only_denied, [])
```

- [ ] **Step 8: Run the full module + commit**

Run: `uv run python manage.py test tests.test_tool_policy -v 2`
Expected: PASS (3 tests).

```bash
git add Main/backend/mcp_client/tool_policy.py Main/backend/mcp_client/mcp_manager.py Main/backend/mcp_client/agent.py Main/backend/tests/test_tool_policy.py
git commit -m "fix(security): deny-by-default tool policy blocks filesystem tools at attach+exec (P0 Root A)"
```

---

## Task 4: Root A.3 — run the container as a non-root user

**Files:**
- Modify: `Main/backend/Dockerfile` (after `COPY . .` / dir creation, before `ENTRYPOINT`)

Playwright base image keeps browsers in `/ms-playwright` (world-readable). The app dir `/app` and runtime/cache dirs must be owned by the new user.

- [ ] **Step 1: Add a non-root user and fix ownership**

In `Main/backend/Dockerfile`, after the `RUN mkdir -p /app/staticfiles /app/media /app/logs /tmp/fingpt_cache` line and before `ENTRYPOINT`:

```dockerfile
# Run as a non-root user so a future RCE is not root-equivalent.
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app /tmp/fingpt_cache
USER app
```

- [ ] **Step 2: Build and verify the runtime user**

Run:
```bash
docker build -t fingpt-sec-test Main/backend
docker run --rm --entrypoint sh fingpt-sec-test -c 'id -un'
```
Expected: prints `app` (not `root`).

- [ ] **Step 3: Smoke-test that the app still boots**

Run: `docker run --rm -e REQUIRE_OPENAI_API_KEY=0 -e DJANGO_SETTINGS_MODULE=django_config.settings --entrypoint sh fingpt-sec-test -c 'python manage.py check'`
Expected: `System check identified no issues`.

- [ ] **Step 4: Commit**

```bash
git add Main/backend/Dockerfile
git commit -m "fix(security): run backend container as non-root user 'app' (P0 Root A)"
```

> Note: the droplet runs the image via `podman run ... --memory=1.7g ... -v /home/deploy/fingpt/runtime:/app/runtime`. The mounted `runtime` dir on the host must be writable by uid that maps to container `app`; if podman userns mapping rejects writes, add `:U` to the volume (`-v ...:/app/runtime:U`) in `backend-deploy.yml` so podman chowns it. Flag for the deploy step.

---

## Task 5: Root B.1 — the SSRF egress validator

**Files:**
- Create: `Main/backend/datascraper/ssrf_guard.py`
- Test: `Main/backend/tests/test_ssrf_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
# Main/backend/tests/test_ssrf_guard.py
from unittest import mock
from django.test import SimpleTestCase
from datascraper.ssrf_guard import validate_fetch_url, UnsafeURLError


def _addrinfo(ip):
    return [(2, 1, 6, "", (ip, 0))]


class SsrfGuardTest(SimpleTestCase):
    def test_rejects_non_http_scheme(self):
        for url in ["file:///etc/passwd", "ftp://host/x", "gopher://h"]:
            with self.assertRaises(UnsafeURLError):
                validate_fetch_url(url)

    def test_rejects_missing_host(self):
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("http:///nohost")

    @mock.patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_rejects_cloud_metadata(self, gai):
        gai.return_value = _addrinfo("169.254.169.254")
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("http://169.254.169.254/metadata/v1/")

    @mock.patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_rejects_loopback_and_private(self, gai):
        for ip in ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.9"]:
            gai.return_value = _addrinfo(ip)
            with self.assertRaises(UnsafeURLError):
                validate_fetch_url(f"http://internal.example/")

    @mock.patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_allows_public(self, gai):
        gai.return_value = _addrinfo("93.184.216.34")  # example.com
        self.assertEqual(validate_fetch_url("https://example.com/news"),
                         "https://example.com/news")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python manage.py test tests.test_ssrf_guard -v 2`
Expected: FAIL — `ModuleNotFoundError: datascraper.ssrf_guard`.

- [ ] **Step 3: Implement the guard**

```python
# Main/backend/datascraper/ssrf_guard.py
"""Code-level SSRF egress guard for all outbound fetches.

Resolves the host and blocks any URL that resolves to a non-public address
(loopback / private / link-local / metadata / reserved), enforces http(s)-only,
and is the single chokepoint every scrape/browser/auto_scrape path calls.
"""
import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """Raised when a URL is not safe to fetch server-side."""


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def validate_fetch_url(url: str) -> str:
    """Return url if safe; raise UnsafeURLError otherwise.

    NOTE: callers must also set allow_redirects=False (or re-validate every
    redirect hop) — a 30x to an internal host bypasses this one-shot check.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("missing host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for {host!r}: {exc}")
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_ip(ip_str):
            raise UnsafeURLError(f"{host!r} resolves to blocked address {ip_str}")
    return url
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python manage.py test tests.test_ssrf_guard -v 2`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add Main/backend/datascraper/ssrf_guard.py Main/backend/tests/test_ssrf_guard.py
git commit -m "feat(security): SSRF egress validator (block private/metadata IPs) (P0 Root B)"
```

---

## Task 6: Root B.2 — wire the guard into every fetch sink

**Files:**
- Modify: `Main/backend/datascraper/url_tools.py` (`_scrape_url_impl`, ~line 225; `scrape_with_playwright`)
- Modify: `Main/backend/datascraper/playwright_tools.py` (`navigate_to_url`, `click_element`, `extract_page_content`)
- Modify: `Main/backend/api/views.py` (`auto_scrape`, ~line 831)
- Modify: `Deploy/podman/Caddyfile.example` (set a trustworthy client IP header — used by Task 7)
- Test: `Main/backend/tests/test_scrape_ssrf_wired.py`

- [ ] **Step 1: Write the failing test (the sink rejects a blocked URL)**

```python
# Main/backend/tests/test_scrape_ssrf_wired.py
import json
from unittest import mock
from django.test import SimpleTestCase
from datascraper import url_tools


class ScrapeSsrfWiredTest(SimpleTestCase):
    @mock.patch("datascraper.url_tools.requests.get")
    @mock.patch("datascraper.url_tools.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("169.254.169.254", 0))])
    def test_impl_refuses_blocked_and_never_fetches(self, _gai, rget):
        out = json.loads(url_tools._scrape_url_impl("http://169.254.169.254/meta"))
        self.assertIn("error", out)
        rget.assert_not_called()
```

(Imports `socket` into `url_tools` — see Step 3.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python manage.py test tests.test_scrape_ssrf_wired -v 2`
Expected: FAIL — currently `requests.get` IS called.

- [ ] **Step 3: Guard `_scrape_url_impl`**

In `Main/backend/datascraper/url_tools.py`, add imports at top: `import socket` and `from datascraper.ssrf_guard import validate_fetch_url, UnsafeURLError`. Replace the opening of `_scrape_url_impl` (the scheme check block) with:

```python
def _scrape_url_impl(url: str) -> str:
    """Core scraping logic - callable directly."""
    try:
        validate_fetch_url(url)
    except UnsafeURLError as exc:
        logger.warning(f"[SSRF] refused {url}: {exc}")
        return json.dumps({"error": "URL not allowed", "url": url})
```

Then change the fetch line to not follow redirects into an unvalidated host:

```python
        response = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=False)
        if response.is_redirect or response.is_permanent_redirect:
            return json.dumps({"error": "Redirects are not followed", "url": url})
        response.raise_for_status()
```

- [ ] **Step 4: Guard the Playwright entrypoints**

In `Main/backend/datascraper/playwright_tools.py`, add `from datascraper.ssrf_guard import validate_fetch_url, UnsafeURLError`, and at the top of each of `navigate_to_url`, `click_element`, `extract_page_content` (and `scrape_with_playwright` in `url_tools.py`), before any navigation:

```python
    try:
        validate_fetch_url(url)
    except UnsafeURLError as exc:
        return json.dumps({"error": f"URL not allowed: {exc}"})
```

(Match each function's existing return type — these tools return JSON strings; `scrape_with_playwright` returns `""` on failure, so there return `""`.)

- [ ] **Step 5: Guard the `auto_scrape` view**

In `Main/backend/api/views.py` `auto_scrape` (~line 831), validate `current_url` before it reaches the scraper:

```python
        from datascraper.ssrf_guard import validate_fetch_url, UnsafeURLError
        try:
            validate_fetch_url(current_url)
        except UnsafeURLError:
            return JsonResponse({"status": "error", "error": "URL not allowed"}, status=400)
```

- [ ] **Step 6: Caddy — pass the real client IP for Task 7**

In `Deploy/podman/Caddyfile.example`, inside the `reverse_proxy` block, add:

```caddyfile
    reverse_proxy fingpt-api:8000 {
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-Host {host}
        header_up X-Real-IP {remote_host}
    }
```

- [ ] **Step 7: Run the sink test + full scrape suite**

Run: `uv run python manage.py test tests.test_scrape_ssrf_wired tests.test_ssrf_guard -v 2`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add Main/backend/datascraper/url_tools.py Main/backend/datascraper/playwright_tools.py Main/backend/api/views.py Deploy/podman/Caddyfile.example Main/backend/tests/test_scrape_ssrf_wired.py
git commit -m "fix(security): enforce SSRF guard at all scrape/browser/auto_scrape sinks; Caddy X-Real-IP (P0 Root B)"
```

---

## Task 7: Root C.1 — identity seam + trusted-IP rate-limit key

The forward-compat seam Felix requested: every per-caller limit keys off `get_request_identity()`, which returns the trusted client IP today and `user:<id>` once login lands — no decorator changes needed later.

**Files:**
- Create: `Main/backend/api/identity.py`
- Modify: `Main/backend/api/views.py` and `Main/backend/api/openai_views.py` — change `@ratelimit(key='ip', ...)` → `key='api.identity.ratelimit_key'`
- Test: `Main/backend/tests/test_identity.py`

- [ ] **Step 1: Write the failing test**

```python
# Main/backend/tests/test_identity.py
from django.test import SimpleTestCase, RequestFactory
from api.identity import get_request_identity, get_client_ip


class IdentityTest(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_prefers_x_real_ip(self):
        req = self.rf.get("/", HTTP_X_REAL_IP="203.0.113.7", REMOTE_ADDR="172.18.0.2")
        self.assertEqual(get_client_ip(req), "203.0.113.7")

    def test_falls_back_to_remote_addr(self):
        req = self.rf.get("/", REMOTE_ADDR="203.0.113.9")
        self.assertEqual(get_client_ip(req), "203.0.113.9")

    def test_identity_is_ip_scoped_today(self):
        req = self.rf.get("/", HTTP_X_REAL_IP="203.0.113.7")
        self.assertEqual(get_request_identity(req), "ip:203.0.113.7")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python manage.py test tests.test_identity -v 2`
Expected: FAIL — `ModuleNotFoundError: api.identity`.

- [ ] **Step 3: Implement the seam**

```python
# Main/backend/api/identity.py
"""Caller identity seam.

Single source of truth for "who is this request" used by rate-limiting and the
agent budget. Today: the trusted client IP (Caddy sets X-Real-IP; the backend
is only reachable via Caddy on the droplet, 127.0.0.1:8000 bind). Tomorrow:
when a user-login system lands, return f"user:{request.user.id}" for
authenticated callers — no call-site changes required.
"""
from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    real_ip = request.META.get("HTTP_X_REAL_IP")
    if real_ip:
        return real_ip.strip()
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def get_request_identity(request: HttpRequest) -> str:
    # FORWARD-COMPAT seam (Felix, 2026-06-29): when login exists, do
    #   if request.user.is_authenticated: return f"user:{request.user.id}"
    return f"ip:{get_client_ip(request)}"


def ratelimit_key(group: str, request: HttpRequest) -> str:
    """django_ratelimit callable key."""
    return get_request_identity(request)
```

- [ ] **Step 4: Repoint the rate-limit decorators**

In `Main/backend/api/views.py` and `Main/backend/api/openai_views.py`, replace every `@ratelimit(key='ip', ...)` with `@ratelimit(key='api.identity.ratelimit_key', ...)` (keep `rate`, `method`, `block` unchanged).

Run: `grep -rn "key='ip'" Main/backend/api/` → expect **no** matches after the edit.

- [ ] **Step 5: Run identity test + a rate-limit smoke test**

```python
# append to Main/backend/tests/test_identity.py
from django.test import Client, override_settings


class RateLimitKeyWiringTest(SimpleTestCase):
    @override_settings(RATELIMIT_ENABLE=True)
    def test_health_still_responds(self):
        # health is exempt/unrated; just assert wiring import path resolves
        from api import identity
        self.assertTrue(callable(identity.ratelimit_key))
```

Run: `uv run python manage.py test tests.test_identity -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Main/backend/api/identity.py Main/backend/api/views.py Main/backend/api/openai_views.py Main/backend/tests/test_identity.py
git commit -m "feat(security): identity seam + trusted X-Real-IP rate-limit key (per-user ready) (P0 Root C)"
```

---

## Task 8: Root C.2 — global concurrency + daily-run budget

Cross-worker counters in the shared cache (gunicorn runs 2 worker processes; an in-process semaphore would only cap per-worker).

**Files:**
- Create: `Main/backend/api/agent_budget.py`
- Test: `Main/backend/tests/test_agent_budget.py`

- [ ] **Step 1: Write the failing tests**

```python
# Main/backend/tests/test_agent_budget.py
from django.test import SimpleTestCase, override_settings
from django.core.cache import cache
from api import agent_budget
from api.agent_budget import BudgetExceeded, ConcurrencyExceeded, agent_run_slot


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class AgentBudgetTest(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_daily_budget_blocks_after_limit(self):
        agent_budget.AGENT_DAILY_RUN_BUDGET = 2
        agent_budget.AGENT_MAX_CONCURRENCY = 100
        with agent_run_slot():
            pass
        with agent_run_slot():
            pass
        with self.assertRaises(BudgetExceeded):
            with agent_run_slot():
                pass

    def test_concurrency_blocks_when_full(self):
        agent_budget.AGENT_DAILY_RUN_BUDGET = 1000
        agent_budget.AGENT_MAX_CONCURRENCY = 1
        with agent_run_slot():
            with self.assertRaises(ConcurrencyExceeded):
                with agent_run_slot():
                    pass

    def test_slot_releases_concurrency_on_exit(self):
        agent_budget.AGENT_DAILY_RUN_BUDGET = 1000
        agent_budget.AGENT_MAX_CONCURRENCY = 1
        with agent_run_slot():
            pass
        with agent_run_slot():  # must not raise — previous slot released
            pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python manage.py test tests.test_agent_budget -v 2`
Expected: FAIL — `ModuleNotFoundError: api.agent_budget`.

- [ ] **Step 3: Implement the budget**

```python
# Main/backend/api/agent_budget.py
"""Global blast-radius caps for the anonymous agent path.

- Concurrency: at most AGENT_MAX_CONCURRENCY agent runs in flight (cross-worker,
  counted in the shared cache).
- Daily budget: at most AGENT_DAILY_RUN_BUDGET agent runs per UTC day.

Both raise on exhaustion so the view can return HTTP 503 + Retry-After. Counters
are keyed globally now; the daily key can become per-identity when login lands.
"""
import datetime
import os
from contextlib import contextmanager

from django.core.cache import cache

AGENT_MAX_CONCURRENCY = int(os.getenv("AGENT_MAX_CONCURRENCY", "3"))
AGENT_DAILY_RUN_BUDGET = int(os.getenv("AGENT_DAILY_RUN_BUDGET", "2000"))

_INFLIGHT_KEY = "agent:inflight"
_DAY_TTL = 60 * 60 * 26  # a bit over a day


class BudgetExceeded(Exception):
    """Daily run budget exhausted."""


class ConcurrencyExceeded(Exception):
    """Too many concurrent agent runs."""


def _daily_key() -> str:
    return f"agent:runs:{datetime.datetime.utcnow().date().isoformat()}"


def _incr(key: str, ttl: int) -> int:
    if cache.add(key, 1, ttl):  # set only if absent; returns True if it set
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl)
        return 1


@contextmanager
def agent_run_slot():
    # Daily budget (counts every attempt that passes the gate).
    day_key = _daily_key()
    day_count = _incr(day_key, _DAY_TTL)
    if day_count > AGENT_DAILY_RUN_BUDGET:
        raise BudgetExceeded(f"daily agent-run budget {AGENT_DAILY_RUN_BUDGET} exhausted")

    # Concurrency.
    inflight = _incr(_INFLIGHT_KEY, _DAY_TTL)
    if inflight > AGENT_MAX_CONCURRENCY:
        try:
            cache.decr(_INFLIGHT_KEY)
        except ValueError:
            pass
        raise ConcurrencyExceeded(f"max concurrency {AGENT_MAX_CONCURRENCY} reached")
    try:
        yield
    finally:
        try:
            cache.decr(_INFLIGHT_KEY)
        except ValueError:
            pass
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python manage.py test tests.test_agent_budget -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add Main/backend/api/agent_budget.py Main/backend/tests/test_agent_budget.py
git commit -m "feat(security): global concurrency + daily-run budget for the agent (P0 Root C)"
```

---

## Task 9: Root C.3 — enforce the budget at the chat entrypoints

**Files:**
- Modify: `Main/backend/api/views.py` — `chat_response` (~219), `chat_response_stream` (~518), `agent_chat_response` (~430), `adv_response`/`adv_response_stream` (the agent-driving views)
- Test: `Main/backend/tests/test_chat_budget_enforced.py`

- [ ] **Step 1: Write the failing test (503 when budget exhausted)**

```python
# Main/backend/tests/test_chat_budget_enforced.py
from unittest import mock
from django.test import SimpleTestCase, RequestFactory
from api import views


class ChatBudgetEnforcedTest(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    @mock.patch("api.views.agent_run_slot")
    def test_returns_503_when_concurrency_exceeded(self, slot):
        from api.agent_budget import ConcurrencyExceeded
        slot.return_value.__enter__.side_effect = ConcurrencyExceeded("full")
        req = self.rf.get("/get_chat_response_stream/", {"question": "hi", "models": "gpt-4o-mini"})
        resp = views.chat_response_stream(req)
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Retry-After", resp)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python manage.py test tests.test_chat_budget_enforced -v 2`
Expected: FAIL — view does not yet use `agent_run_slot`.

- [ ] **Step 3: Wrap each agent-driving view**

In `Main/backend/api/views.py`, add import near the top:

```python
from api.agent_budget import agent_run_slot, BudgetExceeded, ConcurrencyExceeded
```

Then, in each agent-driving view, enter the slot around the agent execution (place it after request parsing, around the part that builds/calls the agent). For the streaming view the slot must wrap the generator's lifetime; the simplest correct form is a guard at the top that returns 503 before starting, and a slot held for the synchronous (non-stream) views around the agent call:

```python
    try:
        _slot = agent_run_slot()
        _slot.__enter__()
    except (BudgetExceeded, ConcurrencyExceeded) as exc:
        from django.http import JsonResponse
        resp = JsonResponse({"error": "Service busy, please retry shortly"}, status=503)
        resp["Retry-After"] = "30"
        return resp
```

For streaming (`chat_response_stream`, `adv_response_stream`), release the slot when the stream finalizes — call `_slot.__exit__(None, None, None)` in the generator's `finally` (the SSE finalization block already exists; add the release there). For non-stream views, wrap the agent call in `with agent_run_slot():` instead of the manual enter/exit.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run python manage.py test tests.test_chat_budget_enforced -v 2`
Expected: PASS.

- [ ] **Step 5: Manual concurrency check (optional, local)**

Set `AGENT_MAX_CONCURRENCY=1`, fire two overlapping requests to `/get_chat_response_stream/`; the second returns 503.

- [ ] **Step 6: Commit**

```bash
git add Main/backend/api/views.py Main/backend/tests/test_chat_budget_enforced.py
git commit -m "fix(security): 503 when agent concurrency/daily budget exhausted (P0 Root C)"
```

---

## Task 10: Root C.4 — require FINGPT_API_KEY in prod + gunicorn hardening

**Files:**
- Modify: `Main/backend/django_config/settings_prod.py` (add the fail-closed check, ~after line 59)
- Modify: `Main/backend/api/openai_views.py` (`_authenticate_request`, ~line 72 — read from settings, not a bare env "disabled" branch)
- Modify: `Main/backend/gunicorn.conf.py` (threads + default timeout)
- Test: `Main/backend/tests/test_openai_auth_failclosed.py`

- [ ] **Step 1: Write the failing test**

```python
# Main/backend/tests/test_openai_auth_failclosed.py
from unittest import mock
from django.test import SimpleTestCase, RequestFactory
from api import openai_views


class OpenAiAuthFailClosedTest(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    @mock.patch.object(openai_views, "REQUIRE_API_KEY", True)
    @mock.patch.object(openai_views, "_API_KEY", "secret-key")
    def test_missing_bearer_is_401(self):
        req = self.rf.get("/v1/models")
        resp = openai_views._authenticate_request(req)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.status_code, 401)

    @mock.patch.object(openai_views, "REQUIRE_API_KEY", True)
    @mock.patch.object(openai_views, "_API_KEY", "secret-key")
    def test_correct_bearer_passes(self):
        req = self.rf.get("/v1/models", HTTP_AUTHORIZATION="Bearer secret-key")
        self.assertIsNone(openai_views._authenticate_request(req))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run python manage.py test tests.test_openai_auth_failclosed -v 2`
Expected: FAIL — module has no `REQUIRE_API_KEY`/`_API_KEY` symbols yet.

- [ ] **Step 3: Make `_authenticate_request` fail-closed via settings**

In `Main/backend/api/openai_views.py`, near the top add:

```python
from django.conf import settings

_API_KEY = getattr(settings, "FINGPT_API_KEY", "") or ""
REQUIRE_API_KEY = getattr(settings, "REQUIRE_FINGPT_API_KEY", False)
```

Replace the body of `_authenticate_request` so the "disabled when unset" path only applies when not required:

```python
def _authenticate_request(request):
    if not _API_KEY:
        if REQUIRE_API_KEY:
            return JsonResponse(
                {'error': {'message': 'Server API key not configured', 'type': 'server_error'}},
                status=503)
        return None  # dev mode only
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse(
            {'error': {'message': 'Missing/invalid Authorization header', 'type': 'authentication_error'}},
            status=401)
    if not hmac.compare_digest(auth_header[7:], _API_KEY):
        return JsonResponse(
            {'error': {'message': 'Invalid API key', 'type': 'authentication_error'}},
            status=401)
    return None
```

- [ ] **Step 4: Wire settings + fail-closed in prod**

In `Main/backend/django_config/settings.py` (base), add: `FINGPT_API_KEY = os.getenv('FINGPT_API_KEY', '')` and `REQUIRE_FINGPT_API_KEY = False`.

In `Main/backend/django_config/settings_prod.py`, after the SECRET_KEY block (~line 59), add:

```python
FINGPT_API_KEY = os.getenv('FINGPT_API_KEY')
REQUIRE_FINGPT_API_KEY = True
if not FINGPT_API_KEY:
    raise ImproperlyConfigured(
        "FINGPT_API_KEY must be set in production (the /v1/* OpenAI-compatible "
        "API requires a Bearer key). Generate one and set it in .env.production."
    )
```

- [ ] **Step 5: gunicorn — sane threads + shorter default timeout**

In `Main/backend/gunicorn.conf.py`, change:

```python
threads = int(os.getenv('GUNICORN_THREADS', '4'))
timeout = int(os.getenv('GUNICORN_TIMEOUT', '120'))
```

(Add the `threads` line; edit the existing `timeout` default 600→120. Streaming SSE responses are sent incrementally, so 120s of inactivity is ample; the agent budget caps total load.)

- [ ] **Step 6: Run tests + prod-config check**

Run: `uv run python manage.py test tests.test_openai_auth_failclosed -v 2`
Expected: PASS.
Run (expect the guard to fire): `DJANGO_SETTINGS_MODULE=django_config.settings_prod uv run python -c "import django,os; os.environ.pop('FINGPT_API_KEY',None); django.setup()"`
Expected: raises `ImproperlyConfigured` mentioning FINGPT_API_KEY (proves fail-closed).

- [ ] **Step 7: Update env templates**

In `Main/backend/.env.production.example`, change `FINGPT_API_KEY=` to include a comment that it is REQUIRED, e.g. `FINGPT_API_KEY=  # REQUIRED in prod — generate a long random token`.

- [ ] **Step 8: Commit**

```bash
git add Main/backend/django_config/settings.py Main/backend/django_config/settings_prod.py Main/backend/api/openai_views.py Main/backend/gunicorn.conf.py Main/backend/.env.production.example Main/backend/tests/test_openai_auth_failclosed.py
git commit -m "fix(security): require FINGPT_API_KEY in prod (fail closed); gunicorn threads=4 timeout=120 (P0 Root C)"
```

---

## Final verification

- [ ] Run the full new suite: `cd Main/backend && uv run python manage.py test tests -v 2` → all green.
- [ ] `uv run python manage.py check` and `DJANGO_SETTINGS_MODULE=django_config.settings_prod ... check` (with required env set) → no issues.
- [ ] `grep -rn "key='ip'" Main/backend/api/` → no matches; `grep -n '"disabled": true' Main/backend/mcp_server_config.json` → filesystem block.
- [ ] Manual SSRF probe against a running container: `curl -X POST localhost:8000/api/auto_scrape/ -d '{"current_url":"http://169.254.169.254/"}'` → 400.
- [ ] Update the spec's acceptance checkboxes; mark queue tasks `finsearch-security-{llm-endpoint-01, ssrf-guard-02, spend-auth-03, cicd-deploygate-04}` complete via the central-db queue MCP.

## Self-review notes

- **Spec coverage:** P0 items A (T2–T4), B (T5–T6), C (T7–T10), F-gate (T1) all mapped. P1/P2/P3 (prompt-injection wrap, frontend XSS, session binding, CI hardening, hygiene) are intentionally **out of scope** for this plan — separate queue tasks/plans.
- **Auth seam:** delivered via `api/identity.py` (`get_request_identity`) used by both rate-limit key and as the documented per-user upgrade point for the budget — satisfies Felix's "leave room for a login system."
- **Cross-worker correctness:** budget/concurrency counters live in the shared cache (not in-process), so caps hold across gunicorn's 2 workers.
- **Known caveat to watch:** the SSE streaming slot release (T9 Step 3) must live in the generator's `finally`; if the client disconnects mid-stream, ensure `__exit__` still runs so concurrency counts don't leak (the existing SSE finalization block is the place).
