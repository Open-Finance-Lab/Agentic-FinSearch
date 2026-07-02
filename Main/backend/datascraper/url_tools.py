"""
URL Tools - Simple web scraping via requests + BeautifulSoup
With smart compression using gpt-4o-mini
"""

import json
import logging
import requests
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from agents import function_tool

from datascraper import ssrf_guard

logger = logging.getLogger(__name__)

backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(backend_dir / '.env')

SITE_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "site_map.json"
_site_map_cache: Optional[Dict] = None

client = None
if os.getenv("OPENAI_API_KEY"):
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    except Exception as e:
        logger.warning(f"Failed to initialize OpenAI client: {e}")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


def _load_site_map() -> Dict:
    """Load site map configuration."""
    global _site_map_cache
    if _site_map_cache is None:
        try:
            with open(SITE_MAP_PATH, 'r', encoding='utf-8') as f:
                _site_map_cache = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load site map: {e}")
            _site_map_cache = {"routes": {}}
    return _site_map_cache


@function_tool
def resolve_url(route_id: str, params: str) -> str:
    """
    Build a URL from route_id and parameters.

    Args:
        route_id: Route name (e.g., 'yahoo_quote', 'yahoo_news', 'generic_url')
        params: JSON string with parameters (e.g., '{"ticker": "AAPL"}')

    Returns:
        JSON with the constructed URL
    """
    try:
        if isinstance(params, str):
            try:
                params_dict = json.loads(params)
            except json.JSONDecodeError:
                params_dict = {"ticker": params.strip()}
        else:
            params_dict = params

        site_map = _load_site_map()
        routes = site_map.get("routes", {})

        if route_id not in routes:
            return json.dumps({"error": f"Route '{route_id}' not found", "available": list(routes.keys())[:10]})

        route = routes[route_id]
        url_pattern = route["url_pattern"]
        defaults = route.get("param_defaults", {})

        for k, v in defaults.items():
            if k not in params_dict:
                params_dict[k] = v

        url = url_pattern.format(**params_dict)
        return json.dumps({"url": url})

    except Exception as e:
        return json.dumps({"error": str(e)})


def _clean_text(text: str) -> str:
    """Basic cleanup of raw text."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def _smart_compress(text: str, url: str) -> str:
    """Compress content using gpt-4o-mini."""
    if not client:
        return text[:15000] + "\n[Truncated: No OpenAI Key]"

    try:
        input_text = text[:60000] 
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "You are a web content compressor. "
                        "Your task is to convert the provided raw web page text into a clear, concise Markdown summary. "
                        "RULES:\n"
                        "1. Preserve ALL specific facts, numbers, dates, tickers, and technical details.\n"
                        "2. Remove navigation menus, footers, ads, legal disclaimers, and 'read more' links.\n"
                        "3. Structure the output with Markdown headers.\n"
                        "4. If the content is an article, summarize the key points but keep the narrative flow.\n"
                        "5. Output MUST be significantly shorter than the input while retaining value."
                    )
                },
                {"role": "user", "content": f"URL: {url}\n\nRAW CONTENT:\n{input_text}"}
            ],
            max_tokens=6000,
            temperature=0.3
        )
        compressed_text = response.choices[0].message.content
        logger.info(f"[SMART COMPRESSION] Success for {url}. Reduced {len(text)} chars -> {len(compressed_text)} chars ({(len(compressed_text)/len(text))*100:.1f}%)")
        return compressed_text
    except Exception as e:
        logger.error(f"[SMART COMPRESSION] Failed for {url}: {e}")
        return text[:15000] + f"\n[Compression failed: {str(e)}]"



def _dismiss_cookie_consent(page) -> None:
    """Try to dismiss cookie consent overlays by clicking common accept buttons."""
    _CONSENT_SELECTORS = [
        'button:has-text("Accept all")',
        'button:has-text("Accept All")',
        'button:has-text("Reject all")',
        'button:has-text("Reject All")',
        'button:has-text("I agree")',
        'button:has-text("Agree")',
        '[name="agree"]',
        '.consent-overlay button.accept',
        '#consent-page .accept-all',
    ]
    for selector in _CONSENT_SELECTORS:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=2000)
                logger.info(f"[CONSENT] Dismissed cookie overlay via: {selector}")
                # Consent click often triggers navigation/reload — wait for page to stabilize
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)
                return
        except Exception:
            continue


def _extract_article_text(page) -> str:
    """Try smart content selectors before falling back to full body."""
    _CONTENT_SELECTORS = ['article', 'main', '[role="main"]', '.article-body', '#main-content', '.caas-body']
    for selector in _CONTENT_SELECTORS:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=500):
                text = loc.inner_text(timeout=3000)
                if len(text) > 200:
                    logger.info(f"[SCRAPE] Used selector '{selector}' ({len(text)} chars)")
                    return text
        except Exception:
            continue
    return page.evaluate("document.body ? document.body.innerText : ''")


def scrape_with_playwright(url: str) -> str:
    """Fallback scraping using Playwright for SPAs."""
    try:
        ssrf_guard.validate_fetch_url(url)
    except ssrf_guard.UnsafeURLError as exc:
        logger.warning(f"[SSRF] Refused playwright fallback scrape {url}: {exc}")
        return ""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed, skipping fallback")
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-quic'])
            try:
                context = browser.new_context(
                    user_agent=HEADERS['User-Agent'],
                    viewport={'width': 1280, 'height': 800}
                )

                page = context.new_page()
                # Pin EVERY in-browser request (navigation + subresources) to a
                # validated IP via safe_get, closing the DNS-rebinding hole the
                # seed-only validate_fetch_url checks leave open. Must precede
                # the first navigation.
                ssrf_guard.install_route_guard_sync(page)
                # WebRTC hardening (see ssrf_guard.DISABLE_WEBRTC_JS): remove
                # RTCPeerConnection in every frame so a scraped page cannot open WebRTC
                # on Chromium's own socket. Must precede the first navigation.
                page.add_init_script(ssrf_guard.DISABLE_WEBRTC_JS)

                logger.info(f"Playwright scraping: {url}")
                page.goto(url, timeout=30000, wait_until="domcontentloaded")

                try:
                    ssrf_guard.validate_fetch_url(page.url)
                except ssrf_guard.UnsafeURLError as exc:
                    logger.warning(f"[SSRF] Playwright fallback landed on unsafe URL {page.url}: {exc}")
                    return ""

                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                _dismiss_cookie_consent(page)
                text = _extract_article_text(page)
                return _clean_text(text)
            finally:
                browser.close()

    except Exception as e:
        logger.error(f"Playwright scraping failed for {url}: {e}")
        return ""


def _scrape_url_impl(url: str) -> str:
    """
    Core scraping logic - callable directly.
    Fetch a URL and return the visible text content as JSON.
    """
    if not url.startswith(('http://', 'https://')):
        return json.dumps({"error": "Invalid URL"})

    try:
        ssrf_guard.validate_fetch_url(url)
    except ssrf_guard.UnsafeURLError as exc:
        logger.warning(f"[SSRF] Refused scrape target {url}: {exc}")
        return json.dumps({"error": "URL refused by security policy", "url": url})

    text = ""
    used_method = "requests"

    try:
        response = ssrf_guard.safe_get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg', 'header', 'footer', 'nav', 'aside']):
            tag.decompose()

        # Remove cookie/consent overlays (common class/id patterns)
        _consent_re = re.compile(r'consent|cookie-banner|gdpr|privacy-policy|ccpa|cmp-container', re.IGNORECASE)
        for el in soup.find_all(id=_consent_re):
            el.decompose()
        for el in soup.find_all(class_=_consent_re):
            el.decompose()

        # Prefer article/main content over full body
        article_text = ""
        for sel in ['article', 'main', '[role="main"]', '.caas-body', '.article-body']:
            tag = soup.select_one(sel)
            if tag:
                candidate = tag.get_text(separator='\n', strip=True)
                if len(candidate) > 200:
                    article_text = candidate
                    logger.info(f"[BS4] Used selector '{sel}' ({len(candidate)} chars)")
                    break

        text = _clean_text(article_text if article_text else soup.get_text(separator='\n', strip=True))

        is_suspicious = len(text) < 500 or "javascript" in text.lower() or "enable js" in text.lower()

        if is_suspicious:
            logger.info(f"Content suspicious (len={len(text)}), attempting Playwright fallback for {url}")
            pw_text = scrape_with_playwright(url)
            if len(pw_text) > len(text):
                text = pw_text
                used_method = "playwright"
                logger.info(f"Playwright fallback successful (len={len(text)})")

    except ssrf_guard.UnsafeURLError as exc:
        logger.warning(f"[SSRF] Aborted unsafe fetch of {url}: {exc}")
        return json.dumps({"error": "URL refused by security policy", "url": url})
    except Exception as e:
        logger.warning(f"Requests scraping failed for {url}: {e}. Attempting Playwright fallback.")
        text = scrape_with_playwright(url)
        if text:
            used_method = "playwright"
        else:
            return json.dumps({"error": f"Failed to scrape {url}: {str(e)}", "url": url})

    if len(text) > 4000:
        logger.info(f"Compressing content for {url} (Length: {len(text)})")
        text = _smart_compress(text, url)

    return json.dumps({
        "url": url,
        "content": text,
        "method": used_method,
        "length": len(text)
    })


@function_tool
def scrape_url(url: str) -> str:
    """
    Fetch a URL and return the visible text content.
    Uses requests first, falls back to Playwright for SPAs.
    Uses smart compression for long pages.

    NOTE: For Yahoo Finance URLs (finance.yahoo.com), you should use the 
    dedicated 'yahoo-finance' MCP tools (get_stock_info, get_stock_history,
    get_stock_financials, get_stock_analysis, get_stock_news) instead of 
    this tool. Only use scraping as a fallback when MCP tools cannot 
    provide the needed data.

    Args:
        url: The URL to scrape

    Returns:
        JSON with page content or error
    """
    # Properly validate Yahoo Finance URL by checking hostname
    try:
        parsed = urlparse(url)
        hostname = parsed.netloc.lower()
        is_yahoo_finance = hostname == "finance.yahoo.com" or hostname.endswith(".finance.yahoo.com")
    except Exception:
        is_yahoo_finance = False

    if is_yahoo_finance:
        logger.warning(f"Yahoo Finance scraping fallback triggered: {url}. MCP tools should be preferred.")
        result_json = _scrape_url_impl(url)
        try:
            result = json.loads(result_json)
            if "content" in result:
                hint = (
                    "\n\n[FALLBACK MODE]: This data was obtained by scraping the Yahoo Finance webpage. "
                    "When presenting this data to the user, you MUST disclose that you could not "
                    "retrieve this data directly via API and had to scrape this URL instead. "
                    "Scraped data may be less accurate than MCP tool data."
                )
                result["content"] = result["content"] + hint
                result["fallback_scrape"] = True
                result["source_url"] = url
                return json.dumps(result)
        except Exception:
            pass
        return result_json
        
    return _scrape_url_impl(url)


def get_url_tools():
    """Return URL tools for agent."""
    return [resolve_url, scrape_url]
