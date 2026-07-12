"""Shared bearer-token auth for FinSearch HTTP endpoints.

Single source of truth extracted from openai_views so /v1 and the non-/v1
routes cannot drift. See Docs/source/api_reference.rst (Authentication) and
django_config/settings.py:179-180. The shared FINGPT_API_KEY is a coarse gate;
per-user attribution is deferred to the identity seam (api/identity.py)."""
import functools
import hmac
import logging
import os
from typing import Optional

from django.conf import settings
from django.http import HttpRequest, JsonResponse

logger = logging.getLogger(__name__)


def authenticate_request(request: HttpRequest) -> Optional[JsonResponse]:
    api_key = os.getenv('FINGPT_API_KEY')
    if not api_key:
        if getattr(settings, 'REQUIRE_FINGPT_API_KEY', False):
            logger.error(
                "FINGPT_API_KEY is not set but REQUIRE_FINGPT_API_KEY is True; "
                "refusing request (fail closed)."
            )
            return JsonResponse(
                {'error': {'message': 'Server authentication is misconfigured.', 'type': 'server_error'}},
                status=503,
            )
        return None
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header:
        return JsonResponse(
            {'error': {'message': 'Missing Authorization header. Use: Authorization: Bearer <api_key>', 'type': 'authentication_error'}},
            status=401,
        )
    if not auth_header.startswith('Bearer '):
        return JsonResponse(
            {'error': {'message': 'Invalid Authorization format. Use: Authorization: Bearer <api_key>', 'type': 'authentication_error'}},
            status=401,
        )
    if not hmac.compare_digest(auth_header[7:], api_key):
        return JsonResponse(
            {'error': {'message': 'Invalid API key', 'type': 'authentication_error'}},
            status=401,
        )
    return None


def require_bearer_auth(view_func):
    """Gate a view with authenticate_request. Placed as the OUTERMOST decorator
    (just under @csrf_exempt) so an unauthorized request is rejected before any
    rate-limit token, disk load, or agent work — a deliberate improvement over
    the /v1 in-body check, same policy."""
    @functools.wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        auth_error = authenticate_request(request)
        if auth_error is not None:
            return auth_error
        return view_func(request, *args, **kwargs)
    return _wrapped
