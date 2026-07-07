"""A rate-limited request must return HTTP 429 (+ Retry-After), not Django's default 403.

django-ratelimit's ``@ratelimit(block=True)`` raises ``Ratelimited``, a ``PermissionDenied``
subclass that Django renders as **403 Forbidden** when nothing intercepts it. 403 is the wrong
semantic for "you exceeded a rate limit" — the correct status is **429 Too Many Requests** with
a ``Retry-After`` so clients back off instead of treating it as an auth failure.

The fix wires ``django_ratelimit.middleware.RatelimitMiddleware`` +
``RATELIMIT_VIEW = 'api.views.ratelimited'``. This suite proves the behavior end-to-end:

  - ``Ratelimit429Tests`` drives a REAL request through the full middleware stack to a tight
    ``1/m`` limited view and asserts the second hit is 429 (would be 403 without the wiring).
  - ``RatelimitedViewUnitTests`` checks the view itself returns 429 + Retry-After.

Run: .venv/bin/python -m pytest tests/test_ratelimit_429.py -v
"""
from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import path
from django_ratelimit import ALL
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

import api.views as views_module
from tests.shared_settings import HERMETIC_REQUEST_SETTINGS


# Throwaway view decorated at a tight 1/m on the REAL production key function, so the second
# request from one trusted-proxy identity trips the limiter and exercises the actual wiring.
@ratelimit(key="api.identity.ratelimit_key", rate="1/m", method=ALL, block=True)
def _limited(request):
    return JsonResponse({"ok": True})


urlpatterns = [path("limited/", _limited)]


RL_429 = override_settings(
    RATELIMIT_ENABLE=True,
    ROOT_URLCONF=__name__,
    # These tests drive a REAL request through the full middleware stack, so they need the
    # shared hermeticity knobs (ALLOWED_HOSTS + SECURE_SSL_REDIRECT off — see conftest.py):
    # we're exercising the rate-limit wiring, not DisallowedHost or the 301-trap.
    **HERMETIC_REQUEST_SETTINGS,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ratelimit-429-tests",
        }
    },
)


@RL_429
class Ratelimit429Tests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _get(self, real_ip="198.51.100.7"):
        # REMOTE_ADDR=127.0.0.1 is a default TRUSTED_PROXY, so get_client_ip() honors X-Real-IP
        # and both hits land in one identity bucket.
        return self.client.get("/limited/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP=real_ip)

    def test_rate_limited_request_returns_429_with_retry_after(self):
        first = self._get()
        self.assertEqual(first.status_code, 200)

        second = self._get()
        self.assertEqual(
            second.status_code, 429,
            "a rate-limited request must be 429 Too Many Requests, not 403 Forbidden",
        )
        self.assertTrue(second.has_header("Retry-After"))

    def test_distinct_identities_are_not_collaterally_limited(self):
        self._get(real_ip="203.0.113.10")
        self.assertEqual(self._get(real_ip="203.0.113.10").status_code, 429)
        # A different identity keeps its own fresh bucket.
        self.assertEqual(self._get(real_ip="203.0.113.11").status_code, 200)


class RatelimitedViewUnitTests(SimpleTestCase):
    def test_view_returns_429_and_retry_after(self):
        req = RequestFactory().get("/x")
        resp = views_module.ratelimited(req, Ratelimited())
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(resp.has_header("Retry-After"))
        self.assertGreaterEqual(int(resp["Retry-After"]), 1)
