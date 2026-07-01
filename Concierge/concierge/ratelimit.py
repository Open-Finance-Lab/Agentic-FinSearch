import asyncio
from dataclasses import dataclass, field


class QueueFullError(Exception):
    pass


@dataclass
class _UserState:
    lock: "asyncio.Lock" = field(default_factory=asyncio.Lock)
    waiting: int = 0
    last_done_s: float = 0.0


class InFlightGuard:
    def __init__(self, cooldown_s: float, max_queue_per_user: int):
        self._cooldown_s = cooldown_s
        self._max_queue = max_queue_per_user
        self._users: dict = {}

    def _state(self, user_id):
        st = self._users.get(user_id)
        if st is None:
            st = _UserState()
            self._users[user_id] = st
        return st

    def _evict_expired(self, now: float) -> None:
        # Drop provably-idle users whose cooldown window has FULLY elapsed, so _users stays
        # bounded by *recently active* users instead of everyone who ever messaged the bot.
        # Eviction can't happen in the just-finished request's finally (no time has passed
        # since last_done_s, so the cooldown never reads as elapsed) — it must run on later
        # activity. No await here -> atomic w.r.t. other run() coroutines on the single loop.
        # An entry still inside its cooldown is kept, so a quick re-request is still throttled;
        # once cooldown has elapsed there is nothing left to preserve.
        for uid in [u for u, st in self._users.items()
                    if st.waiting == 0 and now - st.last_done_s >= self._cooldown_s]:
            del self._users[uid]

    async def run(self, user_id: str, coro_factory):
        loop = asyncio.get_running_loop()
        self._evict_expired(loop.time())
        st = self._state(user_id)
        if st.waiting >= self._max_queue:
            raise QueueFullError(user_id)
        st.waiting += 1
        try:
            async with st.lock:
                wait = self._cooldown_s - (loop.time() - st.last_done_s)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    return await coro_factory()
                finally:
                    st.last_done_s = loop.time()
        finally:
            st.waiting -= 1
