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
