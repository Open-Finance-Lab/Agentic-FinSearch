"""Client identity + rate-limit keying for the FinSearch API.

SECURITY (P0 Root C.1): a direct client must never be able to spoof its own
IP via X-Real-IP / X-Forwarded-For. We therefore only trust those headers
when the immediate TCP peer (REMOTE_ADDR) is a configured reverse proxy
(settings.TRUSTED_PROXIES); otherwise we use REMOTE_ADDR itself.
"""
from django.conf import settings
from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    """Return the best-effort client IP.

    If REMOTE_ADDR is a trusted proxy, honor X-Real-IP, then the leftmost
    entry of X-Forwarded-For. Otherwise return REMOTE_ADDR unchanged so a
    non-proxy peer cannot forge its address.
    """
    remote_addr = request.META.get("REMOTE_ADDR", "") or ""
    trusted = getattr(settings, "TRUSTED_PROXIES", ())
    if remote_addr in trusted:
        real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
        if real_ip:
            return real_ip
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
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
