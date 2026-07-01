from .render import DISCORD_MSG_LIMIT, chunk_message, sources_embed
from .finsearch_client import ChatChunk, ChatResult
from .router import InboundMessage

_THINKING = "\U0001f4ad Thinking…"   # 💭 Thinking…
_ERR = "⚠️ Couldn't reach FinSearch, try again in a moment."
_CUTOFF = "\n\n*(response was cut off)*"
_ERR_NOTE = "\n\n⚠️ *FinSearch hit an error before finishing — this answer may be incomplete.*"


def _preview(text: str) -> str:
    return text[:DISCORD_MSG_LIMIT] if len(text) > DISCORD_MSG_LIMIT else text


async def _deliver(app, msg, placeholder, text: str) -> None:
    # Final-edit the placeholder with part 0, post the overflow as followups. Filter empty
    # pieces — a whitespace-boundary split can produce "" which Discord rejects (400).
    parts = [p for p in chunk_message(text) if p] or ["*(no response)*"]
    await app.discord.edit(placeholder, parts[0])
    for extra in parts[1:]:
        await app.discord.send_followup(msg, extra)


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
        # The transport layer already salvages partial output on a mid-stream drop (it yields
        # a truncated ChatResult), so anything raising here is a hard/unexpected failure
        # (backend unreachable, a bad edit, a bug). Show the friendly error and RE-RAISE so
        # on_message records the traceback — never silently mask it as a "cut off".
        await app.discord.edit(placeholder, _ERR)
        raise

    text = (result.text if result else acc) or "*(no response)*"
    if result and result.error:
        text += _ERR_NOTE          # in-band backend failure ({"error":...,"done":true}) surfaced
    elif result and result.truncated:
        text += _CUTOFF
    await _deliver(app, msg, placeholder, text)
    if result:
        embed = sources_embed(result.used_sources, result.used_urls)
        if embed:
            await app.discord.send_embed(msg, embed)
