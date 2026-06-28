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
    client = discord.Client(intents=intents)

    app = AppContext(cfg, identity, finsearch, DiscordIO(client))
    register_handlers(client, app, router, guard)

    try:
        client.run(cfg.discord_bot_token, log_handler=None)
    finally:
        identity.close()


if __name__ == "__main__":
    main()
