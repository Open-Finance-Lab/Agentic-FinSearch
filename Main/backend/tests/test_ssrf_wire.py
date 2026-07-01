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
        page = MagicMock()
        page.url = "http://example.com/x"
        ctx = MagicMock()
        ctx.new_page.return_value = page
        browser = MagicMock()
        browser.new_context.return_value = ctx
        p = MagicMock()
        p.chromium.launch.return_value = browser
        sp_cm = MagicMock()
        sp_cm.__enter__.return_value = p

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
