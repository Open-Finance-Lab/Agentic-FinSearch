import asyncio
import pytest
from concierge.ratelimit import InFlightGuard, QueueFullError


@pytest.mark.asyncio
async def test_serializes_same_user():
    order = []
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=5)
    async def job(tag):
        order.append(("start", tag)); await asyncio.sleep(0.01); order.append(("end", tag))
    await asyncio.gather(
        guard.run("u1", lambda: job("a")),
        guard.run("u1", lambda: job("b")),
    )
    # Same user: second job must not start before the first ends (queued).
    assert order in (
        [("start","a"),("end","a"),("start","b"),("end","b")],
        [("start","b"),("end","b"),("start","a"),("end","a")],
    )


@pytest.mark.asyncio
async def test_depth_cap_rejects():
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=1)
    async def slow(): await asyncio.sleep(0.05)
    running = asyncio.create_task(guard.run("u1", slow))   # holds the slot
    await asyncio.sleep(0.005)
    with pytest.raises(QueueFullError):                     # 2nd waiter exceeds cap=1
        await asyncio.gather(
            guard.run("u1", slow),
            guard.run("u1", slow),
        )
    await running


@pytest.mark.asyncio
async def test_other_users_not_blocked():
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=5)
    started = []
    async def job(tag): started.append(tag); await asyncio.sleep(0.02)
    await asyncio.gather(guard.run("u1", lambda: job("a")),
                         guard.run("u2", lambda: job("b")))
    assert set(started) == {"a", "b"}


@pytest.mark.asyncio
async def test_idle_state_evicted_after_cooldown_elapses():
    # The real regression case: cooldown_s > 0 (production default is 3.0). An idle user's
    # state is RETAINED while its cooldown is live (so a quick re-request is still throttled)
    # and EVICTED on later activity once the cooldown has fully elapsed.
    guard = InFlightGuard(cooldown_s=0.02, max_queue_per_user=5)
    async def job(): await asyncio.sleep(0)
    await guard.run("u1", job)
    assert "u1" in guard._users          # within cooldown -> retained
    await asyncio.sleep(0.03)            # let u1's cooldown elapse
    await guard.run("u2", job)           # any later activity sweeps expired entries
    assert "u1" not in guard._users      # u1 evicted; _users bounded by recent activity
    assert "u2" in guard._users


@pytest.mark.asyncio
async def test_idle_state_evicted_zero_cooldown_on_next_activity():
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=5)
    async def job(): await asyncio.sleep(0)
    await guard.run("u1", job)
    await guard.run("u2", job)           # next run sweeps u1 (idle, cooldown 0 already elapsed)
    assert "u1" not in guard._users


@pytest.mark.asyncio
async def test_state_within_cooldown_is_retained_for_throttling():
    guard = InFlightGuard(cooldown_s=10.0, max_queue_per_user=5)
    async def job(): await asyncio.sleep(0)
    for u in ("u1", "u2", "u3"):
        await guard.run(u, job)
    # all three still inside the 10s window -> retained so their cooldown can be enforced
    assert set(guard._users) == {"u1", "u2", "u3"}
