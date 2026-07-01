"""Tests for api.identity (P0 Root C.1: trusted-proxy IP resolution +
rate-limit keying). SimpleTestCase, no DB.

Run: uv run python manage.py test tests.test_identity -v 2
"""
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django_ratelimit import ALL
from django_ratelimit.decorators import ratelimit

from api.identity import (
    _trusted_networks,
    get_client_ip,
    get_request_identity,
    ratelimit_key,
)


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


class TrustedProxyCidrTests(SimpleTestCase):
    """P0 Root C.1 hardening: TRUSTED_PROXIES entries are matched as IP networks,
    not exact strings.

    Rootless Podman SNATs the host->container hop, so REMOTE_ADDR inside the
    container is the (dynamic) podman-network address, NOT the literal proxy IP.
    Exact-string membership therefore never matches a /24, collapsing every
    caller into one rate-limit bucket and silently ignoring X-Real-IP. Matching
    by ip_network fixes that while staying backward-compatible with bare IPs.
    """

    def setUp(self):
        self.factory = RequestFactory()
        # _trusted_networks is lru_cache'd and emits the default-route (/0)
        # warning only on a cache MISS. That cache persists across tests in the
        # same process, so if any earlier test parsed the same TRUSTED_PROXIES
        # tuple first, the warm entry would suppress the warning and make the
        # assertLogs cases below fail in a way that depends on suite ordering.
        # Clear it so every test starts from a cold, deterministic cache, and
        # register a cleanup so we also leave it cold on EXIT -- the warn-once
        # test below ends with a warm ('0.0.0.0/0',) entry, and addCleanup keeps
        # this class from leaking that suppression to any later cross-module test
        # even if an assertion raises mid-test.
        _trusted_networks.cache_clear()
        self.addCleanup(_trusted_networks.cache_clear)

    def _req(self, remote_addr, real_ip="198.51.100.7"):
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = remote_addr
        req.META["HTTP_X_REAL_IP"] = real_ip
        return req

    @override_settings(TRUSTED_PROXIES=("10.89.0.0/24",))
    def test_cidr_proxy_inside_range_is_trusted(self):
        # The SNAT address 10.89.0.37 lies inside the configured /24, so the
        # real client (X-Real-IP) must be honored even though 10.89.0.37 is not
        # a literal TRUSTED_PROXIES entry. (Driver for the fix.)
        self.assertEqual(get_client_ip(self._req("10.89.0.37")), "198.51.100.7")

    @override_settings(TRUSTED_PROXIES=("10.89.0.0/24",))
    def test_address_outside_cidr_is_not_trusted(self):
        # 10.90.0.1 is OUTSIDE the /24 -> headers ignored, REMOTE_ADDR returned.
        self.assertEqual(get_client_ip(self._req("10.90.0.1")), "10.90.0.1")

    @override_settings(TRUSTED_PROXIES=("127.0.0.1",))
    def test_bare_ip_still_matches_exactly(self):
        # Backward compat: a bare IP behaves like a /32 host route.
        self.assertEqual(get_client_ip(self._req("127.0.0.1")), "198.51.100.7")
        self.assertEqual(get_client_ip(self._req("127.0.0.2")), "127.0.0.2")

    @override_settings(TRUSTED_PROXIES=("10.89.0.0/24",))
    def test_malformed_remote_addr_is_not_trusted(self):
        # A non-IP REMOTE_ADDR must never raise and must be treated as untrusted.
        self.assertEqual(get_client_ip(self._req("not-an-ip")), "not-an-ip")

    @override_settings(TRUSTED_PROXIES=("::1/128",))
    def test_family_mismatch_is_not_trusted(self):
        # An IPv4 peer cannot match an IPv6-only trusted network (and no crash).
        self.assertEqual(get_client_ip(self._req("203.0.113.9")), "203.0.113.9")

    @override_settings(TRUSTED_PROXIES=("10.89.0.0/24", "::1"))
    def test_mixed_v4_cidr_and_v6_host(self):
        # A v4 address inside the CIDR is trusted...
        self.assertEqual(get_client_ip(self._req("10.89.0.5")), "198.51.100.7")
        # ...and so is the v6 loopback host route.
        self.assertEqual(get_client_ip(self._req("::1")), "198.51.100.7")

    @override_settings(TRUSTED_PROXIES=("garbage", "10.89.0.0/24"))
    def test_unparseable_entry_is_skipped_not_fatal(self):
        # A malformed TRUSTED_PROXIES entry must not poison the whole list: the
        # valid CIDR alongside it still works.
        self.assertEqual(get_client_ip(self._req("10.89.0.9")), "198.51.100.7")

    @override_settings(TRUSTED_PROXIES=("10.89.0.0/24",))
    def test_ipv4_mapped_ipv6_peer_matches_v4_cidr(self):
        # A dual-stack listener may report the SNAT peer as ::ffff:10.89.0.37.
        # It must still be recognized as the in-range proxy, else per-client
        # rate limiting silently collapses back into one bucket.
        self.assertEqual(get_client_ip(self._req("::ffff:10.89.0.37")), "198.51.100.7")

    @override_settings(TRUSTED_PROXIES=("0.0.0.0/0",))
    def test_ipv4_default_route_is_never_trusted(self):
        # Trusting the whole internet would let ANY direct client forge
        # X-Real-IP -> a /0 entry must be refused (and the refusal logged).
        with self.assertLogs("api.identity", level="WARNING") as logs:
            self.assertEqual(get_client_ip(self._req("203.0.113.5")), "203.0.113.5")
        self.assertTrue(any("0.0.0.0/0" in m for m in logs.output))

    @override_settings(TRUSTED_PROXIES=("::/0",))
    def test_ipv6_default_route_is_never_trusted(self):
        with self.assertLogs("api.identity", level="WARNING") as logs:
            self.assertEqual(get_client_ip(self._req("2001:db8::1")), "2001:db8::1")
        self.assertTrue(any("::/0" in m for m in logs.output))

    @override_settings(TRUSTED_PROXIES=("10.0.0.0/0",))  # normalizes to 0.0.0.0/0
    def test_default_route_warning_does_not_echo_raw_entry(self):
        # The /0 refusal must NOT log the raw config value (CodeQL
        # py/clear-text-logging-sensitive-data taints settings reads). A static
        # message is enough -- here the entry "10.0.0.0/0" must not appear.
        with self.assertLogs("api.identity", level="WARNING") as logs:
            self.assertEqual(get_client_ip(self._req("203.0.113.5")), "203.0.113.5")
        self.assertFalse(any("10.0.0.0/0" in m for m in logs.output))

    def test_default_route_warning_is_warn_once_until_cache_cleared(self):
        # Documents the lru_cache warn-once contract that setUp's cache_clear()
        # depends on: the /0 warning fires on the first parse of a tuple, is
        # SUPPRESSED on the cached re-parse, and fires again after a clear. This
        # is exactly why a warm cache from another test could otherwise swallow
        # the assertLogs warnings above -- guards against someone making the
        # warning unconditional or removing the warn-once behavior.
        with self.assertLogs("api.identity", level="WARNING"):
            _trusted_networks(("0.0.0.0/0",))            # cold miss -> warns
        with self.assertNoLogs("api.identity", level="WARNING"):
            _trusted_networks(("0.0.0.0/0",))            # cache hit -> silent
        _trusted_networks.cache_clear()
        with self.assertLogs("api.identity", level="WARNING"):
            _trusted_networks(("0.0.0.0/0",))            # cold again -> warns

    @override_settings(TRUSTED_PROXIES=("127.0.0.1",))
    def test_non_ip_x_real_ip_falls_back_to_remote_addr(self):
        # A trusted proxy that forwards a non-IP X-Real-IP must not have that
        # arbitrary string become the rate-limit key; fall back to the peer.
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        req.META["HTTP_X_REAL_IP"] = "not-an-ip"
        self.assertEqual(get_client_ip(req), "127.0.0.1")

    @override_settings(TRUSTED_PROXIES=("127.0.0.1",))
    def test_forged_non_ip_leftmost_xff_falls_back(self):
        # A garbage leftmost X-Forwarded-For token (e.g. an append-not-replace
        # proxy or a forged value) must not become an arbitrary bucket key.
        req = self.factory.get("/x")
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        req.META["HTTP_X_FORWARDED_FOR"] = "garbage, 203.0.113.1"
        self.assertEqual(get_client_ip(req), "127.0.0.1")


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
        # Reset the module's other process-global cache for symmetry with
        # cache.clear() above. This class asserts no logs and uses the default
        # (/32, /128) TRUSTED_PROXIES, so warm vs cold is observationally
        # identical here -- isolation hygiene, not load-bearing.
        _trusted_networks.cache_clear()
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
        # matches NO HTTP method, so the limiter silently never engages.
        # Production gets this right: api/views.py and api/openai_views.py both
        # `from django_ratelimit import ALL` and pass `method=ALL` (the sentinel,
        # unquoted), so the live decorators DO engage -- this test just guards
        # against a future regression to the string form.
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
