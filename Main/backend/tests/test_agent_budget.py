"""Tests for api.agent_budget — Redis-backed agent run budgeting (P0 Root C.2).

These run against Django's cache via @override_settings, swapping in
LocMemCache. fakeredis is NOT a dependency of this project, so we use
LocMemCache, whose incr/decr are lock-guarded (atomic within the process).
That faithfully exercises the contextmanager's ORDERING and SELF-HEALING
semantics — concurrency-before-daily, decrement-on-reject, release-on-exit,
and the add+incr (never set-to-1) counter discipline. The identical
cache.add / cache.incr / cache.decr calls are atomic server-side on
RedisCache in production, where true multi-worker atomicity is provided by
Redis.
"""
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from api import agent_budget


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "agent-budget-tests",
        }
    }
)
class AgentBudgetTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_exception_types(self):
        self.assertTrue(issubclass(agent_budget.BudgetExceeded, Exception))
        self.assertTrue(issubclass(agent_budget.ConcurrencyExceeded, Exception))

    def test_incr_self_heals_after_eviction(self):
        self.assertEqual(agent_budget._incr("agent:probe", 300), 1)
        self.assertEqual(agent_budget._incr("agent:probe", 300), 2)
        # Simulate a MAX_ENTRIES / TTL eviction of a live counter.
        cache.delete("agent:probe")
        # Re-adds at 0 then incrs (never set-to-1); resumes cleanly.
        self.assertEqual(agent_budget._incr("agent:probe", 300), 1)

    def test_concurrency_reject_does_not_burn_daily_and_releases_inflight(self):
        with mock.patch.multiple(
            agent_budget,
            AGENT_MAX_CONCURRENCY=1,
            AGENT_DAILY_RUN_BUDGET=100,
            AGENT_GLOBAL_DAILY_CEILING=2000,
        ):
            ident = "ip:1.2.3.4"
            with agent_budget.agent_run_slot(ident):
                date = agent_budget._utc_date()
                gkey = f"agent:runs:{date}"
                ikey = f"agent:runs:{date}:{ident}"
                self.assertEqual(cache.get("agent:inflight"), 1)
                self.assertEqual(cache.get(gkey), 1)
                self.assertEqual(cache.get(ikey), 1)
                # A second concurrent run is rejected on concurrency FIRST.
                with self.assertRaises(agent_budget.ConcurrencyExceeded):
                    with agent_budget.agent_run_slot(ident):
                        pass
                # The rejected run did NOT touch the daily counters...
                self.assertEqual(cache.get(gkey), 1)
                self.assertEqual(cache.get(ikey), 1)
                # ...and decremented inflight back to the slot we still hold.
                self.assertEqual(cache.get("agent:inflight"), 1)
            # Slot released on normal exit.
            self.assertEqual(cache.get("agent:inflight"), 0)

    def test_per_identity_budget_exceeded_releases_inflight(self):
        with mock.patch.multiple(
            agent_budget,
            AGENT_MAX_CONCURRENCY=3,
            AGENT_DAILY_RUN_BUDGET=2,
            AGENT_GLOBAL_DAILY_CEILING=2000,
        ):
            ident = "ip:5.6.7.8"
            for _ in range(2):
                with agent_budget.agent_run_slot(ident):
                    pass
            date = agent_budget._utc_date()
            ikey = f"agent:runs:{date}:{ident}"
            self.assertEqual(cache.get(ikey), 2)
            self.assertEqual(cache.get("agent:inflight"), 0)
            with self.assertRaises(agent_budget.BudgetExceeded):
                with agent_budget.agent_run_slot(ident):
                    pass
            # Inflight slot released even though the daily run was rejected.
            self.assertEqual(cache.get("agent:inflight"), 0)
            # Per-identity counter reflects the rejected incr (never reset).
            self.assertEqual(cache.get(ikey), 3)

    def test_global_ceiling_rejects_before_per_identity(self):
        with mock.patch.multiple(
            agent_budget,
            AGENT_MAX_CONCURRENCY=3,
            AGENT_DAILY_RUN_BUDGET=100,
            AGENT_GLOBAL_DAILY_CEILING=2,
        ):
            for ident in ("ip:1.1.1.1", "ip:2.2.2.2"):
                with agent_budget.agent_run_slot(ident):
                    pass
            date = agent_budget._utc_date()
            gkey = f"agent:runs:{date}"
            self.assertEqual(cache.get(gkey), 2)
            fresh = "ip:9.9.9.9"
            fresh_key = f"agent:runs:{date}:{fresh}"
            with self.assertRaises(agent_budget.BudgetExceeded):
                with agent_budget.agent_run_slot(fresh):
                    pass
            # Global reject happens BEFORE the per-identity incr.
            self.assertIsNone(cache.get(fresh_key))
            self.assertEqual(cache.get("agent:inflight"), 0)

    def test_release_on_exit_and_never_reset_to_one(self):
        with mock.patch.multiple(
            agent_budget,
            AGENT_MAX_CONCURRENCY=3,
            AGENT_DAILY_RUN_BUDGET=100,
            AGENT_GLOBAL_DAILY_CEILING=2000,
        ):
            ident = "ip:4.4.4.4"
            date = agent_budget._utc_date()
            gkey = f"agent:runs:{date}"
            ikey = f"agent:runs:{date}:{ident}"
            # Pre-seed accumulated usage a healthy counter already carries.
            cache.set(gkey, 50, 60 * 60 * 26)
            cache.set(ikey, 7, 60 * 60 * 26)
            with agent_budget.agent_run_slot(ident):
                # Existing counters INCREMENT; they are never reset to 1.
                self.assertEqual(cache.get(gkey), 51)
                self.assertEqual(cache.get(ikey), 8)
                self.assertEqual(cache.get("agent:inflight"), 1)
            self.assertEqual(cache.get("agent:inflight"), 0)
            # A second run keeps accumulating.
            with agent_budget.agent_run_slot(ident):
                self.assertEqual(cache.get(gkey), 52)
                self.assertEqual(cache.get(ikey), 9)
            self.assertEqual(cache.get("agent:inflight"), 0)
