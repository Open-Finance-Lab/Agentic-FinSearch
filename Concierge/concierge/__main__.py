import logging
import os

import discord

from .app import AppContext
from .bot import DiscordIO, register_handlers
from .config import load_config
from .finsearch_client import FinSearchClient
from .handlers import chat_handler
from .identity import IdentityStore
from .ratelimit import InFlightGuard
from .router import Router


class ConciergeClient(discord.Client):
    """discord.Client that also closes the FinSearch HTTP session on shutdown. close() is
    invoked via `async with self` inside client.run()'s runner, so the long-lived aiohttp
    ClientSession is torn down deterministically on the live loop (incl. SIGTERM)."""

    def __init__(self, *args, finsearch: FinSearchClient, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._finsearch = finsearch

    async def close(self) -> None:
        try:
            await self._finsearch.aclose()
        finally:
            await super().close()   # always tear down discord's sockets/HTTP


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(os.environ)

    identity = IdentityStore(cfg.identity_db_path)
    finsearch = FinSearchClient(cfg.finsearch_api_base, cfg.finsearch_api_key,
                                cfg.request_timeout_s, cfg.default_model)
    guard = InFlightGuard(cfg.cooldown_s, cfg.max_queue_per_user)
    router = Router(chat_handler)

    intents = discord.Intents.default()
    # Message content is delivered ONLY for DMs + messages that @mention us (Discord
    # platform exemption) — so we need NO privileged Message Content intent. A future
    # NON-mentioning trigger (prefix command, history scan) would read empty content
    # until that privileged intent is enabled.
    intents.message_content = False
    client = ConciergeClient(intents=intents, finsearch=finsearch)

    app = AppContext(cfg, identity, finsearch, DiscordIO(client))
    register_handlers(client, app, router, guard)

    try:
        client.run(cfg.discord_bot_token, log_handler=None)
    finally:
        identity.close()


if __name__ == "__main__":
    main()
