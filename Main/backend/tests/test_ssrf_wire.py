"""SSRF guard wiring tests (Task 7).

Every fetch / browser sink must route through datascraper.ssrf_guard
(validate_fetch_url + safe_get + the async route guard). These tests are
hermetic: the guard, the HTTP fetch, the Playwright browser and the chat
integration are all mocked, so no DB and no network are touched.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from datascraper import ssrf_guard
from datascraper.url_tools import _scrape_url_impl, scrape_with_playwright

BLOCKED = "http://169.254.169.254/latest/meta-data/"


def _fake_html_response(text: str) -> MagicMock:
    """Stand-in for a requests.Response: a body plus a no-op raise_for_status."""
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def _async_pw_mocks():
    """Mock chain for pt.PlaywrightBrowser: async_playwright().start() -> playwright
    .chromium.launch(**kw) -> browser.new_context() -> context.new_page() -> page.
    Returns (ap, page, launch_kwargs); launch kwargs are captured so hardening tests
    can assert on them. ONE scaffold shared by the factory-guard and Chromium-hardening
    tests, so a change to the factory's call chain is fixed in one place."""
    page = MagicMock(name="page")
    page.add_init_script = AsyncMock()
    context = MagicMock(name="context")
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock(name="browser")
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    launch_kwargs = {}

    async def _launch(**kwargs):
        launch_kwargs.update(kwargs)
        return browser

    playwright = MagicMock(name="playwright")
    playwright.chromium.launch = AsyncMock(side_effect=_launch)
    playwright.stop = AsyncMock()
    ap = MagicMock()
    ap.start = AsyncMock(return_value=playwright)
    return ap, page, launch_kwargs


def _sync_pw_mocks():
    """Sync-fallback twin of _async_pw_mocks for url_tools.scrape_with_playwright:
    sync_playwright() (context manager) -> p.chromium.launch(**kw) -> browser
    .new_context() -> context.new_page() -> page. Returns (sp_cm, page, launch_kwargs)."""
    page = MagicMock()
    page.url = "http://example.com/x"
    ctx = MagicMock()
    ctx.new_page.return_value = page
    browser = MagicMock()
    browser.new_context.return_value = ctx
    launch_kwargs = {}

    def _launch(**kwargs):
        launch_kwargs.update(kwargs)
        return browser

    p = MagicMock()
    p.chromium.launch.side_effect = _launch
    sp_cm = MagicMock()
    sp_cm.__enter__.return_value = p
    return sp_cm, page, launch_kwargs


class ScrapeUrlSinkTests(SimpleTestCase):
    """_scrape_url_impl must fetch only via ssrf_guard.safe_get."""

    def test_blocked_url_refused_and_never_fetched(self):
        with patch("datascraper.ssrf_guard.validate_fetch_url",
                   side_effect=ssrf_guard.UnsafeURLError("blocked")) as mock_validate, \
             patch("datascraper.ssrf_guard.safe_get") as mock_get, \
             patch("datascraper.url_tools.scrape_with_playwright", return_value="") as mock_pw, \
             patch("datascraper.url_tools.requests") as mock_requests:
            mock_requests.get.return_value = _fake_html_response("<html></html>")
            result = json.loads(_scrape_url_impl(BLOCKED))
        mock_validate.assert_called_once_with(BLOCKED)
        mock_get.assert_not_called()
        mock_pw.assert_not_called()
        self.assertIn("error", result)

    def test_redirecting_site_succeeds_via_safe_get(self):
        body = "<html><body><article>" + ("word " * 400) + "</article></body></html>"
        with patch("datascraper.ssrf_guard.validate_fetch_url", side_effect=lambda u: u), \
             patch("datascraper.ssrf_guard.safe_get",
                   return_value=_fake_html_response(body)) as mock_get, \
             patch("datascraper.url_tools.scrape_with_playwright", return_value="") as mock_pw, \
             patch("datascraper.url_tools.requests") as mock_requests:
            mock_requests.get.return_value = _fake_html_response("<html></html>")
            result = json.loads(_scrape_url_impl("http://example.com/redirect"))
        mock_get.assert_called_once()
        mock_pw.assert_not_called()
        self.assertNotIn("error", result)
        self.assertEqual(result["method"], "requests")
        self.assertIn("content", result)

    def test_oversize_response_aborted(self):
        with patch("datascraper.ssrf_guard.validate_fetch_url", side_effect=lambda u: u), \
             patch("datascraper.ssrf_guard.safe_get",
                   side_effect=ssrf_guard.UnsafeURLError("response exceeds 10485760 bytes")) as mock_get, \
             patch("datascraper.url_tools.scrape_with_playwright", return_value="") as mock_pw, \
             patch("datascraper.url_tools.requests") as mock_requests:
            mock_requests.get.return_value = _fake_html_response("<html></html>")
            result = json.loads(_scrape_url_impl("http://example.com/big"))
        mock_get.assert_called_once()
        mock_pw.assert_not_called()
        self.assertIn("error", result)


class PlaywrightFallbackSinkTests(SimpleTestCase):
    """scrape_with_playwright must refuse before launching a browser."""

    def test_blocked_url_refused_before_browser_launch(self):
        with patch("datascraper.ssrf_guard.validate_fetch_url",
                   side_effect=ssrf_guard.UnsafeURLError("blocked")) as mock_validate, \
             patch("playwright.sync_api.sync_playwright") as mock_sync_playwright:
            result = scrape_with_playwright(BLOCKED)
        self.assertEqual(result, "")
        mock_validate.assert_called_once_with(BLOCKED)
        mock_sync_playwright.assert_not_called()


class PlaywrightFallbackRouteGuardTests(SimpleTestCase):
    """bug_004: scrape_with_playwright must install the SSRF route guard so
    Chromium subresources are pinned, not only the seed + post-nav URL."""

    def test_scrape_with_playwright_installs_sync_route_guard(self):
        sp_cm, page, _ = _sync_pw_mocks()

        # Record whether the navigation had already happened at the instant the
        # guard is installed. The guard MUST precede page.goto, else the seed
        # navigation (and its initial-load subresources) go out on Chromium's
        # own unpinned socket — re-opening the rebinding hole bug_004 closes.
        # (scrape_with_playwright swallows exceptions, so we record-then-assert
        # rather than raise inside the side_effect.)
        order = {}

        def _record_install(p):
            order["goto_called_at_install"] = page.goto.called

        with patch("datascraper.ssrf_guard.validate_fetch_url", side_effect=lambda u: u), \
             patch("datascraper.ssrf_guard.install_route_guard_sync",
                   side_effect=_record_install) as mock_guard, \
             patch("datascraper.url_tools._extract_article_text", return_value="hello world"), \
             patch("datascraper.url_tools._dismiss_cookie_consent"), \
             patch("playwright.sync_api.sync_playwright", return_value=sp_cm):
            scrape_with_playwright("http://example.com/x")

        mock_guard.assert_called_once_with(page)
        page.goto.assert_called_once()
        self.assertIn("goto_called_at_install", order)
        self.assertFalse(
            order["goto_called_at_install"],
            "install_route_guard_sync must be called BEFORE page.goto",
        )


class PlaywrightNavigateSinkTests(SimpleTestCase):
    """playwright_tools.navigate_to_url must refuse before launching a browser."""

    def test_blocked_url_refused_before_browser_launch(self):
        import datascraper.playwright_tools as pt
        from datascraper.playwright_tools import navigate_to_url
        with patch("datascraper.ssrf_guard.validate_fetch_url",
                   side_effect=ssrf_guard.UnsafeURLError("blocked")) as mock_validate, \
             patch.object(pt, "PlaywrightBrowser") as mock_browser:
            raw = asyncio.run(navigate_to_url.on_invoke_tool(None, json.dumps({"url": BLOCKED})))
        result = json.loads(raw)
        self.assertFalse(result["success"])
        mock_validate.assert_called_once_with(BLOCKED)
        mock_browser.assert_not_called()


class AutoScrapeSinkTests(SimpleTestCase):
    """api.views.auto_scrape must validate current_url before scraping or session work."""

    def test_blocked_current_url_refused_before_scrape(self):
        import api.views as views
        request = RequestFactory().post(
            "/api/auto_scrape",
            data=json.dumps({"current_url": BLOCKED}),
            content_type="application/json",
        )
        with patch("datascraper.ssrf_guard.validate_fetch_url",
                   side_effect=ssrf_guard.UnsafeURLError("blocked")) as mock_validate, \
             patch.object(views, "scrape_url",
                          return_value=json.dumps({"content": "x"})) as mock_scrape, \
             patch.object(views, "_get_session_id", return_value="sid") as mock_sid, \
             patch.object(views, "get_context_integration") as mock_ci:
            mock_ci.return_value.get_scraped_urls.return_value = []
            response = views.auto_scrape(request)
        self.assertEqual(response.status_code, 400)
        mock_scrape.assert_not_called()
        mock_sid.assert_not_called()
        mock_validate.assert_called_once_with(BLOCKED)


class PlaywrightFactoryGuardTests(SimpleTestCase):
    """The PlaywrightBrowser factory must install the SSRF route guard on every
    page it yields, so all three async tools are guarded centrally and a future
    4th entrypoint cannot silently reopen the rebinding hole."""

    def test_factory_installs_route_guard_on_yielded_page(self):
        import datascraper.playwright_tools as pt

        ap, page, _ = _async_pw_mocks()

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


class ChromiumEgressHardeningTests(SimpleTestCase):
    """WebRTC/QUIC are disabled at the Chromium layer (defense-in-depth for the netns
    egress firewall) in BOTH the async factory and the sync fallback: --disable-quic on
    the launch args, and an init script that removes RTCPeerConnection so page JS cannot
    open WebRTC at all."""

    def test_async_factory_disables_webrtc_and_quic(self):
        import datascraper.playwright_tools as pt

        ap, page, launch_kwargs = _async_pw_mocks()

        async def run():
            with patch("playwright.async_api.async_playwright", return_value=ap), \
                 patch("datascraper.ssrf_guard.install_route_guard", new=AsyncMock()):
                async with pt.PlaywrightBrowser():
                    pass

        asyncio.run(run())
        args = launch_kwargs.get("args", [])
        self.assertIn("--disable-quic", args)
        page.add_init_script.assert_awaited()
        script = page.add_init_script.await_args.args[0]
        # Exact equality with the SHARED constant (single-sourced in ssrf_guard), not
        # just a substring: pins that this path cannot drift onto a local copy.
        self.assertEqual(script, ssrf_guard.DISABLE_WEBRTC_JS)
        self.assertIn("RTCPeerConnection", script)

    def test_sync_path_disables_webrtc_and_quic(self):
        import datascraper.url_tools as ut

        sp_cm, page, launch_kwargs = _sync_pw_mocks()

        # Record whether goto had already run at the instant the init script is installed;
        # it MUST precede navigation or it is inert on the seed page.
        order = {}

        def _record_init(script):
            order["goto_at_init"] = page.goto.called

        page.add_init_script.side_effect = _record_init

        with patch("datascraper.ssrf_guard.validate_fetch_url", side_effect=lambda u: u), \
             patch("datascraper.ssrf_guard.install_route_guard_sync"), \
             patch("datascraper.url_tools._extract_article_text", return_value="hello world"), \
             patch("datascraper.url_tools._dismiss_cookie_consent"), \
             patch("playwright.sync_api.sync_playwright", return_value=sp_cm):
            ut.scrape_with_playwright("http://example.com/x")

        self.assertIn("--disable-quic", launch_kwargs.get("args", []))
        page.add_init_script.assert_called_once()
        script = page.add_init_script.call_args.args[0]
        # Same shared-constant pin as the async path: both scrapers must install THE
        # ssrf_guard.DISABLE_WEBRTC_JS object, not a reintroduced local copy.
        self.assertEqual(script, ssrf_guard.DISABLE_WEBRTC_JS)
        self.assertIn("RTCPeerConnection", script)
        self.assertIn("goto_at_init", order)
        self.assertFalse(order["goto_at_init"], "add_init_script must precede page.goto")
