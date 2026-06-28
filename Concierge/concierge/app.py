import asyncio
import datetime as dt

from .config import Config
from .identity import IdentityStore
from .finsearch_client import FinSearchClient
from .session import make_session_id
from .throttle import EditThrottle


class AppContext:
    """Everything chat_handler needs, injectable for tests."""

    def __init__(self, cfg: Config, identity: IdentityStore,
                 finsearch: FinSearchClient, discord_io) -> None:
        self.cfg = cfg
        self.identity = identity
        self.finsearch = finsearch
        self.discord = discord_io

    def make_session(self, user_id: str, location_id: str) -> str:
        return make_session_id(user_id, location_id)

    def new_throttle(self) -> EditThrottle:
        return EditThrottle(self.cfg.edit_interval_s, self.cfg.edit_min_chars)

    def clock(self) -> float:
        return asyncio.get_event_loop().time()

    def now_iso(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat()
