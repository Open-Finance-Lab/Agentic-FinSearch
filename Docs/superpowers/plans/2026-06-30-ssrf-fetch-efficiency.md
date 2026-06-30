# SSRF fetch-path efficiency + Playwright guard centralization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize the Playwright SSRF route-guard install into the browser factory, and make in-browser fetches cheaper by skipping non-text subresources and reusing per-host DNS-resolution + keep-alive connections — without weakening the IP-pinning that closes DNS rebinding.

**Architecture:** Two independent work items in `Main/backend/datascraper/`. Item 2 moves `install_route_guard` into the `PlaywrightBrowser` async factory so every page is born guarded. Item 1 adds (a) a resource-type filter that aborts `image/media/font` route requests and (b) a per-page `_PinnedSessionCache` that caches `{host -> (validated_ip, keep-alive session)}` with a short TTL; the route guards fetch through `cache.fetch` instead of building a fresh `requests.Session` per subresource. `safe_get`'s public one-shot signature is unchanged.

**Tech Stack:** Python 3.12, Django `SimpleTestCase`, `pytest`, `requests` + `urllib3` HTTPAdapter, Playwright (async + sync), `unittest.mock` (incl. `AsyncMock`).

**Spec:** `Docs/superpowers/specs/2026-06-30-ssrf-fetch-efficiency-design.md`

**Working dir for all commands:** `Main/backend/` (run `cd Main/backend` first; the `.venv` there has Django + pytest + playwright).

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `Main/backend/datascraper/playwright_tools.py` | Modify | Move guard install into `PlaywrightBrowser` factory; drop 3 redundant installs. |
| `Main/backend/datascraper/ssrf_guard.py` | Modify | Add `_PinnedSessionCache`, `_should_skip_resource`, `_validated_host`/`_resolve_and_pin`/`_follow_redirects` refactor; rewire route guards through the cache. |
| `Main/backend/api/views.py` | Modify (comment only) | One-line comment at line 941 explaining why the pre-check is intentionally kept. |
| `Main/backend/tests/test_ssrf_wire.py` | Modify | New `PlaywrightFactoryGuardTests` (factory installs guard). |
| `Main/backend/tests/test_ssrf_guard.py` | Modify | New `PinnedSessionCacheTests`; migrate route-guard tests to the cache seam; add resource-skip tests; add `import threading`/`import time`. |

**Commit policy:** Frequent TDD commits (one per task). The branch tells the story; squash to two logical commits (Item 2, Item 1) at merge if a tidy history is preferred.

---

### Task 1: Centralize the route-guard install in the `PlaywrightBrowser` factory (Item 2)

**Files:**
- Modify: `Main/backend/datascraper/playwright_tools.py` (factory ~48; remove installs at 89, 139, 246)
- Modify: `Main/backend/api/views.py:940` (add one comment line)
- Test: `Main/backend/tests/test_ssrf_wire.py` (new `PlaywrightFactoryGuardTests`)

- [ ] **Step 1: Write the failing test**

Add to the end of `Main/backend/tests/test_ssrf_wire.py`. First extend the import on line 10 from `from unittest.mock import MagicMock, patch` to include `AsyncMock`:

```python
from unittest.mock import AsyncMock, MagicMock, patch
```

Then append this class:

```python
class PlaywrightFactoryGuardTests(SimpleTestCase):
    """The PlaywrightBrowser factory must install the SSRF route guard on every
    page it yields, so all three async tools are guarded centrally and a future
    4th entrypoint cannot silently reopen the rebinding hole."""

    def test_factory_installs_route_guard_on_yielded_page(self):
        import datascraper.playwright_tools as pt

        page = MagicMock(name="page")
        context = MagicMock(name="context")
        context.new_page = AsyncMock(return_value=page)
        browser = MagicMock(name="browser")
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        playwright = MagicMock(name="playwright")
        playwright.chromium.launch = AsyncMock(return_value=browser)
        playwright.stop = AsyncMock()
        ap = MagicMock()
        ap.start = AsyncMock(return_value=playwright)

        async def run():
            with patch("playwright.async_api.async_playwright", return_value=ap), \
                 patch("datascraper.ssrf_guard.install_route_guard",
                       new=AsyncMock()) as mock_guard:
                async with pt.PlaywrightBrowser() as yielded:
                    # Installed on the yielded page BEFORE the caller's body runs
                    # (i.e. before any goto).
                    mock_guard.assert_awaited_once_with(page)
                    self.assertIs(yielded, page)

        asyncio.run(run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_wire.py::PlaywrightFactoryGuardTests -v`
Expected: FAIL — `mock_guard.assert_awaited_once_with(page)` raises because the factory does not yet install the guard (it's installed in the tools instead).

- [ ] **Step 3: Move the install into the factory**

In `Main/backend/datascraper/playwright_tools.py`, change the factory body so the guard is installed right after the page is created. Replace:

```python
        page = await context.new_page()

        yield page
```

with:

```python
        page = await context.new_page()
        # Every page is born guarded: install the SSRF route guard here so all
        # async entrypoints (and any future one) are pinned without each having
        # to remember the call. See datascraper.ssrf_guard.install_route_guard.
        await ssrf_guard.install_route_guard(page)

        yield page
```

- [ ] **Step 4: Remove the now-redundant per-tool installs**

In the same file, delete the line `await ssrf_guard.install_route_guard(page)` from all three tools. Each appears immediately after `async with PlaywrightBrowser() as page:`:

In `navigate_to_url`, change:
```python
        async with PlaywrightBrowser() as page:
            await ssrf_guard.install_route_guard(page)
            logger.info(f"Navigating to: {url}")
```
to:
```python
        async with PlaywrightBrowser() as page:
            logger.info(f"Navigating to: {url}")
```

In `click_element`, change:
```python
        async with PlaywrightBrowser() as page:
            await ssrf_guard.install_route_guard(page)
            logger.info(f"Navigating to {url} to click: {selector}")
```
to:
```python
        async with PlaywrightBrowser() as page:
            logger.info(f"Navigating to {url} to click: {selector}")
```

In `extract_page_content`, change:
```python
        async with PlaywrightBrowser() as page:
            await ssrf_guard.install_route_guard(page)
            logger.info(f"Extracting content from: {url}")
```
to:
```python
        async with PlaywrightBrowser() as page:
            logger.info(f"Extracting content from: {url}")
```

Leave the `ssrf_guard.validate_fetch_url(url)` seed pre-checks and the `ssrf_guard.assert_safe_page_url(page)` post-checks untouched in all three tools.

- [ ] **Step 5: Add the explanatory comment at `views.py:941`**

In `Main/backend/api/views.py`, in `auto_scrape`, change:
```python
        try:
            ssrf_guard.validate_fetch_url(current_url)
```
to:
```python
        try:
            # Intentional fail-fast (kept, not redundant): refuse a blocked URL
            # with a 400 before any scrape/session work. scrape_url validates
            # transitively too, but dropping this would surface the refusal as a
            # 500 and run session work first (pinned by test_ssrf_wire
            # AutoScrapeSinkTests). The openai_views url-init path differs — it
            # silently continues on a scrape error, so its precedent doesn't apply.
            ssrf_guard.validate_fetch_url(current_url)
```

- [ ] **Step 6: Run the factory test + the existing wiring tests to verify GREEN**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_wire.py -v`
Expected: PASS — the new `PlaywrightFactoryGuardTests` passes, and `PlaywrightNavigateSinkTests` / `PlaywrightFallbackRouteGuardTests` / `AutoScrapeSinkTests` still pass (seed validation, fail-fast, and 400 status unchanged).

- [ ] **Step 7: Commit**

```bash
cd Main/backend && git add datascraper/playwright_tools.py api/views.py tests/test_ssrf_wire.py
git commit -m "$(cat <<'EOF'
refactor(security): centralize Playwright route-guard install in the factory

Move install_route_guard into the PlaywrightBrowser async factory so every
page is born guarded; drop the 3 redundant per-tool installs. Keep the seed
validate_fetch_url pre-checks (test-pinned fail-fast before browser launch)
and views.py auto_scrape's pre-check (test-pinned 400; the openai_views
silent-continue precedent does not transfer). Adds a factory-install test.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Refactor the redirect loop into a shared helper (Item 1 prep)

Extract the redirect + byte-cap loop so `safe_get` and the upcoming cache share one copy of the security-critical per-hop re-validation. No behavior change; existing `SafeGetTests` must stay green.

**Files:**
- Modify: `Main/backend/datascraper/ssrf_guard.py`

- [ ] **Step 1: Confirm the existing safe_get tests pass (baseline)**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_guard.py::SafeGetTests tests/test_ssrf_guard.py::ValidateFetchUrlTests -v`
Expected: PASS (baseline before refactor).

- [ ] **Step 2: Add new imports**

In `Main/backend/datascraper/ssrf_guard.py`, change the stdlib import block. Replace:
```python
import asyncio
import ipaddress
import logging
import os
import socket
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse
```
with:
```python
import asyncio
import ipaddress
import logging
import os
import socket
import threading
import time
from http.cookiejar import DefaultCookiePolicy
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse
```

- [ ] **Step 3: Split `_check_and_resolve` into reusable helpers**

Replace the existing `_check_and_resolve` function:
```python
def _check_and_resolve(url: str) -> Tuple[str, str]:
    """Validate ``url`` and resolve it with a SINGLE DNS lookup, returning
    ``(host, pinned_ip)``. Enforces http/https scheme, a present host, and that
    EVERY resolved IP is publicly routable. ``pinned_ip`` is one of the IPs that
    just passed the block-check, so safe_get can connect to exactly that address
    with no second, rebind-vulnerable lookup."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Blocked scheme {parsed.scheme!r} in {url!r} (only http/https allowed)"
        )
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"Missing host in URL {url!r}")
    ips = _resolve_ips(host)
    for ip in ips:
        if _is_blocked_ip(ip):
            raise UnsafeURLError(
                f"Blocked host {host!r}: resolves to non-routable IP {ip}"
            )
    return host, ips[0]
```
with:
```python
def _validated_host(url: str) -> str:
    """Enforce http/https scheme and a present host on ``url`` and return the
    host. Raises :class:`UnsafeURLError`. DNS resolution + block-check is a
    separate step (see :func:`_resolve_and_pin`)."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Blocked scheme {parsed.scheme!r} in {url!r} (only http/https allowed)"
        )
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"Missing host in URL {url!r}")
    return host


def _resolve_and_pin(host: str) -> str:
    """Resolve ``host`` with a SINGLE DNS lookup and return its first IP after
    verifying EVERY resolved IP is publicly routable. Raises
    :class:`UnsafeURLError` otherwise. The returned IP just passed the
    block-check, so a caller can pin the connection to exactly that address with
    no second, rebind-vulnerable lookup."""
    ips = _resolve_ips(host)
    for ip in ips:
        if _is_blocked_ip(ip):
            raise UnsafeURLError(
                f"Blocked host {host!r}: resolves to non-routable IP {ip}"
            )
    return ips[0]


def _check_and_resolve(url: str) -> Tuple[str, str]:
    """Validate ``url`` and resolve it with a SINGLE DNS lookup, returning
    ``(host, pinned_ip)``."""
    host = _validated_host(url)
    return host, _resolve_and_pin(host)
```

- [ ] **Step 4: Extract `_follow_redirects` and rewrite `safe_get` to use it**

Replace the existing `safe_get` function:
```python
def safe_get(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 15,
    max_bytes: int = MAX_FETCH_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> requests.Response:
    """SSRF-safe drop-in for ``requests.get`` used by auto_scrape.

    Validates + resolves the URL, pins the TCP connection to the validated IP
    (original Host preserved), follows at most ``max_redirects`` hops while
    RE-VALIDATING each ``Location`` BEFORE it is fetched, streams the body, and
    aborts once Content-Length or cumulative bytes exceed ``max_bytes``. Returns
    the final :class:`requests.Response` with a bounded, buffered body."""
    current = url
    for _ in range(max_redirects + 1):
        _host, ip = _check_and_resolve(current)
        response = _pinned_fetch(current, ip, headers, timeout)
        location = response.headers.get("Location")
        if response.status_code in _REDIRECT_STATUSES and location:
            response.close()
            current = urljoin(current, location)
            continue
        return _enforce_byte_cap(response, max_bytes)
    raise UnsafeURLError(
        f"Exceeded maximum of {max_redirects} redirects starting from {url!r}"
    )
```
with:
```python
def _follow_redirects(url, fetch_one, max_bytes, max_redirects):
    """Drive the redirect + byte-cap loop shared by :func:`safe_get` and
    :meth:`_PinnedSessionCache.fetch`. ``fetch_one(current_url)`` MUST return a
    streaming, non-redirecting response whose connection is pinned to a
    freshly-validated public IP for ``current_url``'s host — that per-hop
    re-validation is what makes following a ``Location`` header safe. Returns the
    final :class:`requests.Response` with a bounded, buffered body."""
    current = url
    for _ in range(max_redirects + 1):
        response = fetch_one(current)
        location = response.headers.get("Location")
        if response.status_code in _REDIRECT_STATUSES and location:
            response.close()
            current = urljoin(current, location)
            continue
        return _enforce_byte_cap(response, max_bytes)
    raise UnsafeURLError(
        f"Exceeded maximum of {max_redirects} redirects starting from {url!r}"
    )


def safe_get(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 15,
    max_bytes: int = MAX_FETCH_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> requests.Response:
    """SSRF-safe drop-in for ``requests.get`` used by auto_scrape.

    Validates + resolves the URL, pins the TCP connection to the validated IP
    (original Host preserved), follows at most ``max_redirects`` hops while
    RE-VALIDATING each ``Location`` BEFORE it is fetched, streams the body, and
    aborts once Content-Length or cumulative bytes exceed ``max_bytes``. Returns
    the final :class:`requests.Response` with a bounded, buffered body. Stateless:
    each hop builds a fresh pinned session (see ``_PinnedSessionCache`` for the
    keep-alive variant used by the in-browser route guards)."""
    def _fetch_one(current):
        _host, ip = _check_and_resolve(current)
        return _pinned_fetch(current, ip, headers, timeout)
    return _follow_redirects(url, _fetch_one, max_bytes, max_redirects)
```

- [ ] **Step 5: Run the safe_get + validate tests to verify still GREEN**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_guard.py::SafeGetTests tests/test_ssrf_guard.py::ValidateFetchUrlTests -v`
Expected: PASS — behavior is identical; the loop and per-hop `_check_and_resolve` are unchanged, just relocated.

- [ ] **Step 6: Commit**

```bash
cd Main/backend && git add datascraper/ssrf_guard.py
git commit -m "$(cat <<'EOF'
refactor(security): extract _follow_redirects + DNS-resolve helpers in ssrf_guard

Split _check_and_resolve into _validated_host (scheme/host) + _resolve_and_pin
(DNS + block-check), and lift safe_get's redirect/byte-cap loop into
_follow_redirects, so the upcoming per-page session cache reuses the exact
per-hop re-validation. No behavior change.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add the per-page `_PinnedSessionCache` (Item 1b)

A per-page cache of validated hosts → keep-alive IP-pinned sessions, thread-safe for the async guard's `asyncio.to_thread` dispatch. Not wired into the guards yet — unit-tested in isolation first.

**Files:**
- Modify: `Main/backend/datascraper/ssrf_guard.py` (constants + class)
- Test: `Main/backend/tests/test_ssrf_guard.py` (new `PinnedSessionCacheTests`)

- [ ] **Step 1: Write the failing tests**

In `Main/backend/tests/test_ssrf_guard.py`, extend the imports at the top. Change:
```python
import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch
```
to:
```python
import asyncio
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch
```

Then append this class to the file:

```python
class PinnedSessionCacheTests(SimpleTestCase):
    """The per-page cache reuses validated DNS + keep-alive sessions per host,
    re-validates after TTL, and never reaches a private address."""

    def test_resolves_once_and_reuses_session_within_ttl(self):
        cache = ssrf_guard._PinnedSessionCache(ttl=1000)
        with patch("datascraper.ssrf_guard._resolve_ips",
                   return_value=["93.184.216.34"]) as m_res:
            s1 = cache._session_for("example.com")
            s2 = cache._session_for("example.com")
        self.assertIs(s1, s2)
        m_res.assert_called_once()
        cache.close()

    def test_reresolves_and_reblocks_after_ttl(self):
        cache = ssrf_guard._PinnedSessionCache(ttl=30)
        clock = {"t": 100.0}
        with patch("datascraper.ssrf_guard.time.monotonic",
                   side_effect=lambda: clock["t"]), \
             patch("datascraper.ssrf_guard._resolve_ips",
                   side_effect=[["93.184.216.34"], ["127.0.0.1"]]) as m_res:
            cache._session_for("rebind.test")     # caches; expiry = 130
            clock["t"] = 200.0                     # past the 30s TTL
            with self.assertRaises(UnsafeURLError):
                cache._session_for("rebind.test")  # re-resolve -> now private -> blocked
        self.assertEqual(m_res.call_count, 2)
        cache.close()

    def test_blocked_ip_on_miss_caches_nothing(self):
        cache = ssrf_guard._PinnedSessionCache()
        with patch("datascraper.ssrf_guard._resolve_ips",
                   return_value=["169.254.169.254"]):
            with self.assertRaises(UnsafeURLError):
                cache._session_for("evil.test")
        self.assertEqual(cache._entries, {})

    def test_fetch_revalidates_each_redirect_host(self):
        cache = ssrf_guard._PinnedSessionCache()
        redirect = _FakeResp(status_code=302, location="http://h2.test/final")
        final = _FakeResp(status_code=200, chunks=[b"ok"])
        seen = []

        def fake_session_for(host):
            seen.append(host)
            s = MagicMock()
            s.get.return_value = redirect if host == "h1.test" else final
            return s

        with patch.object(cache, "_session_for", side_effect=fake_session_for):
            resp = cache.fetch("http://h1.test/start")
        self.assertEqual(seen, ["h1.test", "h2.test"])
        self.assertEqual(resp._content, b"ok")
        self.assertTrue(redirect.closed)

    def test_concurrent_same_host_resolves_once(self):
        cache = ssrf_guard._PinnedSessionCache(ttl=1000)

        def slow_resolve(host):
            time.sleep(0.02)
            return ["93.184.216.34"]

        with patch("datascraper.ssrf_guard._resolve_ips",
                   side_effect=slow_resolve) as m_res:
            threads = [threading.Thread(target=cache._session_for,
                                        args=("example.com",)) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        m_res.assert_called_once()
        cache.close()

    def test_close_closes_all_sessions(self):
        cache = ssrf_guard._PinnedSessionCache(ttl=1000)
        fake = MagicMock()
        with patch.object(ssrf_guard._PinnedSessionCache, "_build_session",
                          return_value=fake), \
             patch("datascraper.ssrf_guard._resolve_ips",
                   return_value=["93.184.216.34"]):
            cache._session_for("example.com")
        cache.close()
        fake.close.assert_called_once()
        self.assertEqual(cache._entries, {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_guard.py::PinnedSessionCacheTests -v`
Expected: FAIL — `AttributeError: module 'datascraper.ssrf_guard' has no attribute '_PinnedSessionCache'`.

- [ ] **Step 3: Add the cache constants**

In `Main/backend/datascraper/ssrf_guard.py`, after the `MAX_REDIRECTS` line (the `_ALLOWED_SCHEMES`/`_REDIRECT_STATUSES`/`_STREAM_CHUNK_BYTES` block), add:

```python
# In-browser subresource types the route guard aborts outright: they never feed
# page.inner_text, so fetching them only burns DNS+TLS and egress. Deliberately
# NOT stylesheet/script/xhr/fetch/document — dropping CSS can leak display:none
# boilerplate into inner_text, and JS/XHR drive SPA rendering.
_SKIP_RESOURCE_TYPES = frozenset(
    t.strip()
    for t in os.getenv("SCRAPE_SKIP_RESOURCE_TYPES", "image,media,font").split(",")
    if t.strip()
)
# Seconds a per-page resolved+validated host entry stays usable before it must be
# re-resolved and re-block-checked. Per-page cache scope already bounds reuse to a
# single scrape; this is a defense-in-depth staleness ceiling.
_DNS_CACHE_TTL = float(os.getenv("SCRAPE_DNS_CACHE_TTL", "30"))
# Keep-alive pool size for each cached per-host pinned session, so a same-host
# subresource burst reuses connections instead of serializing on one.
_POOL_MAXSIZE = int(os.getenv("SCRAPE_POOL_MAXSIZE", "20"))
```

- [ ] **Step 4: Add the `_PinnedSessionCache` class**

In `Main/backend/datascraper/ssrf_guard.py`, add this class immediately after the `safe_get` function (and before `_fulfill_headers`):

```python
class _PinnedSessionCache:
    """Per-page cache of resolved+validated hosts and their keep-alive,
    IP-pinned ``requests.Session``s, used by the Playwright route guards to skip
    re-resolving DNS and re-handshaking TLS on every subresource.

    A cached entry stores ONLY an IP that already passed the block-check, and the
    session stays pinned to that IP, so reuse cannot reach a private address even
    if the host later rebinds — re-resolution (and the next block-check) only
    happens after the entry's TTL expires. Per page, NEVER global. Thread-safe:
    the async route guard dispatches :meth:`fetch` via ``asyncio.to_thread``, so
    concurrent same-host subresources may call it at once."""

    def __init__(self, ttl: float = _DNS_CACHE_TTL):
        self._ttl = ttl
        self._lock = threading.Lock()
        # host -> (pinned_ip, session, expiry_monotonic)
        self._entries = {}

    @staticmethod
    def _build_session(ip: str, host: str) -> requests.Session:
        """A keep-alive Session whose adapter pins every connection to ``ip``
        (Host/SNI preserved for ``host``). Its cookie jar is disabled: cookies
        flow through Chromium (fulfilled Set-Cookie -> stored -> replayed via the
        forwarded Cookie header), never through this session, so disabling it both
        removes the only per-request mutable shared state (making concurrent
        ``session.get`` thread-safe) and prevents cross-subresource cookie bleed."""
        session = requests.Session()
        session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        adapter = _PinnedHTTPAdapter(
            ip, host, pool_connections=_POOL_MAXSIZE, pool_maxsize=_POOL_MAXSIZE
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _session_for(self, host: str) -> requests.Session:
        """Return a keep-alive Session pinned to a freshly-validated public IP for
        ``host``, resolving + block-checking on a cache miss or after TTL expiry.
        Holds the lock across the whole get-or-create so a concurrent same-host
        burst resolves exactly once."""
        with self._lock:
            entry = self._entries.get(host)
            if entry is not None and time.monotonic() < entry[2]:
                return entry[1]
            # Miss or expired: resolve + block-check BEFORE mutating the cache, so
            # a now-blocked host raises without evicting/replacing a good entry.
            pinned_ip = _resolve_and_pin(host)
            if entry is not None:
                entry[1].close()
            session = self._build_session(pinned_ip, host)
            self._entries[host] = (pinned_ip, session, time.monotonic() + self._ttl)
            return session

    def fetch(
        self,
        url: str,
        headers: Optional[dict] = None,
        timeout: int = 15,
        max_bytes: int = MAX_FETCH_BYTES,
        max_redirects: int = MAX_REDIRECTS,
    ) -> requests.Response:
        """:func:`safe_get`'s redirect + byte-cap contract, but each hop reuses
        the host's pinned keep-alive session. Validates scheme + host per hop and
        resolves/block-checks per host (cache miss or expiry)."""
        def _fetch_one(current):
            host = _validated_host(current)
            session = self._session_for(host)
            return session.get(
                current,
                headers=headers,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
        return _follow_redirects(url, _fetch_one, max_bytes, max_redirects)

    def close(self) -> None:
        """Close every cached session (its keep-alive sockets). Called when the
        page closes."""
        with self._lock:
            for _ip, session, _exp in self._entries.values():
                try:
                    session.close()
                except Exception:
                    pass
            self._entries.clear()
```

- [ ] **Step 5: Run the cache tests to verify GREEN**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_guard.py::PinnedSessionCacheTests -v`
Expected: PASS — all 6 cache tests pass.

- [ ] **Step 6: Commit**

```bash
cd Main/backend && git add datascraper/ssrf_guard.py tests/test_ssrf_guard.py
git commit -m "$(cat <<'EOF'
perf(security): add per-page pinned-session cache for in-browser fetches

_PinnedSessionCache caches {host -> (validated_ip, keep-alive session)} per page
with a short TTL, reusing DNS resolution + TCP/TLS across same-host subresources.
Validated-IP pinning, per-hop re-validation, and full block-check on every
(re)resolve are preserved; cookie jar disabled for thread-safety. Not yet wired
into the route guards.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wire the route guards through the cache + skip non-text resources (Item 1a + 1b)

Rewrite both route-guard handlers to (1) abort `image/media/font` resource types and (2) fetch through a per-page `_PinnedSessionCache` instead of module-level `safe_get`. Migrate the existing route-guard tests to the cache seam and add resource-skip tests.

**Files:**
- Modify: `Main/backend/datascraper/ssrf_guard.py` (`_should_skip_resource`, `_proxied_response_kwargs`, both `install_route_guard*`)
- Test: `Main/backend/tests/test_ssrf_guard.py` (`RouteGuardTests`, `SyncRouteGuardTests`)

- [ ] **Step 1: Migrate the existing route-guard tests to the cache seam + add skip tests (RED)**

The guards will fetch via `_PinnedSessionCache.fetch` instead of `safe_get`, so the tests patch the cache class and alias its `.fetch` to the existing `m_get` variable (every assertion body stays unchanged). Also set an explicit `resource_type` on the route helper and add a skip test.

In `Main/backend/tests/test_ssrf_guard.py`, in **`RouteGuardTests`**, add `resource_type` to the `_route` helper. Change:
```python
    def _route(self, url, method="GET"):
        route = MagicMock()
        route.request.url = url
        route.request.method = method
        route.request.headers = dict(self._BROWSER_HEADERS)
        route.abort = AsyncMock()
        route.fulfill = AsyncMock()
        route.continue_ = AsyncMock()
        return route
```
to:
```python
    def _route(self, url, method="GET", resource_type="document"):
        route = MagicMock()
        route.request.url = url
        route.request.method = method
        route.request.resource_type = resource_type
        route.request.headers = dict(self._BROWSER_HEADERS)
        route.abort = AsyncMock()
        route.fulfill = AsyncMock()
        route.continue_ = AsyncMock()
        return route
```

Then change each of the four test method decorators + signatures from patching `safe_get` to patching `_PinnedSessionCache`, adding the alias as the first line. Specifically:

`test_route_guard_aborts_blocked_request` — change:
```python
    @patch("datascraper.ssrf_guard.safe_get")
    def test_route_guard_aborts_blocked_request(self, m_get):
        m_get.side_effect = UnsafeURLError("blocked")
```
to:
```python
    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_aborts_blocked_request(self, m_cache):
        m_get = m_cache.return_value.fetch
        m_get.side_effect = UnsafeURLError("blocked")
```

`test_route_guard_fulfills_public_request_from_pinned_fetch` — change:
```python
    @patch("datascraper.ssrf_guard.safe_get")
    def test_route_guard_fulfills_public_request_from_pinned_fetch(self, m_get):
        resp = _FakeResp(
```
to:
```python
    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_fulfills_public_request_from_pinned_fetch(self, m_cache):
        m_get = m_cache.return_value.fetch
        resp = _FakeResp(
```

`test_route_guard_forwards_browser_headers_to_pinned_fetch` — change:
```python
    @patch("datascraper.ssrf_guard.safe_get")
    def test_route_guard_forwards_browser_headers_to_pinned_fetch(self, m_get):
        # The pinned fetch must present the browser's own User-Agent (the
```
to:
```python
    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_forwards_browser_headers_to_pinned_fetch(self, m_cache):
        m_get = m_cache.return_value.fetch
        # The pinned fetch must present the browser's own User-Agent (the
```

`test_route_guard_aborts_non_get` — change:
```python
    @patch("datascraper.ssrf_guard.safe_get")
    def test_route_guard_aborts_non_get(self, m_get):
        # safe_get is GET-only; non-GET in-browser requests fail closed.
```
to:
```python
    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_aborts_non_get(self, m_cache):
        m_get = m_cache.return_value.fetch
        # cache.fetch is GET-only; non-GET in-browser requests fail closed.
```

Then add this new skip test inside `RouteGuardTests`:
```python
    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_aborts_skipped_resource(self, m_cache):
        # image/media/font are aborted before any fetch — they don't feed
        # inner_text, so we never spend DNS+TLS or egress on them.
        route = self._route("http://example.com/logo.png", resource_type="image")
        self._drive(route)
        route.abort.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        m_cache.return_value.fetch.assert_not_called()
```

Now in **`SyncRouteGuardTests`**, add `resource_type` to its `_route` helper. Change:
```python
    def _route(self, url, method="GET"):
        route = MagicMock()
        route.request.url = url
        route.request.method = method
        route.request.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "example.com",
        }
        return route
```
to:
```python
    def _route(self, url, method="GET", resource_type="document"):
        route = MagicMock()
        route.request.url = url
        route.request.method = method
        route.request.resource_type = resource_type
        route.request.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "example.com",
        }
        return route
```

Then migrate its four tests the same way (decorator → `_PinnedSessionCache`, param → `m_cache`, add `m_get = m_cache.return_value.fetch` as the first line):

- `test_sync_guard_aborts_blocked_request`: decorator `@patch("datascraper.ssrf_guard._PinnedSessionCache")`, signature `(self, m_cache)`, first body line `m_get = m_cache.return_value.fetch`, then keep `m_get.side_effect = UnsafeURLError("blocked")`.
- `test_sync_guard_fulfills_public_request`: same decorator/param, first line `m_get = m_cache.return_value.fetch`, then keep `resp = _FakeResp(...)` etc.
- `test_sync_guard_forwards_browser_user_agent`: same decorator/param, first line `m_get = m_cache.return_value.fetch`.
- `test_sync_guard_aborts_non_get`: same decorator/param, first line `m_get = m_cache.return_value.fetch`.

And add the sync skip test inside `SyncRouteGuardTests`:
```python
    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_sync_guard_aborts_skipped_resource(self, m_cache):
        route = self._route("http://example.com/font.woff2", resource_type="font")
        self._drive(route)
        route.abort.assert_called_once()
        route.fulfill.assert_not_called()
        m_cache.return_value.fetch.assert_not_called()
```

- [ ] **Step 2: Run the route-guard tests to verify they fail**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_guard.py::RouteGuardTests tests/test_ssrf_guard.py::SyncRouteGuardTests -v`
Expected: FAIL — the guards still call `safe_get`, so `m_cache.return_value.fetch` is never called (fulfill/abort assertions and the new skip tests fail).

- [ ] **Step 3: Add `_should_skip_resource`**

In `Main/backend/datascraper/ssrf_guard.py`, add this function next to `_should_proxy`:

```python
def _should_skip_resource(route) -> bool:
    """True for in-browser subresource types we never need for text extraction
    (image/media/font by default — see ``_SKIP_RESOURCE_TYPES``). Aborting them
    cuts DNS+TLS work and egress with no effect on ``page.inner_text``; aborting
    more requests is also strictly less egress, so there is no SSRF downside."""
    try:
        return route.request.resource_type in _SKIP_RESOURCE_TYPES
    except Exception:
        return False
```

- [ ] **Step 4: Route `_proxied_response_kwargs` through the cache**

Replace the existing `_proxied_response_kwargs` function signature and body. Change:
```python
def _proxied_response_kwargs(request_url: str, headers: dict):
    """Fetch an intercepted in-browser GET through the IP-pinned, byte-capped
    ``safe_get`` and return the kwargs to fulfill the Playwright route with, or
    ``None`` to fail closed (abort). Blocking — the async guard dispatches it via
    ``asyncio.to_thread``. Both route guards share this one fail-closed policy
    (which exceptions abort, what gets logged, which headers fulfill) so the sync
    and async paths cannot drift. ``headers`` are the browser's own request
    headers (UA/Accept/...) so the IP-pinned fetch stays indistinguishable to the
    origin."""
    try:
        # safe_get validates, pins to the resolved IP, follows redirects
        # re-validating each, and byte-caps the body — raising UnsafeURLError on
        # any violation.
        response = safe_get(request_url, headers=headers)
    except UnsafeURLError as exc:
        logger.warning(
            "[ssrf_guard] aborting in-browser request to %s: %s",
            request_url,
            exc,
        )
        return None
    except Exception as exc:  # network error, timeout, etc. — fail closed.
        logger.warning(
            "[ssrf_guard] fetch failed for in-browser request %s: %s",
            request_url,
            exc,
        )
        return None
    return {
        "status": response.status_code,
        "headers": _fulfill_headers(response),
        "body": response.content,
    }
```
to:
```python
def _proxied_response_kwargs(cache: "_PinnedSessionCache", request_url: str, headers: dict):
    """Fetch an intercepted in-browser GET through the per-page IP-pinned,
    byte-capped ``cache`` and return the kwargs to fulfill the Playwright route
    with, or ``None`` to fail closed (abort). Blocking — the async guard
    dispatches it via ``asyncio.to_thread``. Both route guards share this one
    fail-closed policy (which exceptions abort, what gets logged, which headers
    fulfill) so the sync and async paths cannot drift. ``headers`` are the
    browser's own request headers (UA/Accept/...) so the IP-pinned fetch stays
    indistinguishable to the origin."""
    try:
        # cache.fetch validates, pins to the resolved IP (reusing a per-host
        # keep-alive session), follows redirects re-validating each, and byte-caps
        # the body — raising UnsafeURLError on any violation.
        response = cache.fetch(request_url, headers=headers)
    except UnsafeURLError as exc:
        logger.warning(
            "[ssrf_guard] aborting in-browser request to %s: %s",
            request_url,
            exc,
        )
        return None
    except Exception as exc:  # network error, timeout, etc. — fail closed.
        logger.warning(
            "[ssrf_guard] fetch failed for in-browser request %s: %s",
            request_url,
            exc,
        )
        return None
    return {
        "status": response.status_code,
        "headers": _fulfill_headers(response),
        "body": response.content,
    }
```

- [ ] **Step 5: Rewrite both `install_route_guard*` to create a cache + skip resources**

Replace the existing `install_route_guard` async function body. Change:
```python
async def install_route_guard(page) -> None:
    """Register an async Playwright route handler on ALL URLs that fetches each
    intercepted in-browser request (top-level navigation OR subresource) through
    the IP-pinned, byte-capped ``safe_get`` and fulfills Chromium with the
    buffered response. For these HTTP(S) requests Chromium therefore never opens
    its own socket and cannot be redirected to a private address by a
    DNS-rebinding answer.

    MUST be called BEFORE the first ``page.goto`` in every Playwright entrypoint
    so EVERY navigation/subresource is pinned, not just the seed URL. Non-GET and
    non-http(s) requests fail closed (aborted). WebSocket/WebRTC are not routed
    through ``page.route`` and are out of scope (see module docstring)."""

    async def _handler(route):
        if not _should_proxy(route):
            await route.abort()
            return
        # _proxied_response_kwargs is blocking (it calls safe_get); run it off the
        # event loop. route.request.* is read here on the loop before dispatch.
        kwargs = await asyncio.to_thread(
            _proxied_response_kwargs, route.request.url, _forward_headers(route)
        )
        if kwargs is None:  # fetch was unsafe or failed — fail closed.
            await route.abort()
            return
        await route.fulfill(**kwargs)

    await page.route("**/*", _handler)
```
to:
```python
async def install_route_guard(page) -> None:
    """Register an async Playwright route handler on ALL URLs that fetches each
    intercepted in-browser request (top-level navigation OR subresource) through
    a per-page IP-pinned, byte-capped, keep-alive cache and fulfills Chromium with
    the buffered response. For these HTTP(S) requests Chromium therefore never
    opens its own socket and cannot be redirected to a private address by a
    DNS-rebinding answer. image/media/font requests are aborted outright (they
    never feed text extraction); non-GET and non-http(s) requests fail closed.

    MUST be called BEFORE the first ``page.goto`` so EVERY navigation/subresource
    is pinned, not just the seed URL. Installed centrally by the PlaywrightBrowser
    factory. WebSocket/WebRTC are not routed through ``page.route`` and are out of
    scope (see module docstring)."""
    cache = _PinnedSessionCache()
    page.on("close", lambda *_: cache.close())

    async def _handler(route):
        if _should_skip_resource(route):
            await route.abort()
            return
        if not _should_proxy(route):
            await route.abort()
            return
        # _proxied_response_kwargs is blocking (it calls cache.fetch); run it off
        # the event loop. route.request.* is read here on the loop before dispatch.
        kwargs = await asyncio.to_thread(
            _proxied_response_kwargs, cache, route.request.url, _forward_headers(route)
        )
        if kwargs is None:  # fetch was unsafe or failed — fail closed.
            await route.abort()
            return
        await route.fulfill(**kwargs)

    await page.route("**/*", _handler)
```

Replace the existing `install_route_guard_sync` function body. Change:
```python
def install_route_guard_sync(page) -> None:
    """Synchronous twin of :func:`install_route_guard` for the sync Playwright
    fallback (``url_tools.scrape_with_playwright``). Same guarantee: every
    in-browser GET is fulfilled from the IP-pinned ``safe_get`` so Chromium never
    re-resolves DNS on its own socket; non-GET / non-http(s) fail closed.

    MUST be called BEFORE the first ``page.goto``."""

    def _handler(route):
        if not _should_proxy(route):
            route.abort()
            return
        kwargs = _proxied_response_kwargs(route.request.url, _forward_headers(route))
        if kwargs is None:  # fetch was unsafe or failed — fail closed.
            route.abort()
            return
        route.fulfill(**kwargs)

    page.route("**/*", _handler)
```
to:
```python
def install_route_guard_sync(page) -> None:
    """Synchronous twin of :func:`install_route_guard` for the sync Playwright
    fallback (``url_tools.scrape_with_playwright``). Same guarantees: every
    in-browser GET is fulfilled from a per-page IP-pinned, keep-alive cache so
    Chromium never re-resolves DNS on its own socket; image/media/font are
    aborted outright; non-GET / non-http(s) fail closed.

    MUST be called BEFORE the first ``page.goto``."""
    cache = _PinnedSessionCache()
    page.on("close", lambda *_: cache.close())

    def _handler(route):
        if _should_skip_resource(route):
            route.abort()
            return
        if not _should_proxy(route):
            route.abort()
            return
        kwargs = _proxied_response_kwargs(cache, route.request.url, _forward_headers(route))
        if kwargs is None:  # fetch was unsafe or failed — fail closed.
            route.abort()
            return
        route.fulfill(**kwargs)

    page.route("**/*", _handler)
```

- [ ] **Step 6: Run the route-guard tests to verify GREEN**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_guard.py::RouteGuardTests tests/test_ssrf_guard.py::SyncRouteGuardTests -v`
Expected: PASS — all migrated tests (abort-blocked, fulfill-public, forward-headers, abort-non-GET) plus the two new skip tests pass.

- [ ] **Step 7: Commit**

```bash
cd Main/backend && git add datascraper/ssrf_guard.py tests/test_ssrf_guard.py
git commit -m "$(cat <<'EOF'
perf(security): skip non-text subresources + fetch via per-page cache in guards

Route guards now abort image/media/font requests (never needed for inner_text)
and fetch the rest through the per-page _PinnedSessionCache (DNS + keep-alive)
instead of building a fresh session per subresource. Per-hop re-validation and
IP-pinning unchanged. Route-guard tests migrated to the cache seam; skip tests
added.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Full-suite regression + lint sanity

**Files:** none (verification only)

- [ ] **Step 1: Run the full SSRF + wiring test modules**

Run: `cd Main/backend && .venv/bin/python -m pytest tests/test_ssrf_guard.py tests/test_ssrf_wire.py -v`
Expected: PASS — all route-guard, cache, factory, and wiring tests green.

- [ ] **Step 2: Run the entire backend suite (regression vs the PR #314 baseline)**

Run: `cd Main/backend && .venv/bin/python -m pytest -q`
Expected: PASS — no regressions (PR #314 baseline was 459 passed; this should be that plus the new tests).

- [ ] **Step 3: Quick import/smoke check of the edited modules**

Run: `cd Main/backend && .venv/bin/python -c "import datascraper.ssrf_guard, datascraper.playwright_tools, datascraper.url_tools, api.views; print('imports ok')"`
Expected: `imports ok` (no syntax/typo errors in the edited files).

- [ ] **Step 4: Confirm the branch history**

Run: `git log --oneline main..HEAD`
Expected: the spec commit plus the four task commits (Task 1–4), newest first.

---

## Self-Review

**1. Spec coverage**
- Item 2 (centralize install) → Task 1. ✓
- Item 2 "keep `views.py:941`" → Task 1 Step 5 (comment). ✓
- Item 2 factory-install test → Task 1 Step 1. ✓
- Item 1 Lever A (skip image/media/font) → Task 3 (constant) + Task 4 (`_should_skip_resource`, handler, tests). ✓
- Item 1 Lever B (per-page per-host pinned-session cache, TTL, thread-safety, close) → Task 3. ✓
- Item 1 shared `_follow_redirects` refactor + helper split → Task 2. ✓
- Item 1 route-guard rewiring (`_proxied_response_kwargs` cache param) → Task 4. ✓
- Security tests (rebind-after-TTL refusal, blocked-on-miss caches nothing, per-hop redirect re-validation, concurrency) → Task 3 Step 1. ✓
- Regression (full suite green) → Task 5. ✓
- Out-of-scope items (WebSocket/WebRTC, `safe_get` signature, global cache) — untouched; `safe_get` signature preserved in Task 2 Step 4. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output. ✓

**3. Type/name consistency:** `_PinnedSessionCache` (Task 3) is the patch target and constructor used in Task 4 tests and `install_route_guard*`. `.fetch(url, headers=...)`, `._session_for(host)`, `._build_session(ip, host)`, `._entries`, `.close()` are used consistently across Tasks 3–4. `_should_skip_resource`/`_should_proxy`/`_proxied_response_kwargs(cache, url, headers)`/`_validated_host`/`_resolve_and_pin`/`_follow_redirects` names match between definition and call sites. Env knobs `SCRAPE_SKIP_RESOURCE_TYPES`/`SCRAPE_DNS_CACHE_TTL`/`SCRAPE_POOL_MAXSIZE` defined once in Task 3 Step 3. ✓
