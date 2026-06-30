"""Client identity + rate-limit keying for the FinSearch API.

SECURITY (P0 Root C.1): a direct client must never be able to spoof its own
IP via X-Real-IP / X-Forwarded-For. We therefore only trust those headers
when the immediate TCP peer (REMOTE_ADDR) is a configured reverse proxy
(settings.TRUSTED_PROXIES); otherwise we use REMOTE_ADDR itself.

TRUSTED_PROXIES entries are matched as IP *networks*, not exact strings: a
bare IP is a host route (/32 or /128) and a CIDR (e.g. ``10.89.0.0/24``) trusts
the whole range. This matters under rootless Podman, which SNATs the
host->container hop so REMOTE_ADDR is the (dynamic) podman-network address
rather than the literal proxy IP -- an exact-string compare would never match
it, collapsing every caller into one rate-limit bucket and ignoring X-Real-IP.
"""
import ipaddress
import logging
from functools import lru_cache

from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)


def _valid_ip(value: str) -> bool:
    """True when value parses as a literal IPv4/IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


@lru_cache(maxsize=8)
def _trusted_networks(trusted: tuple) -> tuple:
    """Parse a TRUSTED_PROXIES tuple into ip_network objects, once per config.

    A bare IP becomes a host route (/32 or /128); a CIDR is honored as-is. An
    unparseable entry is skipped -- it can never match, so a typo fails safe
    instead of poisoning the whole list. A default route (``0.0.0.0/0`` or
    ``::/0``) is REFUSED: it would trust every peer and let any direct client
    forge X-Real-IP, defeating the whole control -- use the narrowest covering
    CIDR instead. Cached on the (hashable) settings tuple, so we parse once
    rather than per request while still honoring override_settings (a different
    tuple is a different cache key).
    """
    networks = []
    for entry in trusted:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if net.prefixlen == 0:
            logger.warning(
                "Ignoring TRUSTED_PROXIES entry %r: a default route would trust "
                "every peer. Use the narrowest covering CIDR (e.g. the podman "
                "network subnet), never 0.0.0.0/0 or ::/0.",
                entry,
            )
            continue
        networks.append(net)
    return tuple(networks)


def _is_trusted_proxy(remote_addr: str, trusted) -> bool:
    """True when remote_addr falls inside any trusted-proxy network.

    A blank or non-IP remote_addr is never trusted, so a peer we cannot place
    on the network can never gain header trust (and a malformed value cannot
    raise out of the request path).
    """
    if not remote_addr:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    # On a dual-stack listener an IPv4 peer can arrive as ::ffff:a.b.c.d; test
    # the unwrapped IPv4 too so a v4 CIDR still matches it. (ip_address-in-
    # ip_network is False across families, so without this the SNAT proxy would
    # read as untrusted and collapse every caller into one rate-limit bucket.)
    candidates = [addr]
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        candidates.append(mapped)
    nets = _trusted_networks(tuple(trusted))
    return any(c in net for c in candidates for net in nets)


def get_client_ip(request: HttpRequest) -> str:
    """Return the best-effort client IP.

    If REMOTE_ADDR is a trusted proxy, honor X-Real-IP, then the leftmost
    entry of X-Forwarded-For. Otherwise return REMOTE_ADDR unchanged so a
    non-proxy peer cannot forge its address.

    A forwarded value is only used when it is itself a valid IP literal, so a
    proxy that appends (rather than replaces) X-Forwarded-For -- or a forged
    non-IP token -- cannot mint an arbitrary rate-limit key; we fall back to the
    real peer instead.
    """
    remote_addr = request.META.get("REMOTE_ADDR", "") or ""
    trusted = getattr(settings, "TRUSTED_PROXIES", ())
    if _is_trusted_proxy(remote_addr, trusted):
        real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
        if real_ip and _valid_ip(real_ip):
            return real_ip
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
            if _valid_ip(candidate):
                return candidate
    return remote_addr


def get_request_identity(request: HttpRequest) -> str:
    """Stable identity string for rate limiting and budgeting.

    Today this is always ``ip:<client_ip>``; a future authenticated path can
    return ``user:<id>`` without changing callers.
    """
    return f"ip:{get_client_ip(request)}"


def ratelimit_key(group: str, request: HttpRequest) -> str:
    """django-ratelimit key callable (dotted-path target).

    Wired via ``@ratelimit(key='api.identity.ratelimit_key', ...)``.
    """
    return get_request_identity(request)
