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

    async def run(self, user_id: str, coro_factory):
        st = self._state(user_id)
        if st.waiting >= self._max_queue:
            raise QueueFullError(user_id)
        st.waiting += 1
        try:
            async with st.lock:
                now = asyncio.get_event_loop().time()
                wait = self._cooldown_s - (now - st.last_done_s)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    return await coro_factory()
                finally:
                    st.last_done_s = asyncio.get_event_loop().time()
        finally:
            st.waiting -= 1
            # Evict provably-idle state so _users stays bounded by *active* users, not
            # every user who ever messaged the bot. No await here -> atomic w.r.t. other
            # run() coroutines on the single event loop; the identity guard avoids
            # clobbering a state another waiter just recreated. A later request simply
            # recreates a (correctly cold-cooldown) state.
            if (st.waiting == 0
                    and asyncio.get_running_loop().time() - st.last_done_s >= self._cooldown_s
                    and self._users.get(user_id) is st):
                del self._users[user_id]
