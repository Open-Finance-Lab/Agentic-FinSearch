"""Tests for api.identity (P0 Root C.1: trusted-proxy IP resolution +
rate-limit keying). SimpleTestCase, no DB.

Run: uv run python manage.py test tests.test_identity -v 2
"""
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django_ratelimit import ALL
from django_ratelimit.decorators import ratelimit

from api.identity import get_client_ip, get_request_identity, ratelimit_key


class GetClientIpTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_x_real_ip_ignored_from_non_proxy_peer(self):
        # A direct (non-proxy) client cannot spoof its IP via headers.
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = "203.0.113.99"
        req.META["HTTP_X_REAL_IP"] = "10.0.0.1"
        req.META["HTTP_X_FORWARDED_FOR"] = "10.0.0.2"
        self.assertEqual(get_client_ip(req), "203.0.113.99")

    def test_x_real_ip_honored_from_trusted_proxy(self):
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = "127.0.0.1"  # default TRUSTED_PROXIES
        req.META["HTTP_X_REAL_IP"] = "198.51.100.7"
        self.assertEqual(get_client_ip(req), "198.51.100.7")

    def test_xff_leftmost_when_no_real_ip(self):
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        req.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.8, 10.0.0.1, 127.0.0.1"
        self.assertEqual(get_client_ip(req), "198.51.100.8")


class IdentityFormatTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_identity_is_ip_prefixed(self):
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = "198.51.100.5"
        self.assertEqual(get_request_identity(req), "ip:198.51.100.5")

    def test_ratelimit_key_returns_identity(self):
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        req.META["HTTP_X_REAL_IP"] = "198.51.100.6"
        self.assertEqual(ratelimit_key("any-group", req), "ip:198.51.100.6")


class RatelimitKeyBucketTests(SimpleTestCase):
    """Behavioral: two different forwarded IPs from a trusted proxy land in
    SEPARATE rate-limit buckets (and the dotted-path key resolves through
    django-ratelimit's import_string)."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _proxied(self, real_ip):
        req = self.factory.get("/probe")
        req.META["REMOTE_ADDR"] = "127.0.0.1"  # trusted proxy
        req.META["HTTP_X_REAL_IP"] = real_ip
        return req

    def test_distinct_forwarded_ips_get_distinct_buckets(self):
        # NOTE: use the django_ratelimit.ALL sentinel (the tuple ``(None,)``),
        # NOT the string "ALL". In django-ratelimit 4.x, _method_match compares
        # ``method == ALL`` against that sentinel; passing the string "ALL"
        # matches NO HTTP method, so the limiter silently never engages. (The
        # production decorators still pass method='ALL' — see the concern raised
        # for this task; that is a separate, out-of-scope fix.)
        @ratelimit(
            key="api.identity.ratelimit_key",
            rate="1/m",
            method=ALL,
            block=False,
        )
        def probe(request):
            return HttpResponse("ok")

        a1 = self._proxied("203.0.113.10")
        a2 = self._proxied("203.0.113.10")
        probe(a1)
        probe(a2)
        self.assertFalse(a1.limited)   # first hit for client A
        self.assertTrue(a2.limited)    # client A exhausted its 1/m bucket

        b1 = self._proxied("203.0.113.20")
        probe(b1)
        self.assertFalse(b1.limited)   # client B has its own fresh bucket
