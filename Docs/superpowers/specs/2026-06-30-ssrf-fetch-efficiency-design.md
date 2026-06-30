# SSRF fetch-path efficiency + Playwright guard centralization

**Date:** 2026-06-30
**Status:** Approved (design)
**Branch base:** `main` (after PR #314 merge, `f420c28`)
**Touches:** `Main/backend/datascraper/ssrf_guard.py`, `Main/backend/datascraper/playwright_tools.py`, `Main/backend/api/views.py` (comment only), plus tests.

These are the two follow-ups deferred from PR #314 (security-audit remediation). They are independent of each other but live in the same subsystem (the SSRF guard + Playwright entrypoints), so they ship on one branch as two commits.

## Background

PR #314 closed an in-browser DNS-rebinding hole by routing **every** Chromium request (top-level navigation *and* every subresource) through `ssrf_guard.safe_get`: the route guard fetches each request via an IP-pinned, byte-capped `requests` call and `route.fulfill`s the buffered response, so Chromium never opens its own socket for the HTTP(S) requests `page.route` intercepts. See module docstring in `ssrf_guard.py`.

Two costs were knowingly deferred:

1. **Fetch-path efficiency.** The guard fulfills *every* subresource through `safe_get`, and `safe_get` builds a fresh `requests.Session` (no keep-alive), re-resolves DNS, and re-parses the URL on every call. A media-heavy page therefore pays one `getaddrinfo` + one TCP/TLS handshake per asset — including assets the scrape never reads.
2. **Guard-install altitude.** `install_route_guard` is hand-wired at every async entrypoint. A future 4th entrypoint that forgets the call silently reopens the rebinding hole.

## Item 2 — Centralize the route-guard install

*(Mechanical; implement first so it lands as a clean standalone commit.)*

### Change

In `playwright_tools.py`, move the guard install into the `PlaywrightBrowser` async context-manager factory, immediately after `page = await context.new_page()` and before `yield page`:

```python
page = await context.new_page()
await ssrf_guard.install_route_guard(page)   # every page born guarded
yield page
```

Delete the three now-redundant `await ssrf_guard.install_route_guard(page)` calls inside `navigate_to_url`, `click_element`, and `extract_page_content`.

### Deliberately unchanged

- **Seed `validate_fetch_url(url)` pre-checks** in the three async tools. They are test-pinned by `PlaywrightNavigateSinkTests` to refuse a blocked seed *before the browser launches* (fail-fast, no Chromium process spun up). The factory-level guard would only abort the seed *after* launch, so these stay.
- **`assert_safe_page_url` post-checks** — still needed (JS/meta redirects can move the URL after `goto`).
- **Sync path** (`url_tools.scrape_with_playwright`) — it builds its own browser, not via the factory, and already installs `install_route_guard_sync` inline (test-pinned to precede `goto`). Out of scope for the factory move; the cache work in Item 1 still applies to it.
- **`api/views.py:941`** (`auto_scrape`'s `validate_fetch_url` pre-check). The memory note proposed dropping it as "redundant, matching the `openai_views.py:281` precedent." **We are keeping it.** `test_ssrf_wire.py::AutoScrapeSinkTests::test_blocked_current_url_refused_before_scrape` pins that a blocked `current_url` returns **400** and calls *neither* `scrape_url` *nor* `_get_session_id`. Dropping the pre-check would (a) flip the status to **500** (a blocked URL would fall through to `scrape_url`'s error, which `auto_scrape` surfaces via its `if "error" in scrape_result` → 500 branch), and (b) run session work before refusing. The `openai_views` precedent does not transfer: that path *silently continues* on a scrape error and never surfaces a status, whereas `auto_scrape` surfaces it. A one-line comment is added at the pre-check explaining why it is intentionally retained.

### New test

- The factory installs the route guard on the page it yields (mock the `async_playwright` chain, assert `install_route_guard` is called with the yielded page). Locks the "every page born guarded" guarantee.

## Item 1 — Fetch fewer subresources, make repeats cheaper

Two independent levers, both requested.

### Lever A — Resource-type filter

In both route handlers, abort requests whose `route.request.resource_type` is in a skip-set **before** proxying:

- New gate `_should_skip_resource(route) -> bool`.
- Skip-set from env `SCRAPE_SKIP_RESOURCE_TYPES`, default `{"image", "media", "font"}`.
- **Deliberately excludes** `stylesheet`, `script`, `xhr`, `fetch`, `document`. Removing CSS can let `display:none` boilerplate leak into `inner_text` (worse extraction, not better); `script`/`xhr`/`fetch` are load-bearing for SPA rendering; `document` is the page itself.
- Handler order: skip-resource check first → `route.abort()`; else the existing `_should_proxy` safety gate; else proxy. A skipped resource never reaches the cache/`safe_get`.

Aborting these resource types is strictly *less* egress, so it has no SSRF downside; it only reduces what Chromium loads, which text extraction never uses.

### Lever B — Per-page per-host pinned-session cache

New class `_PinnedSessionCache` in `ssrf_guard.py`:

- **State:** `dict{host -> _Entry(validated_ip, session, expiry_monotonic)}`, guarded by a `threading.Lock`.
- **`fetch(url, headers=None, timeout=15, max_bytes=MAX_FETCH_BYTES, max_redirects=MAX_REDIRECTS) -> requests.Response`** — same redirect + byte-cap contract as `safe_get`, but each hop reuses the host's pinned `requests.Session` (keep-alive) instead of building a new one.
  - On cache **miss** for a host: resolve via `_resolve_ips`, block-check *all* resolved IPs (unchanged "every IP must be public" rule), build a `requests.Session` with a `_PinnedHTTPAdapter(ip, host)` mounted on http/https, disable its cookie jar (`http.cookiejar.DefaultCookiePolicy(allowed_domains=[])`), cache `(ip, session, monotonic() + TTL)`, use it.
  - On cache **hit** within TTL: reuse `(ip, session)` — still pinned to the previously-validated public IP.
  - On **expiry**: re-resolve + re-block-check (drop and rebuild the entry).
- **`close()`** — close all cached sessions. Registered via `page.on("close", lambda *_: cache.close())` in both `install_route_guard` and `install_route_guard_sync`.
- **TTL:** env `SCRAPE_DNS_CACHE_TTL`, default **30s**. Per-page scope already bounds the cache to a single scrape (seconds); the TTL is a defense-in-depth upper bound on staleness, not the primary control.

#### Thread-safety

The async handler dispatches `cache.fetch` via `asyncio.to_thread`, so concurrent same-host subresources hit the cache from multiple worker threads. The `Lock` serializes dict get-or-create (one `getaddrinfo` per host under a concurrent burst). Concurrent `session.get` calls are safe because (a) urllib3's connection pool is thread-safe, (b) the cookie jar is disabled so there is no per-request mutable shared state, and (c) we never mutate `session.headers` (headers are passed per call). `pool_maxsize` on the adapter is sized so a same-host burst does not serialize on connections.

### Shared refactor (DRY, security-critical)

Extract the redirect-revalidation loop into one helper:

```python
def _follow_redirects(url, fetch_one, max_bytes, max_redirects):
    current = url
    for _ in range(max_redirects + 1):
        response = fetch_one(current)   # MUST return a response pinned to a validated IP for current's host
        location = response.headers.get("Location")
        if response.status_code in _REDIRECT_STATUSES and location:
            response.close()
            current = urljoin(current, location)
            continue
        return _enforce_byte_cap(response, max_bytes)
    raise UnsafeURLError(...)
```

- `safe_get` supplies a stateless `fetch_one` (fresh session per hop, via `_check_and_resolve` + `_pinned_fetch`) — **public signature unchanged**, so `_scrape_url_impl`'s direct callers are untouched.
- `_PinnedSessionCache.fetch` supplies a cached `fetch_one` (cached session per host).
- Per-hop re-validation now lives in exactly one place; the documented invariant on `fetch_one` is that it always returns a response pinned to a freshly-validated IP for that hop's host.

### Route-guard wiring

`_proxied_response_kwargs` gains the cache as a parameter and calls `cache.fetch` instead of module-level `safe_get`; its fail-closed policy (which exceptions abort, what is logged, which headers fulfill) is otherwise unchanged, so the sync/async paths still share one implementation. The async handler runs `_proxied_response_kwargs(cache, url, headers)` inside `asyncio.to_thread`; the sync handler calls it directly.

## Security argument (load-bearing)

- **Per-hop validation preserved.** A cache hit reuses a session already pinned to a validated public IP for that exact host; every redirect `Location` resolves and block-checks its own host fresh.
- **Caching reduces rebinding exposure.** Re-resolving on every subresource is itself a string of TOCTOU windows. Caching the validated IP and continuing to pin to it removes those windows; a mid-scrape rebind `public -> private` is never re-resolved within the TTL, and on TTL expiry the re-block-check refuses it.
- **Cache is per-page, never global** — no cross-request/cross-tenant reasoning.
- **Resource filtering only removes egress** — strictly safer.
- **Cookie continuity unaffected.** Cookies already flow through Chromium (fulfilled `Set-Cookie` → stored by Chromium → replayed via the forwarded `Cookie` request header), never through the `requests` session, which today is already fresh-per-call. Disabling the cached session's cookie jar matches current behavior and prevents cross-subresource cookie bleed.

## Testing (TDD, RED → GREEN)

**Item 2**
- Factory installs the route guard on the yielded page.
- Existing `PlaywrightNavigateSinkTests` / `PlaywrightFallbackRouteGuardTests` / `AutoScrapeSinkTests` stay green (no behavior change to seed validation, fail-fast, or 400 status).

**Item 1 — resource filter**
- `_should_skip_resource`: True for `image`/`media`/`font`, False for `document`/`script`/`xhr`/`fetch`/`stylesheet`.
- Handler aborts a skipped resource **without** invoking the cache/`safe_get`.

**Item 1 — cache**
- Second same-host `fetch` reuses the session and does **not** re-resolve (assert one `getaddrinfo` within TTL).
- After TTL expiry, the next `fetch` re-resolves and re-block-checks; a host that rebinds `public -> private` after the window is refused (`UnsafeURLError`).
- A blocked IP on a cache miss caches **nothing** and raises.
- A redirect to a different host validates that host fresh.
- Concurrent same-host fetches resolve exactly once (lock).
- `close()` closes all cached sessions.

**Regression**
- Full backend suite green (`pytest`), matching the PR #314 baseline (459 passed).

## Packaging

One branch off `main`. Two commits:
1. `refactor(security): centralize Playwright route-guard install in the browser factory` (Item 2).
2. `perf(security): skip non-text subresources + per-page pinned-session cache for in-browser fetches` (Item 1).

One PR (the tracked "two deferred follow-ups").

## Out of scope

- WebSocket / WebRTC SSRF (Chromium egresses these on its own socket; `page.route` does not intercept them) — pre-existing, documented gap.
- Any change to `safe_get`'s public signature or to the requests-first `_scrape_url_impl` path.
- Global / cross-page DNS caching.
