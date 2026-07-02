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
WebRTC are NOT intercepted by ``page.route``; that gap is closed for
private/link-local/metadata destinations at the netns layer by the egress
firewall — see ops/egress_firewall.py, which deliberately still accepts
own-subnet + public egress — with ``DISABLE_WEBRTC_JS`` below as best-effort
surface reduction on top.)

Public contract (do not rename):
    UnsafeURLError
    validate_fetch_url(url) -> str
    safe_get(url, headers=None, timeout=15, max_bytes=MAX_FETCH_BYTES,
             max_redirects=MAX_REDIRECTS) -> requests.Response
    install_route_guard(page)      (async, Playwright)
    install_route_guard_sync(page) (sync, Playwright)
    assert_safe_page_url(page)     (async, Playwright)
    DISABLE_WEBRTC_JS              (init-script string, Playwright)
    CHROMIUM_HARDENING_ARGS        (launch-args tuple, Playwright)
"""
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

# Best-effort WebRTC surface reduction (NOT a boundary): a text scraper needs no WebRTC,
# and page.route cannot intercept it (Chromium egresses it on its own socket). Installed
# as a pre-navigation init script by BOTH scraper paths (the playwright_tools async
# factory and the url_tools sync fallback) — defined once HERE so the two paths cannot
# drift. Paired with CHROMIUM_HARDENING_ARGS on the launch args. A fresh realm could
# re-obtain the deleted constructor, so the netns egress firewall (ops/egress_firewall.py)
# remains the actual boundary; this only shrinks the surface for the public-egress residual.
DISABLE_WEBRTC_JS = (
    "delete window.RTCPeerConnection;"
    "delete window.webkitRTCPeerConnection;"
    "delete window.RTCDataChannel;"
)

# The launch-args half of the same surface reduction, consumed by BOTH launch sites
# (playwright_tools async factory + url_tools sync fallback) so a hardening flag can
# never be added to one path only. QUIC would otherwise carry HTTP/3 on Chromium's own
# UDP socket, past page.route exactly like WebRTC.
CHROMIUM_HARDENING_ARGS = ("--disable-quic",)

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


# Firewall-dropped space (see ops/egress_firewall._V4_DROP) that CPython's address
# properties do NOT flag, so _is_blocked_ip needs explicit membership checks:
#   100.64.0.0/10 -- CGNAT / RFC 6598 shared space, IANA-listed as neither private
#     nor globally reachable, so is_private and is_reserved are both False.
#   192.0.0.0/24  -- is_private EXCEPT the two globally-reachable anycast carve-outs
#     CPython added in the post-CVE-2024-4032 IANA alignment: 192.0.0.9 (PCP,
#     RFC 7723) and 192.0.0.10 (TURN anycast, RFC 8155). A scraper has no
#     business at either, and the firewall drops the whole /24, so block it whole.
# Both directions of parity with the firewall (drop-implies-blocked, exhaustively
# for small ranges; and each entry here nests inside a drop range) are pinned by
# the structural tests in tests/test_egress_firewall.py.
_EXTRA_BLOCKED_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
)


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
    (incl. 169.254.169.254 cloud metadata), multicast, reserved, the
    unspecified address, or the ranges the ipaddress properties don't cover
    (``_EXTRA_BLOCKED_NETS``: CGNAT + the 192.0.0.0/24 anycast carve-outs).
    An unparseable value is treated as blocked."""
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
        or any(ip in net for net in _EXTRA_BLOCKED_NETS)
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
        # Sessions displaced by a TTL re-resolve, retained (NOT closed inline)
        # until close(): a session is returned under the lock but used outside it,
        # so a concurrent same-host fetch may still be mid-``session.get`` on the
        # one being displaced — closing it here would be a use-after-close (a
        # spurious fail-closed abort of a legitimate subresource). Per page +
        # bounded by page_lifetime/ttl per host, so retaining until close is cheap.
        self._stale = []

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
                # Retain, don't close: a concurrent fetch may still hold this
                # session (returned under the lock, used outside it). Closed in
                # close() at page teardown instead — see self._stale.
                self._stale.append(entry[1])
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
        """Close every cached session (its keep-alive sockets), including those
        displaced by a TTL re-resolve (``self._stale``). Called when the page
        closes, by which point no fetch is still borrowing a session."""
        with self._lock:
            sessions = [session for _ip, session, _exp in self._entries.values()]
            sessions.extend(self._stale)
            for session in sessions:
                try:
                    session.close()
                except Exception:
                    pass
            self._entries.clear()
            self._stale.clear()


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


def _should_skip_resource(route) -> bool:
    """True for in-browser subresource types we never need for text extraction
    (image/media/font by default — see ``_SKIP_RESOURCE_TYPES``). Aborting them
    cuts DNS+TLS work and egress with no effect on ``page.inner_text``; aborting
    more requests is also strictly less egress, so there is no SSRF downside."""
    try:
        return route.request.resource_type in _SKIP_RESOURCE_TYPES
    except Exception:
        return False


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


async def assert_safe_page_url(page) -> None:
    """After a goto/click settles, re-validate the page's CURRENT URL (it may
    have changed via a JS or meta redirect) and raise :class:`UnsafeURLError`
    if it now points at a blocked host."""
    validate_fetch_url(page.url)
