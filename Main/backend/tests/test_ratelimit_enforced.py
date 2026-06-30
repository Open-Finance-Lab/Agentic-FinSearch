"""Behavioral proof that the production @ratelimit decorators ACTUALLY FIRE.

P0 Root C: in django-ratelimit 4.1.0 ``django_ratelimit.ALL`` is the sentinel
tuple ``(None,)`` and ``core._method_match`` does ``method == ALL``. Passing the
*string* ``'ALL'`` never matches that sentinel, so ``get_usage()`` returns
``None`` and the limiter NEVER engages — every ``@ratelimit(..., method='ALL')``
decorator is a silent no-op.

This suite exercises the REAL production view ``has_axiom_claims`` (a cheap GET
wired with the production ``@ratelimit`` decorator) from a single trusted-proxy
identity. Because the decorator captures ``rate=settings.API_RATE_LIMIT`` at
import time, we reload ``api.views`` under ``override_settings(API_RATE_LIMIT=
'1/m')`` so the production decorator re-binds at a tight rate while keeping its
real ``key=`` and ``method=`` arguments. The limiter is then driven to its limit:

  - method='ALL' string bug present  -> _method_match False -> get_usage None
    -> limiter never engages -> second request NOT blocked (RED).
  - method=django_ratelimit.ALL sentinel -> _method_match True -> limiter
    engages -> second request raises Ratelimited (GREEN).

Run: uv run pytest tests/test_ratelimit_enforced.py -v
"""
import importlib

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings
from django_ratelimit.exceptions import Ratelimited

import api.views as views_module

# Tight per-identity limit + an in-process LocMemCache so rate-limit counters
# are deterministic and isolated from the base FileBasedCache.
RL_OVERRIDES = override_settings(
    RATELIMIT_ENABLE=True,
    API_RATE_LIMIT="1/m",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "ratelimit-enforced-tests",
        }
    },
)


@RL_OVERRIDES
class RatelimitEnforcedTests(SimpleTestCase):
    """The production limiter must engage on the SECOND request from one id."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Re-import api.views with API_RATE_LIMIT='1/m' in effect so the
        # @ratelimit decorators re-bind rate='1/m'. This re-runs the real
        # production decorator wiring (including its key= and method=
        # arguments), so the method fix is genuinely under test.
        cls._reloaded = importlib.reload(views_module)
        cls.has_axiom_claims = staticmethod(cls._reloaded.has_axiom_claims)

    @classmethod
    def tearDownClass(cls):
        # Restore the module to its real (un-overridden) rate for the rest of
        # the suite so we don't leak a 1/m limiter into other tests.
        importlib.reload(views_module)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _trusted_proxy_get(self, real_ip="198.51.100.42"):
        # REMOTE_ADDR=127.0.0.1 is a default TRUSTED_PROXY, so get_client_ip()
        # honors X-Real-IP and both requests land in the same identity bucket.
        #
        # Pass an explicit session_id so the view body short-circuits before
        # touching request.session (RequestFactory adds no SessionMiddleware).
        # get_claims() on an unknown session returns [] -> a clean 200, leaving
        # the rate limiter as the only thing that can change the outcome.
        req = self.factory.get(
            "/api/axioms/has_claims/", {"session_id": "ratelimit-probe"}
        )
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        req.META["HTTP_X_REAL_IP"] = real_ip
        return req

    def test_second_request_from_same_identity_is_rate_limited(self):
        # First request from this identity: allowed (consumes the 1/m bucket).
        first = self._trusted_proxy_get()
        resp = self.has_axiom_claims(first)
        self.assertFalse(
            getattr(first, "limited", False),
            "first request must NOT be limited (fresh 1/m bucket)",
        )
        self.assertEqual(resp.status_code, 200)

        # Second request from the SAME identity within the window: the limiter
        # MUST fire. With block=True the decorator raises Ratelimited. With the
        # method='ALL' string bug the limiter is a no-op and this returns 200.
        second = self._trusted_proxy_get()
        with self.assertRaises(
            Ratelimited,
            msg="second request was NOT blocked -> limiter is a no-op "
            "(method='ALL' string never matches the django_ratelimit.ALL "
            "sentinel)",
        ):
            self.has_axiom_claims(second)
        self.assertTrue(
            getattr(second, "limited", False),
            "request.limited must be True once the bucket is exhausted",
        )

    def test_distinct_identities_keep_independent_buckets(self):
        # Sanity: a *different* identity is not collateral-damaged by the first
        # client's exhausted bucket — proves the limiter keys on identity, not
        # globally, once it actually engages.
        a1 = self._trusted_proxy_get(real_ip="203.0.113.1")
        self.has_axiom_claims(a1)
        a2 = self._trusted_proxy_get(real_ip="203.0.113.1")
        with self.assertRaises(Ratelimited):
            self.has_axiom_claims(a2)

        # Fresh identity -> fresh bucket -> allowed.
        b1 = self._trusted_proxy_get(real_ip="203.0.113.2")
        resp = self.has_axiom_claims(b1)
        self.assertFalse(getattr(b1, "limited", False))
        self.assertEqual(resp.status_code, 200)
