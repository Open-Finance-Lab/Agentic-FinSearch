"""
Playwright Tools - Browser automation for dynamic content extraction.
Provides granular tools for navigation, clicking, and content extraction.
"""

import json
import logging
import re
from contextlib import asynccontextmanager

from agents import function_tool

from datascraper import ssrf_guard

logger = logging.getLogger(__name__)

@asynccontextmanager
async def PlaywrightBrowser(timeout: int = 30000):
    """
    Ephemeral async browser context manager.
    Launches headless Chromium, yields page, closes on exit.

    Args:
        timeout: Default timeout in milliseconds (default 30s)
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("Playwright not installed. Run: playwright install chromium")
        raise ImportError("Playwright not installed")

    playwright = None
    browser = None

    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )

        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        context.set_default_timeout(timeout)

        page = await context.new_page()
        # Every page is born guarded: install the SSRF route guard here so all
        # async entrypoints (and any future one) are pinned without each having
        # to remember the call. See datascraper.ssrf_guard.install_route_guard.
        await ssrf_guard.install_route_guard(page)

        yield page

    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


def _clean_extracted_text(text: str) -> str:
    """Clean up extracted text content."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\t+', ' ', text)
    return text.strip()


def _get_page_summary(page) -> dict:
    """Get summary info about current page."""
    return {
        "title": page.title(),
        "url": page.url
    }


@function_tool
async def navigate_to_url(url: str) -> str:
    """
    Navigate to a URL and wait for page load.

    Args:
        url: The URL to navigate to

    Returns:
        JSON with page title, URL, and status
    """
    try:
        ssrf_guard.validate_fetch_url(url)
        async with PlaywrightBrowser() as page:
            logger.info(f"Navigating to: {url}")

            response = await page.goto(url, wait_until='load')
            # Brief delay for JS rendering - don't use networkidle (never completes on dynamic sites)
            await page.wait_for_timeout(2000)
            await ssrf_guard.assert_safe_page_url(page)

            status = response.status if response else None
            result = {
                "success": True,
                "title": await page.title(),
                "url": page.url,
                "status_code": status
            }

            # Extract initial content summary
            content = await page.inner_text('body')
            cleaned = _clean_extracted_text(content)
            if len(cleaned) > 2000:
                cleaned = cleaned[:2000] + "..."
            result["content_preview"] = cleaned

            logger.info(f"Navigation successful: {await page.title()}")
            return json.dumps(result)

    except Exception as e:
        logger.error(f"Navigation failed for {url}: {e}")
        return json.dumps({
            "success": False,
            "error": str(e),
            "url": url
        })


@function_tool
async def click_element(url: str, selector: str) -> str:
    """
    Navigate to URL, click an element, and return the resulting page content.

    Args:
        url: The URL to navigate to first
        selector: CSS selector or text content to click (e.g., 'a:has-text("Read more")')

    Returns:
        JSON with clicked element info and new page content
    """
    try:
        ssrf_guard.validate_fetch_url(url)
        async with PlaywrightBrowser() as page:
            logger.info(f"Navigating to {url} to click: {selector}")

            # Navigate first - use 'load' event, not networkidle
            await page.goto(url, wait_until='load')
            await ssrf_guard.assert_safe_page_url(page)
            await page.wait_for_timeout(2000)  # Brief delay for JS rendering

            # Find the element with multiple strategies
            element = None

            # Try as CSS selector first
            try:
                element = page.locator(selector).first
                if await element.count() == 0:
                    element = None
            except Exception:
                element = None

            # Try as text-based selector
            if element is None:
                try:
                    element = page.get_by_text(selector, exact=False).first
                    if await element.count() == 0:
                        element = None
                except Exception:
                    element = None

            # Try as link text
            if element is None:
                try:
                    element = page.get_by_role("link", name=selector).first
                    if await element.count() == 0:
                        element = None
                except Exception:
                    element = None

            if element is None:
                return json.dumps({
                    "success": False,
                    "error": f"Element not found: {selector}",
                    "url": url
                })

            # Wait for element to be visible before clicking
            await element.wait_for(state='visible', timeout=5000)

            # Get element info before clicking
            try:
                element_text = (await element.inner_text())[:100]
            except Exception:
                element_text = selector

            # Click and wait for navigation
            original_url = page.url
            await element.click()

            # Wait for either URL change or page load (whichever comes first)
            try:
                await page.wait_for_url(lambda u: u != original_url, timeout=5000)
            except Exception:
                pass  # URL didn't change - might be same-page navigation
            await page.wait_for_load_state('load', timeout=10000)
            await page.wait_for_timeout(2000)  # Brief delay for JS rendering
            await ssrf_guard.assert_safe_page_url(page)

            # Extract new page content
            content = await page.inner_text('body')
            cleaned = _clean_extracted_text(content)

            result = {
                "success": True,
                "clicked_element": element_text,
                "new_url": page.url,
                "new_title": await page.title(),
                "content": cleaned[:5000] if len(cleaned) > 5000 else cleaned
            }

            logger.info(f"Click successful, now at: {page.url}")
            return json.dumps(result)

    except Exception as e:
        logger.error(f"Click failed for {selector} on {url}: {e}")
        return json.dumps({
            "success": False,
            "error": str(e),
            "url": url,
            "selector": selector
        })


@function_tool
async def extract_page_content(url: str, content_selector: str = "") -> str:
    """
    Navigate to URL and extract the main text content.

    Args:
        url: The URL to extract content from
        content_selector: Optional CSS selector to extract specific content
                         (defaults to body if not provided)

    Returns:
        JSON with extracted text content
    """
    try:
        ssrf_guard.validate_fetch_url(url)
        async with PlaywrightBrowser() as page:
            logger.info(f"Extracting content from: {url}")

            await page.goto(url, wait_until='load')
            await ssrf_guard.assert_safe_page_url(page)
            await page.wait_for_timeout(2000)  # Brief delay for JS rendering

            # Determine which selector to use
            selector = content_selector if content_selector else 'body'

            # Try to find main content areas if using body
            if selector == 'body':
                for main_selector in ['article', 'main', '[role="main"]', '.article-body', '#main-content']:
                    try:
                        if await page.locator(main_selector).count() > 0:
                            selector = main_selector
                            break
                    except Exception:
                        continue

            # Extract content
            try:
                content = await page.locator(selector).first.inner_text()
            except Exception:
                content = await page.inner_text('body')

            cleaned = _clean_extracted_text(content)

            result = {
                "success": True,
                "url": page.url,
                "title": await page.title(),
                "selector_used": selector,
                "content": cleaned,
                "content_length": len(cleaned)
            }

            logger.info(f"Extracted {len(cleaned)} chars from {url}")
            return json.dumps(result)

    except Exception as e:
        logger.error(f"Content extraction failed for {url}: {e}")
        return json.dumps({
            "success": False,
            "error": str(e),
            "url": url
        })


def get_playwright_tools():
    """Return Playwright tools for agent."""
    return [navigate_to_url, click_element, extract_page_content]
