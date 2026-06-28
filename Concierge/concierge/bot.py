import contextlib
import logging

import discord

from .ratelimit import QueueFullError
from .router import InboundMessage, Router

log = logging.getLogger("concierge")


def should_handle(*, author_is_bot: bool, is_dm: bool, mentioned: bool) -> bool:
    if author_is_bot:
        return False
    return is_dm or mentioned


def _strip_mention(content: str, bot_id: int) -> str:
    for token in (f"<@{bot_id}>", f"<@!{bot_id}>"):
        content = content.replace(token, "")
    return content


class DiscordIO:
    """Transport-thin wrapper so handlers never import discord.py types."""

    def __init__(self, client: discord.Client) -> None:
        self._client = client
        self._channels: dict = {}   # location_id -> live channel (avoids per-reply re-resolve)

    def remember_channel(self, channel) -> None:
        self._channels[str(channel.id)] = channel

    async def _channel(self, msg: InboundMessage):
        ch = self._channels.get(msg.location_id)
        if ch is not None:
            return ch
        cid = int(msg.location_id)
        ch = self._client.get_channel(cid) or await self._client.fetch_channel(cid)
        self._channels[msg.location_id] = ch
        return ch

    def typing(self, msg: InboundMessage):
        # "Bot is typing…" for the stream duration; reuse the remembered live channel.
        ch = self._channels.get(msg.location_id)
        return ch.typing() if ch is not None else contextlib.nullcontext()

    async def send(self, msg: InboundMessage, content: str):
        ch = await self._channel(msg)
        return await ch.send(content, allowed_mentions=discord.AllowedMentions.none())

    async def edit(self, message, content: str):
        return await message.edit(content=content,
                                  allowed_mentions=discord.AllowedMentions.none())

    async def send_followup(self, msg: InboundMessage, content: str):
        return await self.send(msg, content)

    async def send_embed(self, msg: InboundMessage, embed_dict: dict):
        ch = await self._channel(msg)
        return await ch.send(embed=discord.Embed.from_dict(embed_dict),
                             allowed_mentions=discord.AllowedMentions.none())


def register_handlers(client: discord.Client, app, router: Router, guard) -> None:
    @client.event
    async def on_message(message: discord.Message):
        me = client.user
        if me is None:                       # not logged in yet — ignore
            return
        is_dm = message.guild is None
        mentioned = me in message.mentions
        if not should_handle(author_is_bot=message.author.bot, is_dm=is_dm, mentioned=mentioned):
            return
        text = _strip_mention(message.content, me.id).strip()
        if not text:
            await message.channel.send("Ask me something \U0001f642",
                                       allowed_mentions=discord.AllowedMentions.none())
            return
        app.discord.remember_channel(message.channel)   # reuse the live channel (no REST re-resolve)
        inbound = InboundMessage(user_id=str(message.author.id),
                                 location_id=str(message.channel.id),
                                 text=text, is_dm=is_dm)
        handler = router.route(inbound)
        try:
            await guard.run(inbound.user_id, lambda: handler(inbound, app))
        except QueueFullError:
            await message.channel.send("⏳ I'm still working on your previous messages — one moment.",
                                       allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:            # spec §6: missing channel perms -> react ❌
            try:
                await message.add_reaction("❌")
            except discord.HTTPException:
                log.warning("no perms to react or reply for user %s", inbound.user_id)
        except Exception:
            log.exception("handler failed for user %s", inbound.user_id)

    @client.event
    async def on_interaction(interaction: discord.Interaction):
        # SEAM: future Confirm/Cancel buttons + slash commands. No-op in v1.
        log.info("interaction received (ignored in v1): %s", getattr(interaction, "type", "?"))
