"""Unit tests for the Redis-backed atomic counter primitive ``api.agent_budget._incr``.

These tests run WITHOUT a live Redis. We override the default cache to
``LocMemCache``, whose ``incr`` is atomic within a single process — the same
contract ``RedisCache`` provides across processes — so the add-then-incr logic
is exercised faithfully. ``LiveRedisIntegrationTests`` documents and (only when
explicitly enabled) executes the same checks against a real broker.

Run (no live redis needed):
    cd Main/backend && uv run python manage.py test tests.test_agent_budget_redis -v 2

Run the live-redis integration check (needs `docker compose up -d redis`, or any
reachable broker):
    cd Main/backend && RUN_REDIS_INTEGRATION=1 REDIS_URL=redis://localhost:6379/0 \
        uv run python manage.py test \
        tests.test_agent_budget_redis.LiveRedisIntegrationTests -v 2
"""
import os
from unittest import skipUnless

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from api.agent_budget import _incr

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "agent-budget-incr-tests",
    }
}

REDIS_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    }
}


@override_settings(CACHES=LOCMEM_CACHE)
class IncrAtomicCounterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_first_incr_returns_one(self):
        self.assertEqual(_incr("agent:runs:2026-06-29", 60), 1)

    def test_sequential_incr_accumulates(self):
        self.assertEqual(_incr("agent:inflight", 300), 1)
        self.assertEqual(_incr("agent:inflight", 300), 2)
        self.assertEqual(_incr("agent:inflight", 300), 3)

    def test_existing_counter_is_not_reset_to_one(self):
        _incr("agent:runs:2026-06-29", 60)
        _incr("agent:runs:2026-06-29", 60)
        self.assertEqual(cache.get("agent:runs:2026-06-29"), 2)
        # A third call keeps climbing instead of snapping back to 1 (the bug the
        # spec forbids: do NOT reset to 1 on the add/incr path).
        self.assertEqual(_incr("agent:runs:2026-06-29", 60), 3)

    def test_distinct_keys_are_independent(self):
        self.assertEqual(_incr("agent:runs:2026-06-29", 60), 1)
        self.assertEqual(_incr("agent:runs:2026-06-29:ip:1.2.3.4", 60), 1)
        self.assertEqual(_incr("agent:runs:2026-06-29", 60), 2)

    def test_active_backend_supports_atomic_incr(self):
        # Locks the precondition: the default backend must implement a real
        # atomic incr (RedisCache and LocMemCache do; DummyCache would not).
        cache.add("agent:probe", 0, 60)
        self.assertEqual(cache.incr("agent:probe"), 1)


@skipUnless(
    os.getenv("RUN_REDIS_INTEGRATION") == "1",
    "live-redis check: set RUN_REDIS_INTEGRATION=1 with a reachable REDIS_URL",
)
@override_settings(CACHES=REDIS_CACHE)
class LiveRedisIntegrationTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_incr_is_atomic_on_redis(self):
        self.assertEqual(_incr("agent:it:counter", 60), 1)
        self.assertEqual(_incr("agent:it:counter", 60), 2)
        self.assertEqual(cache.get("agent:it:counter"), 2)
