from .render import DISCORD_MSG_LIMIT, chunk_message, sources_embed
from .finsearch_client import ChatChunk, ChatResult
from .router import InboundMessage

_THINKING = "\U0001f4ad Thinking…"   # 💭 Thinking…
_ERR = "⚠️ Couldn't reach FinSearch, try again in a moment."


def _preview(text: str) -> str:
    return text[:DISCORD_MSG_LIMIT] if len(text) > DISCORD_MSG_LIMIT else text


async def chat_handler(msg: InboundMessage, app) -> None:
    app.identity.resolve(msg.user_id, now_iso=app.now_iso())
    session_id = app.make_session(msg.user_id, msg.location_id)
    placeholder = await app.discord.send(msg, _THINKING)
    throttle = app.new_throttle()
    acc, result = "", None
    try:
        async with app.discord.typing(msg):          # "Bot is typing…" for the stream duration
            async for item in app.finsearch.stream_chat(
                question=msg.text, session_id=session_id,
                user_timezone="UTC", user_time=app.now_iso(),
            ):
                if isinstance(item, ChatChunk):
                    acc += item.content
                    now = app.clock()                # read the clock ONCE per chunk
                    if throttle.should_flush(len(acc), now):
                        throttle.mark_flushed(len(acc), now)
                        await app.discord.edit(placeholder, _preview(acc))
                elif isinstance(item, ChatResult):
                    result = item
    except Exception:
        await app.discord.edit(placeholder, _ERR)
        raise

    text = (result.text if result else acc) or "*(no response)*"
    if result and result.truncated:
        text += "\n\n*(response was cut off)*"
    parts = chunk_message(text)
    await app.discord.edit(placeholder, parts[0] if parts else "*(no response)*")
    for extra in parts[1:]:
        await app.discord.send_followup(msg, extra)
    if result:
        embed = sources_embed(result.used_sources, result.used_urls)
        if embed:
            await app.discord.send_embed(msg, embed)
