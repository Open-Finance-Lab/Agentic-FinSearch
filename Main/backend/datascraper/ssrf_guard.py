"""
SSRF guard for all outbound fetches and in-browser (Playwright) navigations.

Single chokepoint for P0 Root B.1: every URL the agent fetches or browses must
(a) use http/https, (b) have a host, and (c) resolve ONLY to publicly-routable
IPs. The connection is pinned to the validated IP so a DNS-rebinding answer
between validation and connect cannot redirect us to a private address, and the
response body is byte-capped to defeat huge-response resource exhaustion.

Public contract (do not rename):
    UnsafeURLError
    validate_fetch_url(url) -> str
    safe_get(url, headers=None, timeout=15, max_bytes=MAX_FETCH_BYTES,
             max_redirects=MAX_REDIRECTS) -> requests.Response
    install_route_guard(page)      (async, Playwright)
    assert_safe_page_url(page)     (async, Playwright)
"""
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


async def install_route_guard(page) -> None:
    """Register a Playwright route handler on ALL URLs that aborts any request
    (top-level navigation OR subresource) whose host resolves to a blocked IP.
    MUST be called BEFORE the first ``page.goto`` in every Playwright entrypoint
    so EVERY in-browser navigation/subresource is re-validated, not just the
    seed URL."""

    async def _handler(route):
        request_url = route.request.url
        try:
            validate_fetch_url(request_url)
        except UnsafeURLError as exc:
            logger.warning(
                "[ssrf_guard] aborting in-browser request to %s: %s",
                request_url,
                exc,
            )
            await route.abort()
            return
        await route.continue_()

    await page.route("**/*", _handler)


async def assert_safe_page_url(page) -> None:
    """After a goto/click settles, re-validate the page's CURRENT URL (it may
    have changed via a JS or meta redirect) and raise :class:`UnsafeURLError`
    if it now points at a blocked host."""
    validate_fetch_url(page.url)
