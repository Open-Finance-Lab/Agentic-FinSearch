"""Agent run budgeting backed by the Django default cache.

In production the default cache is ``django.core.cache.backends.redis.RedisCache``
(see ``django_config/settings_prod.py``), which provides atomic ``incr``/``decr``
shared across every gunicorn worker. That atomicity is what turns the agent
concurrency and daily-run caps (added in the budget task that builds on this
module) into HARD limits rather than best-effort, racy counters.

This module exposes the atomic counter primitive ``_incr`` and the
concurrency / daily-budget context manager ``agent_run_slot`` (with its
``BudgetExceeded`` / ``ConcurrencyExceeded`` exceptions), both layered on the
default cache.

Three independent ceilings are enforced per run, in a fixed order so a
*rejected* run can never burn daily budget:

  1. concurrency  — in-flight runs across the whole community. Stored under
     ``_INFLIGHT_TTL`` and refreshed on every acquire, so the counter never
     expires mid-stream (which would reseed it at 0 and drift the cap); a
     release that is skipped (crash, missed finally) still self-heals within
     the TTL instead of wedging the slot for ~26h.
  2. global daily — total runs community-wide for the current UTC day.
  3. per-identity — runs for one identity (``ip:<addr>`` today) for the day.
"""
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from django.core.cache import cache

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """A daily run ceiling (per-identity or global) was reached."""


class ConcurrencyExceeded(Exception):
    """Too many agent runs are in flight at once."""


# Read at module level so tests can monkeypatch the module attributes.
AGENT_MAX_CONCURRENCY = int(os.getenv("AGENT_MAX_CONCURRENCY", "3"))
AGENT_DAILY_RUN_BUDGET = int(os.getenv("AGENT_DAILY_RUN_BUDGET", "100"))
AGENT_GLOBAL_DAILY_CEILING = int(os.getenv("AGENT_GLOBAL_DAILY_CEILING", "2000"))

_INFLIGHT_KEY = "agent:inflight"
# Comfortably longer than any single agent run / SSE stream. Refreshed on every
# acquire (see agent_run_slot), so under load the in-flight counter never
# expires and reseeds mid-stream; a genuinely skipped release still self-heals
# within this window rather than wedging the slot for ~26h.
_INFLIGHT_TTL = 1800  # seconds (30 min)
_DAILY_TTL = 60 * 60 * 26  # ~26h, so a UTC-day counter outlives its day


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _incr(key: str, ttl: int) -> int:
    """Atomically increment the integer counter at ``key`` and return the new value.

    First-touch semantics: ``cache.add`` seeds the counter at ``0`` with a
    ``ttl``-second expiry (and is a no-op if the key already exists), then
    ``cache.incr`` bumps it. Both operations are atomic on RedisCache (prod) and
    on LocMemCache (single-process tests), so concurrent callers never lose a
    tick and the first caller always observes ``1``.

    We deliberately do NOT reset the key to ``1`` on the ``add``/``incr`` path:
    ``add`` guarantees the key exists before ``incr`` runs, and a
    reset-on-ValueError fallback would silently drop concurrent increments,
    defeating the hard-limit guarantee. The narrow ``ValueError`` fallback below
    only re-seeds at ``0`` and re-increments (never ``set(key, 1)``) for the rare
    race where the key is evicted between ``add`` and ``incr`` — so an
    evicted/expired key resumes from a correct floor rather than crashing.
    """
    cache.add(key, 0, ttl)
    try:
        return cache.incr(key)
    except ValueError:
        cache.add(key, 0, ttl)
        return cache.incr(key)


def _safe_decr(key: str):
    """Atomically decrement ``key`` and return the new value, swallowing the
    ``ValueError`` ``cache.decr`` raises when the key has expired/been evicted.

    Returns ``None`` when the key was already gone (nothing to release). Used on
    EVERY release/rollback path so a vanished counter degrades to a clean no-op
    instead of a 500 — e.g. a stream that outlived ``_INFLIGHT_TTL``, or a daily
    counter evicted between increment and rollback."""
    try:
        return cache.decr(key)
    except ValueError:
        return None


@contextmanager
def agent_run_slot(identity: str):
    """Reserve one agent run slot for ``identity`` or raise.

    Order is load-bearing: concurrency is checked FIRST, so a concurrency
    rejection never increments (burns) the daily counters. On any daily
    rejection the in-flight slot taken in step (1) is released before
    raising. The in-flight slot is always released on exit via ``finally``;
    the daily counters are NOT decremented on normal completion (they count
    runs/day, not in-flight work).
    """
    date = _utc_date()
    global_key = f"agent:runs:{date}"
    identity_key = f"agent:runs:{date}:{identity}"

    # (1) concurrency — checked before any daily counter is touched. Refresh
    # the key's TTL on every acquire so an active server never lets the
    # in-flight counter expire and reseed at 0 mid-stream (which would drift
    # the concurrency cap).
    inflight = _incr(_INFLIGHT_KEY, _INFLIGHT_TTL)
    cache.touch(_INFLIGHT_KEY, _INFLIGHT_TTL)
    if inflight > AGENT_MAX_CONCURRENCY:
        _safe_decr(_INFLIGHT_KEY)
        logger.warning(
            "agent_run_slot: concurrency limit hit (%s in flight, max %s)",
            inflight, AGENT_MAX_CONCURRENCY,
        )
        raise ConcurrencyExceeded(
            f"{inflight - 1} agent runs already in flight "
            f"(max {AGENT_MAX_CONCURRENCY})"
        )

    # (2) global daily ceiling.
    global_runs = _incr(global_key, _DAILY_TTL)
    if global_runs > AGENT_GLOBAL_DAILY_CEILING:
        # Roll back this rejected run's own global tick so the counter sits AT
        # the ceiling instead of inflating past it on every rejected attempt.
        _safe_decr(global_key)
        _safe_decr(_INFLIGHT_KEY)
        logger.warning(
            "agent_run_slot: global daily ceiling hit (%s, max %s)",
            global_runs, AGENT_GLOBAL_DAILY_CEILING,
        )
        raise BudgetExceeded(
            f"global daily ceiling reached (max {AGENT_GLOBAL_DAILY_CEILING})"
        )

    # (3) per-identity daily budget.
    identity_runs = _incr(identity_key, _DAILY_TTL)
    if identity_runs > AGENT_DAILY_RUN_BUDGET:
        # A rejected run must NOT burn the shared global ceiling — otherwise one
        # identity over its own budget could exhaust the community-wide ceiling
        # via rejected attempts (DoS amplification). Roll the global tick back.
        # The per-identity counter is intentionally left incremented: it counts
        # this identity's own attempts, which are already over budget.
        _safe_decr(global_key)
        _safe_decr(_INFLIGHT_KEY)
        logger.warning(
            "agent_run_slot: daily budget hit for %s (%s, max %s)",
            identity, identity_runs, AGENT_DAILY_RUN_BUDGET,
        )
        raise BudgetExceeded(
            f"daily run budget reached for {identity} "
            f"(max {AGENT_DAILY_RUN_BUDGET})"
        )

    try:
        yield
    finally:
        # Release the in-flight slot. _safe_decr is a no-op if the key already
        # expired (a stream that outlived the TTL) — nothing to release then.
        _safe_decr(_INFLIGHT_KEY)
