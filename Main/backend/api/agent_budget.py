"""Agent run budgeting backed by the Django default cache.

In production the default cache is ``django.core.cache.backends.redis.RedisCache``
(see ``django_config/settings_prod.py``), which provides atomic ``incr``/``decr``
shared across every gunicorn worker. That atomicity is what turns the agent
concurrency and daily-run caps (added in the budget task that builds on this
module) into HARD limits rather than best-effort, racy counters.

This module currently exposes the atomic counter primitive ``_incr``. The
concurrency / daily-budget context manager (``agent_run_slot``) and the
``BudgetExceeded`` / ``ConcurrencyExceeded`` exceptions are layered on top of
``_incr`` in a later task.
"""
from django.core.cache import cache


def _incr(key: str, ttl: int) -> int:
    """Atomically increment the integer counter at ``key`` and return the new value.

    First-touch semantics: ``cache.add`` seeds the counter at ``0`` with a
    ``ttl``-second expiry (and is a no-op if the key already exists), then
    ``cache.incr`` bumps it. Both operations are atomic on RedisCache (prod) and
    on LocMemCache (single-process tests), so concurrent callers never lose a
    tick and the first caller always observes ``1``.

    We deliberately do NOT wrap ``incr`` in ``try/except ValueError`` to reset
    the key to ``1``: ``add`` guarantees the key exists before ``incr`` runs, and
    a reset-on-ValueError fallback would silently drop concurrent increments,
    defeating the hard-limit guarantee.
    """
    cache.add(key, 0, ttl)
    return cache.incr(key)
