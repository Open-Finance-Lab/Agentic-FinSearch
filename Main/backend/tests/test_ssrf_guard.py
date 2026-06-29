"""Tests for datascraper.ssrf_guard (P0 Root B.1 SSRF guard)."""
import asyncio
import socket
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
    def _make_page(self, captured):
        async def fake_route(pattern, handler):
            captured["handler"] = handler
        page = MagicMock()
        page.route = fake_route
        return page

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_route_guard_aborts_blocked_request(self, m_gai):
        m_gai.return_value = _gai("127.0.0.1")
        route = MagicMock()
        route.request.url = "http://evil.example.test/x"
        route.abort = AsyncMock()
        route.continue_ = AsyncMock()
        captured = {}
        page = self._make_page(captured)

        async def run():
            await ssrf_guard.install_route_guard(page)
            await captured["handler"](route)

        asyncio.run(run())
        route.abort.assert_awaited_once()
        route.continue_.assert_not_awaited()

    @patch("datascraper.ssrf_guard.socket.getaddrinfo")
    def test_route_guard_allows_public_request(self, m_gai):
        m_gai.return_value = _gai("93.184.216.34")
        route = MagicMock()
        route.request.url = "http://example.com/x"
        route.abort = AsyncMock()
        route.continue_ = AsyncMock()
        captured = {}
        page = self._make_page(captured)

        async def run():
            await ssrf_guard.install_route_guard(page)
            await captured["handler"](route)

        asyncio.run(run())
        route.continue_.assert_awaited_once()
        route.abort.assert_not_awaited()


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
