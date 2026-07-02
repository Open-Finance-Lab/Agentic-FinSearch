"""Tests for datascraper.ssrf_guard (P0 Root B.1 SSRF guard)."""
import asyncio
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from datascraper import ssrf_guard
from datascraper.ssrf_guard import UnsafeURLError, safe_get, validate_fetch_url


def _gai(*ips):
    """Build a fake socket.getaddrinfo() return value for the given textual IPs."""
    out = []
    for ip in ips:
        if ":" in ip:
            family = socket.AF_INET6
            sockaddr = (ip, 0, 0, 0)
        else:
            family = socket.AF_INET
            sockaddr = (ip, 0)
        out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
    return out


class _FakeResp:
    """Minimal requests.Response stand-in to drive safe_get without a live network."""

    def __init__(self, status_code=200, headers=None, chunks=(), location=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        if location is not None:
            self.headers["Location"] = location
        self._chunks = list(chunks)
        self.closed = False
        self._content = None
        self._content_consumed = False

    def iter_content(self, chunk_size=65536):
        for chunk in self._chunks:
            yield chunk

    @property
    def content(self):
        if self._content is not None:
            return self._content
        return b"".join(self._chunks)

    def close(self):
        self.closed = True


class NormalizeAndBlockTests(SimpleTestCase):
    def test_normalize_collapses_ipv4_mapped(self):
        self.assertEqual(ssrf_guard._normalize_ip("::ffff:127.0.0.1"), "127.0.0.1")

    def test_normalize_passthrough_plain_ipv4(self):
        self.assertEqual(ssrf_guard._normalize_ip("8.8.8.8"), "8.8.8.8")

    def test_blocked_ranges(self):
        for ip in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "0.0.0.0",
                   "::ffff:10.0.0.1", "fe80::1", "224.0.0.1"):
            self.assertTrue(ssrf_guard._is_blocked_ip(ip), ip)

    def test_blocks_cgnat_shared_address_space(self):
        # is_private and is_reserved are both False for this whole range -- only
        # the explicit _EXTRA_BLOCKED_NETS entry blocks it (rationale at its
        # definition).
        for ip in ("100.64.0.0", "100.64.0.1", "100.96.0.5",
                   "100.127.255.255", "::ffff:100.64.0.1"):
            self.assertTrue(ssrf_guard._is_blocked_ip(ip), ip)

    def test_blocks_ietf_protocol_anycast_carveouts(self):
        # CPython carves exactly these two globally-reachable anycast addresses
        # out of the otherwise-is_private 192.0.0.0/24 -- only the explicit
        # _EXTRA_BLOCKED_NETS entry blocks them (rationale at its definition).
        for ip in ("192.0.0.9", "192.0.0.10", "::ffff:192.0.0.9"):
            self.assertTrue(ssrf_guard._is_blocked_ip(ip), ip)

    def test_public_ips_allowed(self):
        for ip in ("8.8.8.8", "93.184.216.34", "1.1.1.1"):
            self.assertFalse(ssrf_guard._is_blocked_ip(ip), ip)


class ValidateFetchUrlTests(SimpleTestCase):
    def test_blocks_non_http_scheme(self):
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("ftp://example.com/x")
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("file:///etc/passwd")

    def test_blocks_missing_host(self):
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("http:///no-host")

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_blocks_cloud_metadata(self, m_gai):
        m_gai.return_value = _gai("169.254.169.254")
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("http://metadata.example.test/latest/meta-data/")

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_blocks_private(self, m_gai):
        m_gai.return_value = _gai("10.0.0.5")
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("http://intranet.example.test/")

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_blocks_loopback(self, m_gai):
        m_gai.return_value = _gai("127.0.0.1")
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("http://localhost.example.test/")

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_blocks_ipv4_mapped_loopback(self, m_gai):
        m_gai.return_value = _gai("::ffff:127.0.0.1")
        with self.assertRaises(UnsafeURLError):
            validate_fetch_url("http://mapped.example.test/")

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_allows_public_and_returns_url(self, m_gai):
        m_gai.return_value = _gai("93.184.216.34")
        self.assertEqual(
            validate_fetch_url("http://example.com/page"),
            "http://example.com/page",
        )


class SafeGetTests(SimpleTestCase):
    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    @patch("datascraper.ssrf_guard._pinned_fetch")
    def test_byte_cap_aborts_oversized_stream(self, m_fetch, m_gai):
        m_gai.return_value = _gai("93.184.216.34")
        one_mb = b"x" * (1024 * 1024)
        m_fetch.return_value = _FakeResp(chunks=[one_mb, one_mb, one_mb])
        with self.assertRaises(UnsafeURLError):
            safe_get("http://example.com/big", max_bytes=2 * 1024 * 1024)

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    @patch("datascraper.ssrf_guard._pinned_fetch")
    def test_byte_cap_aborts_on_content_length(self, m_fetch, m_gai):
        m_gai.return_value = _gai("93.184.216.34")
        m_fetch.return_value = _FakeResp(headers={"Content-Length": "5000000"})
        with self.assertRaises(UnsafeURLError):
            safe_get("http://example.com/big", max_bytes=1000000)

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    @patch("datascraper.ssrf_guard._pinned_fetch")
    def test_redirect_to_blocked_is_rejected(self, m_fetch, m_gai):
        def fake_gai(host, *args, **kwargs):
            if host == "example.com":
                return _gai("93.184.216.34")
            return _gai("127.0.0.1")
        m_gai.side_effect = fake_gai
        m_fetch.return_value = _FakeResp(
            status_code=302, location="http://localhost.example.test/"
        )
        with self.assertRaises(UnsafeURLError):
            safe_get("http://example.com/start")
        self.assertEqual(m_fetch.call_count, 1)

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    @patch("datascraper.ssrf_guard._pinned_fetch")
    def test_redirect_to_public_is_followed(self, m_fetch, m_gai):
        m_gai.return_value = _gai("93.184.216.34")
        redirect = _FakeResp(status_code=301, location="http://example.org/final")
        final = _FakeResp(status_code=200, chunks=[b"hello"])
        m_fetch.side_effect = [redirect, final]
        resp = safe_get("http://example.com/start")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp._content, b"hello")
        self.assertEqual(m_fetch.call_count, 2)

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    @patch("datascraper.ssrf_guard._pinned_fetch")
    def test_too_many_redirects_raises(self, m_fetch, m_gai):
        m_gai.return_value = _gai("93.184.216.34")
        m_fetch.return_value = _FakeResp(
            status_code=302, location="http://example.com/loop"
        )
        with self.assertRaises(UnsafeURLError):
            safe_get("http://example.com/start", max_redirects=2)
        self.assertEqual(m_fetch.call_count, 3)


class RouteGuardTests(SimpleTestCase):
    """The async Playwright route guard must fulfill in-browser requests from
    the SSRF-pinned ``safe_get`` (never ``route.continue_()``), so Chromium
    never opens its own DNS-rebind-vulnerable socket."""

    def _make_page(self, captured):
        async def fake_route(pattern, handler):
            captured["handler"] = handler
        page = MagicMock()
        page.route = fake_route
        return page

    _BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Host": "example.com",
    }

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

    def _drive(self, route):
        captured = {}
        page = self._make_page(captured)

        async def run():
            await ssrf_guard.install_route_guard(page)
            await captured["handler"](route)

        asyncio.run(run())

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_aborts_blocked_request(self, m_cache):
        m_get = m_cache.return_value.fetch
        m_get.side_effect = UnsafeURLError("blocked")
        route = self._route("http://evil.example.test/x")
        self._drive(route)
        route.abort.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        # Never delegate the fetch back to Chromium (would re-resolve DNS).
        route.continue_.assert_not_awaited()

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_fulfills_public_request_from_pinned_fetch(self, m_cache):
        m_get = m_cache.return_value.fetch
        resp = _FakeResp(
            status_code=200,
            headers={"Content-Type": "text/html", "Content-Encoding": "gzip"},
        )
        resp._content = b"<html>ok</html>"
        m_get.return_value = resp
        route = self._route("http://example.com/x")
        self._drive(route)
        route.fulfill.assert_awaited_once()
        kwargs = route.fulfill.await_args.kwargs
        self.assertEqual(kwargs["status"], 200)
        self.assertEqual(kwargs["body"], b"<html>ok</html>")
        # requests already decoded the body, so the encoding header must be
        # stripped or Chromium would try to gunzip plaintext.
        lowered = {k.lower() for k in kwargs["headers"]}
        self.assertNotIn("content-encoding", lowered)
        self.assertIn("content-type", lowered)
        route.abort.assert_not_awaited()
        route.continue_.assert_not_awaited()
        m_get.assert_called_once()
        self.assertEqual(m_get.call_args.args[0], "http://example.com/x")

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_forwards_browser_headers_to_pinned_fetch(self, m_cache):
        m_get = m_cache.return_value.fetch
        # The pinned fetch must present the browser's own User-Agent (the
        # context deliberately sets a Chrome UA to avoid bot-gating); SSRF
        # safety comes from IP-pinning, not from hiding the UA. Framing/encoding
        # headers (Host, Accept-Encoding) are stripped so requests can
        # re-derive Host and only negotiate encodings it decodes.
        resp = _FakeResp(status_code=200, headers={"Content-Type": "text/html"})
        resp._content = b"<html>ok</html>"
        m_get.return_value = resp
        route = self._route("http://example.com/x")
        self._drive(route)
        m_get.assert_called_once()
        sent = m_get.call_args.kwargs.get("headers")
        self.assertIsNotNone(sent, "safe_get must be called with forwarded headers")
        lowered = {k.lower(): v for k, v in sent.items()}
        self.assertIn("user-agent", lowered)
        self.assertIn("Chrome", lowered["user-agent"])
        self.assertNotIn("host", lowered)
        self.assertNotIn("accept-encoding", lowered)

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_aborts_non_get(self, m_cache):
        m_get = m_cache.return_value.fetch
        # cache.fetch is GET-only; non-GET in-browser requests fail closed.
        route = self._route("http://example.com/api", method="POST")
        self._drive(route)
        route.abort.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        m_get.assert_not_called()

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_aborts_skipped_resource(self, m_cache):
        # image/media/font are aborted before any fetch — they don't feed
        # inner_text, so we never spend DNS+TLS or egress on them.
        route = self._route("http://example.com/logo.png", resource_type="image")
        self._drive(route)
        route.abort.assert_awaited_once()
        route.fulfill.assert_not_awaited()
        m_cache.return_value.fetch.assert_not_called()

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_route_guard_closes_cache_on_page_close(self, m_cache):
        # The guard must wire page.on("close", ...) -> cache.close() so the
        # per-page sessions (including TTL-displaced ones held in _stale) are
        # torn down when the page closes.
        handlers = {}
        page = MagicMock()

        async def fake_route(pattern, handler):
            pass

        page.route = fake_route
        page.on = lambda event, cb: handlers.__setitem__(event, cb)
        asyncio.run(ssrf_guard.install_route_guard(page))
        self.assertIn("close", handlers)
        handlers["close"]("evt")  # simulate Playwright firing the close event
        m_cache.return_value.close.assert_called_once()


class SyncRouteGuardTests(SimpleTestCase):
    """install_route_guard_sync mirrors the async guard for the sync
    Playwright fallback (scrape_with_playwright)."""

    def _page(self, captured):
        page = MagicMock()

        def route(pattern, handler):
            captured["handler"] = handler

        page.route = route
        return page

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

    def _drive(self, route):
        captured = {}
        page = self._page(captured)
        ssrf_guard.install_route_guard_sync(page)
        captured["handler"](route)

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_sync_guard_aborts_blocked_request(self, m_cache):
        m_get = m_cache.return_value.fetch
        m_get.side_effect = UnsafeURLError("blocked")
        route = self._route("http://evil.example.test/x")
        self._drive(route)
        route.abort.assert_called_once()
        route.fulfill.assert_not_called()

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_sync_guard_fulfills_public_request(self, m_cache):
        m_get = m_cache.return_value.fetch
        resp = _FakeResp(status_code=200, headers={"Content-Type": "text/html"})
        resp._content = b"hi"
        m_get.return_value = resp
        route = self._route("http://example.com/x")
        self._drive(route)
        route.fulfill.assert_called_once()
        self.assertEqual(route.fulfill.call_args.kwargs["body"], b"hi")
        route.abort.assert_not_called()

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_sync_guard_forwards_browser_user_agent(self, m_cache):
        m_get = m_cache.return_value.fetch
        resp = _FakeResp(status_code=200, headers={"Content-Type": "text/html"})
        resp._content = b"hi"
        m_get.return_value = resp
        route = self._route("http://example.com/x")
        self._drive(route)
        sent = m_get.call_args.kwargs.get("headers")
        self.assertIsNotNone(sent)
        lowered = {k.lower(): v for k, v in sent.items()}
        self.assertIn("Chrome", lowered.get("user-agent", ""))
        self.assertNotIn("host", lowered)
        self.assertNotIn("accept-encoding", lowered)

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_sync_guard_aborts_non_get(self, m_cache):
        m_get = m_cache.return_value.fetch
        route = self._route("http://example.com/api", method="POST")
        self._drive(route)
        route.abort.assert_called_once()
        m_get.assert_not_called()

    @patch("datascraper.ssrf_guard._PinnedSessionCache")
    def test_sync_guard_aborts_skipped_resource(self, m_cache):
        route = self._route("http://example.com/font.woff2", resource_type="font")
        self._drive(route)
        route.abort.assert_called_once()
        route.fulfill.assert_not_called()
        m_cache.return_value.fetch.assert_not_called()


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

    def test_ttl_reresolve_defers_closing_displaced_session(self):
        # A session displaced by a TTL re-resolve must NOT be closed inline: a
        # concurrent same-host fetch may still be mid-get on it (it is returned
        # under the lock but used outside it). It is closed at page close instead.
        cache = ssrf_guard._PinnedSessionCache(ttl=30)
        clock = {"t": 100.0}
        built = []

        def fake_build(ip, host):
            s = MagicMock()
            built.append(s)
            return s

        with patch("datascraper.ssrf_guard.time.monotonic",
                   side_effect=lambda: clock["t"]), \
             patch.object(ssrf_guard._PinnedSessionCache, "_build_session",
                          side_effect=fake_build), \
             patch("datascraper.ssrf_guard._resolve_ips",
                   side_effect=[["93.184.216.34"], ["93.184.216.34"]]):
            cache._session_for("example.com")   # builds S0, expiry = 130
            clock["t"] = 200.0                  # past the 30s TTL
            cache._session_for("example.com")   # re-resolve -> builds S1, S0 displaced
            built[0].close.assert_not_called()  # S0 NOT torn down inline
        cache.close()
        built[0].close.assert_called_once()     # both closed at page teardown
        built[1].close.assert_called_once()
        self.assertEqual(cache._stale, [])


class AssertSafePageUrlTests(SimpleTestCase):
    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_raises_on_blocked_current_url(self, m_gai):
        m_gai.return_value = _gai("169.254.169.254")
        page = MagicMock()
        page.url = "http://metadata.example.test/latest"
        with self.assertRaises(UnsafeURLError):
            asyncio.run(ssrf_guard.assert_safe_page_url(page))

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_passes_on_public_current_url(self, m_gai):
        m_gai.return_value = _gai("93.184.216.34")
        page = MagicMock()
        page.url = "http://example.com/ok"
        asyncio.run(ssrf_guard.assert_safe_page_url(page))  # must not raise
