"""
SSRF guard for all outbound fetches and in-browser (Playwright) navigations.

Single chokepoint for P0 Root B.1: every URL the agent fetches or browses must
(a) use http/https, (b) have a host, and (c) resolve ONLY to publicly-routable
IPs. The connection is pinned to the validated IP so a DNS-rebinding answer
between validation and connect cannot redirect us to a private address, and the
response body is byte-capped to defeat huge-response resource exhaustion.

The Playwright route guards close the same DNS-rebinding hole IN-BROWSER: rather
than re-validate and then hand the request back to Chromium (which would resolve
DNS a SECOND time on its own socket — the rebinding window), they fetch the
request through ``safe_get`` (IP-pinned, byte-capped) and ``route.fulfill`` the
buffered response, so Chromium never opens its own connection for the HTTP(S)
requests ``page.route`` intercepts. Non-GET and non-http(s) in-browser requests
fail closed (aborted), as ``safe_get`` only serves pinned GETs. (WebSocket /
WebRTC are NOT intercepted by ``page.route`` and remain a separate, pre-existing
gap — out of scope here.)

Public contract (do not rename):
    UnsafeURLError
    validate_fetch_url(url) -> str
    safe_get(url, headers=None, timeout=15, max_bytes=MAX_FETCH_BYTES,
             max_redirects=MAX_REDIRECTS) -> requests.Response
    install_route_guard(page)      (async, Playwright)
    install_route_guard_sync(page) (sync, Playwright)
    assert_safe_page_url(page)     (async, Playwright)
"""
import asyncio
import ipaddress
import logging
import os
import socket
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)

# Maximum bytes we will buffer from any single fetched response (default 10 MB).
MAX_FETCH_BYTES = int(os.getenv("SCRAPE_MAX_BYTES", "10485760"))
# Maximum number of redirect hops safe_get will follow (each re-validated).
MAX_REDIRECTS = int(os.getenv("SCRAPE_MAX_REDIRECTS", "3"))

_ALLOWED_SCHEMES = ("http", "https")
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_STREAM_CHUNK_BYTES = 65536

# Response headers that must NOT be forwarded when fulfilling a Playwright route
# from a safe_get response: requests has already decoded the body (so a stale
# Content-Encoding would make Chromium try to gunzip plaintext), and the framing
# headers no longer match the buffered body we hand back.
_NON_FORWARDABLE_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding", "connection"}
)

# Request headers we do NOT forward from Chromium into the pinned safe_get:
# host/content-length/connection/transfer-encoding are re-derived by requests,
# and accept-encoding is dropped so requests only negotiates encodings it can
# decode (br/zstd would otherwise come back compressed but be handed to Chromium
# with the Content-Encoding header stripped -> garbage). The browser's
# User-Agent / Accept / Accept-Language / Referer / Cookie ARE forwarded so the
# pinned fetch is indistinguishable to the origin (bot-gated pages keep working).
_NON_FORWARDABLE_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "content-encoding", "transfer-encoding",
     "connection", "accept-encoding"}
)


class UnsafeURLError(ValueError):
    """Raised when a URL, host, or resolved IP is rejected by the SSRF guard."""


def _normalize_ip(ip_str: str) -> str:
    """Collapse an IPv4-mapped IPv6 address (``::ffff:a.b.c.d``) to its bare IPv4
    form before classification, so a private IPv4 cannot be smuggled past the
    IPv6 range checks. Non-mapped addresses are returned canonicalized."""
    ip = ipaddress.ip_address(ip_str)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return str(mapped)
    return str(ip)


def _is_blocked_ip(ip_str: str) -> bool:
    """True if ``ip_str`` (after :func:`_normalize_ip`) is in a range that must
    never be reachable from a user-driven fetch: private, loopback, link-local
    (incl. 169.254.169.254 cloud metadata), multicast, reserved, or the
    unspecified address. An unparseable value is treated as blocked."""
    try:
        ip = ipaddress.ip_address(_normalize_ip(ip_str))
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_ips(host: str) -> List[str]:
    """Resolve ``host`` to its textual IPs via getaddrinfo. Raises
    :class:`UnsafeURLError` if resolution fails or yields nothing."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for host {host!r}: {exc}")
    ips = [info[4][0] for info in infos]
    if not ips:
        raise UnsafeURLError(f"No addresses resolved for host {host!r}")
    return ips


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


def validate_fetch_url(url: str) -> str:
    """Public pre-check for Playwright entrypoints and auto_scrape. Raises
    :class:`UnsafeURLError` if the scheme/host is illegal or any resolved IP is
    non-routable; otherwise returns ``url`` unchanged."""
    _check_and_resolve(url)
    return url


class _PinnedHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that forces the TCP connection to a pre-validated IP while
    preserving the original Host header and (for TLS) SNI + cert-hostname
    verification. Pins the fetch to the IP we already block-checked, defeating
    DNS rebinding between validation and connect."""

    def __init__(self, pinned_ip: str, pinned_host: str, *args, **kwargs):
        self._pinned_ip = pinned_ip
        self._pinned_host = pinned_host
        super().__init__(*args, **kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        host_params["host"] = self._pinned_ip
        if host_params.get("scheme") == "https":
            pool_kwargs["server_hostname"] = self._pinned_host
            pool_kwargs["assert_hostname"] = self._pinned_host
        return host_params, pool_kwargs


def _pinned_fetch(
    url: str, ip: str, headers: Optional[dict], timeout: int
) -> requests.Response:
    """Perform ONE non-redirecting, IP-pinned, streaming GET. Isolated so
    safe_get's redirect + byte-cap orchestration is testable without a live
    network."""
    host = urlparse(url).hostname
    session = requests.Session()
    adapter = _PinnedHTTPAdapter(ip, host)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session.get(
        url,
        headers=headers,
        timeout=timeout,
        stream=True,
        allow_redirects=False,
    )


def _enforce_byte_cap(response: requests.Response, max_bytes: int) -> requests.Response:
    """Abort (raise :class:`UnsafeURLError`) if the declared Content-Length or the
    cumulative streamed body exceeds ``max_bytes``; otherwise buffer the bounded
    body onto ``response`` and return it."""
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_len = int(declared)
        except ValueError:
            # Malformed/non-numeric Content-Length: ignore the hint and fall
            # through to the streamed byte-cap below (which is authoritative).
            declared_len = None
        # NOTE: the abort MUST be raised outside the int() try/except — UnsafeURLError
        # subclasses ValueError, so raising it inside that block would be swallowed by
        # the malformed-header handler and defeat the Content-Length pre-check entirely.
        if declared_len is not None and declared_len > max_bytes:
            response.close()
            raise UnsafeURLError(
                f"Response too large: Content-Length {declared} exceeds cap {max_bytes}"
            )
    body = bytearray()
    for chunk in response.iter_content(chunk_size=_STREAM_CHUNK_BYTES):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > max_bytes:
            response.close()
            raise UnsafeURLError(
                f"Response body exceeded byte cap of {max_bytes} bytes"
            )
    response._content = bytes(body)
    response._content_consumed = True
    return response


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


def _fulfill_headers(response: requests.Response) -> dict:
    """Headers to forward when fulfilling a Playwright route from a ``safe_get``
    response, dropping the encoding/framing headers that no longer match the
    already-decoded, re-buffered body (see ``_NON_FORWARDABLE_HEADERS``)."""
    return {
        k: v
        for k, v in response.headers.items()
        if k.lower() not in _NON_FORWARDABLE_HEADERS
    }


def _should_proxy(route) -> bool:
    """True only for GET http(s) requests, which ``safe_get`` can serve pinned.
    Everything else (POST/PUT/..., ws://, data:, blob:) fails closed."""
    request = route.request
    if request.method != "GET":
        return False
    return urlparse(request.url).scheme in _ALLOWED_SCHEMES


def _forward_headers(route) -> dict:
    """The browser request's headers to replay through ``safe_get``, minus the
    framing/encoding headers requests must control (see
    ``_NON_FORWARDABLE_REQUEST_HEADERS``). Forwarding the browser's own
    User-Agent/Accept/etc. keeps the IP-pinned fetch indistinguishable to the
    origin, so bot-gated pages behave as they did before fulfill-based pinning."""
    try:
        headers = dict(route.request.headers)
    except Exception:
        return {}
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in _NON_FORWARDABLE_REQUEST_HEADERS
    }


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
        request_url = route.request.url
        if not _should_proxy(route):
            await route.abort()
            return
        try:
            # safe_get is blocking; run it off the event loop. It validates,
            # pins to the resolved IP, follows redirects re-validating each, and
            # byte-caps the body — raising UnsafeURLError on any violation. The
            # browser's headers (UA/Accept/...) are forwarded so the origin sees
            # the same request Chromium would have made.
            response = await asyncio.to_thread(
                safe_get, request_url, headers=_forward_headers(route)
            )
        except UnsafeURLError as exc:
            logger.warning(
                "[ssrf_guard] aborting in-browser request to %s: %s",
                request_url,
                exc,
            )
            await route.abort()
            return
        except Exception as exc:  # network error, timeout, etc. — fail closed.
            logger.warning(
                "[ssrf_guard] fetch failed for in-browser request %s: %s",
                request_url,
                exc,
            )
            await route.abort()
            return
        await route.fulfill(
            status=response.status_code,
            headers=_fulfill_headers(response),
            body=response.content,
        )

    await page.route("**/*", _handler)


def install_route_guard_sync(page) -> None:
    """Synchronous twin of :func:`install_route_guard` for the sync Playwright
    fallback (``url_tools.scrape_with_playwright``). Same guarantee: every
    in-browser GET is fulfilled from the IP-pinned ``safe_get`` so Chromium never
    re-resolves DNS on its own socket; non-GET / non-http(s) fail closed.

    MUST be called BEFORE the first ``page.goto``."""

    def _handler(route):
        request_url = route.request.url
        if not _should_proxy(route):
            route.abort()
            return
        try:
            response = safe_get(request_url, headers=_forward_headers(route))
        except UnsafeURLError as exc:
            logger.warning(
                "[ssrf_guard] aborting in-browser request to %s: %s",
                request_url,
                exc,
            )
            route.abort()
            return
        except Exception as exc:  # network error, timeout, etc. — fail closed.
            logger.warning(
                "[ssrf_guard] fetch failed for in-browser request %s: %s",
                request_url,
                exc,
            )
            route.abort()
            return
        route.fulfill(
            status=response.status_code,
            headers=_fulfill_headers(response),
            body=response.content,
        )

    page.route("**/*", _handler)


async def assert_safe_page_url(page) -> None:
    """After a goto/click settles, re-validate the page's CURRENT URL (it may
    have changed via a JS or meta redirect) and raise :class:`UnsafeURLError`
    if it now points at a blocked host."""
    validate_fetch_url(page.url)
