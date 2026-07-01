import contextlib
import pytest
import discord
from concierge.bot import should_handle, _strip_mention, register_handlers, DiscordIO
from concierge.router import Router, InboundMessage
from concierge.ratelimit import QueueFullError
from concierge.render import sources_embed


def test_should_handle_rules():
    assert should_handle(author_is_bot=False, is_dm=True, mentioned=False) is True
    assert should_handle(author_is_bot=False, is_dm=False, mentioned=True) is True
    assert should_handle(author_is_bot=False, is_dm=False, mentioned=False) is False
    assert should_handle(author_is_bot=True, is_dm=True, mentioned=True) is False


def test_strip_mention():
    assert _strip_mention("<@42> hello", 42).strip() == "hello"
    assert _strip_mention("<@!42> hi", 42).strip() == "hi"
    assert _strip_mention("no mention", 42) == "no mention"


# --- on_message dispatch coverage (the closure register_handlers wires) ------------------

class _StubClient:
    """Captures the @client.event coroutines so the on_message closure can be driven directly."""
    def __init__(self, user): self.user = user; self.events = {}
    def event(self, coro): self.events[coro.__name__] = coro; return coro


class _User:
    def __init__(self, uid, bot=False): self.id = uid; self.bot = bot


class _Channel:
    def __init__(self, cid=10): self.id = cid; self.sent = []
    async def send(self, content, **kw): self.sent.append(content)
    def typing(self): return contextlib.nullcontext()


class _Message:
    def __init__(self, *, content, author, channel, guild=None, mentions=()):
        self.content = content; self.author = author; self.channel = channel
        self.guild = guild; self.mentions = list(mentions); self.reactions = []
    async def add_reaction(self, emoji): self.reactions.append(emoji)


class _AppDiscord:
    def __init__(self): self.remembered = []
    def remember_channel(self, ch): self.remembered.append(ch)


class _App:
    def __init__(self): self.discord = _AppDiscord()


class _Guard:
    """run() raises `exc` if given, else awaits the factory (the real dispatch)."""
    def __init__(self, exc=None): self.exc = exc; self.calls = []
    async def run(self, user_id, factory):
        self.calls.append(user_id)
        if self.exc is not None:
            raise self.exc
        return await factory()


class _Resp:
    status = 403
    reason = "Forbidden"


def _on_message(app, router, guard, bot_user):
    client = _StubClient(bot_user)
    register_handlers(client, app, router, guard)
    return client.events["on_message"]


async def _noop_chat(inbound, app):
    return None


@pytest.mark.asyncio
async def test_on_message_empty_text_prompts_and_does_not_dispatch():
    bot_user = _User(42)
    app, guard = _App(), _Guard()
    on_message = _on_message(app, Router(_noop_chat), guard, bot_user)
    ch = _Channel()
    msg = _Message(content="<@42>", author=_User(1), channel=ch, guild=object(), mentions=[bot_user])
    await on_message(msg)
    assert ch.sent and "Ask me something" in ch.sent[0]
    assert guard.calls == []


@pytest.mark.asyncio
async def test_on_message_ignores_other_bots():
    bot_user = _User(42)
    app, guard = _App(), _Guard()
    on_message = _on_message(app, Router(_noop_chat), guard, bot_user)
    ch = _Channel()
    msg = _Message(content="hi", author=_User(2, bot=True), channel=ch, guild=None, mentions=[])
    await on_message(msg)
    assert ch.sent == [] and guard.calls == []


@pytest.mark.asyncio
async def test_on_message_dispatches_and_remembers_channel():
    bot_user = _User(42); app = _App()
    seen = []
    async def chat(inbound, app): seen.append(inbound.text)
    guard = _Guard()
    on_message = _on_message(app, Router(chat), guard, bot_user)
    ch = _Channel(cid=77)
    msg = _Message(content="<@42> what is AAPL", author=_User(1), channel=ch,
                   guild=object(), mentions=[bot_user])
    await on_message(msg)
    assert app.discord.remembered == [ch]
    assert guard.calls == ["1"]
    assert seen == ["what is AAPL"]


@pytest.mark.asyncio
async def test_on_message_queue_full_replies():
    bot_user = _User(42)
    app, guard = _App(), _Guard(exc=QueueFullError("1"))
    on_message = _on_message(app, Router(_noop_chat), guard, bot_user)
    ch = _Channel()
    msg = _Message(content="hi", author=_User(1), channel=ch, guild=None, mentions=[])
    await on_message(msg)
    assert ch.sent and "still working" in ch.sent[0]


@pytest.mark.asyncio
async def test_on_message_forbidden_reacts_cross():
    bot_user = _User(42)
    app, guard = _App(), _Guard(exc=discord.Forbidden(_Resp(), "no perms"))
    on_message = _on_message(app, Router(_noop_chat), guard, bot_user)
    ch = _Channel()
    msg = _Message(content="hi", author=_User(1), channel=ch, guild=None, mentions=[])
    await on_message(msg)
    assert msg.reactions == ["❌"]


@pytest.mark.asyncio
async def test_on_message_forbidden_then_failed_reaction_is_swallowed():
    bot_user = _User(42)
    app, guard = _App(), _Guard(exc=discord.Forbidden(_Resp(), "no perms"))
    on_message = _on_message(app, Router(_noop_chat), guard, bot_user)
    ch = _Channel()
    msg = _Message(content="hi", author=_User(1), channel=ch, guild=None, mentions=[])
    async def boom(emoji): raise discord.HTTPException(_Resp(), "cannot react")
    msg.add_reaction = boom
    await on_message(msg)   # must NOT raise — nested HTTPException is logged + swallowed


# --- DiscordIO transport wrapper (the 'transport-thin' layer this PR exists to provide) ----

class _IOSent:
    """A sent message that records its edits (DiscordIO.edit -> message.edit(...))."""
    def __init__(self): self.edits = []
    async def edit(self, *, content=None, allowed_mentions=None):
        self.edits.append({"content": content, "allowed_mentions": allowed_mentions})


class _IOChannel:
    def __init__(self, cid): self.id = cid; self.sent = []
    async def send(self, content=None, *, embed=None, allowed_mentions=None):
        self.sent.append({"content": content, "embed": embed, "allowed_mentions": allowed_mentions})
        return _IOSent()


class _IOClient:
    """get_channel hits an in-memory map; a miss forces the REST fetch_channel fallback."""
    def __init__(self, get_map=None): self._get = get_map or {}; self.fetched = []
    def get_channel(self, cid): return self._get.get(cid)
    async def fetch_channel(self, cid): self.fetched.append(cid); return _IOChannel(cid)


def _io_msg(cid): return InboundMessage(user_id="1", location_id=str(cid), text="hi", is_dm=True)


def _none(am):  # AllowedMentions has no __eq__; assert the suppression flags directly
    return (am.everyone, am.users, am.roles)


@pytest.mark.asyncio
async def test_discordio_remembered_channel_skips_resolve():
    client = _IOClient()                     # get_channel would miss; fetch would record
    io = DiscordIO(client)
    ch = _IOChannel(77)
    io.remember_channel(ch)
    await io.send(_io_msg(77), "yo")
    assert ch.sent[0]["content"] == "yo"
    assert client.fetched == []              # remembered live channel -> no get/fetch round-trip


@pytest.mark.asyncio
async def test_discordio_fetch_fallback_on_miss_then_caches():
    client = _IOClient(get_map={})           # get_channel miss -> REST fetch_channel
    io = DiscordIO(client)
    await io.send(_io_msg(88), "a")
    assert client.fetched == [88]            # fell back to fetch_channel
    await io.send(_io_msg(88), "b")
    assert client.fetched == [88]            # second send reuses the cached channel (no 2nd fetch)


@pytest.mark.asyncio
async def test_discordio_get_channel_hit_avoids_fetch():
    ch = _IOChannel(99)
    client = _IOClient(get_map={99: ch})
    await DiscordIO(client).send(_io_msg(99), "x")
    assert ch.sent[0]["content"] == "x"
    assert client.fetched == []              # resolved via get_channel, never fetched


@pytest.mark.asyncio
async def test_discordio_send_and_edit_suppress_all_mentions():
    ch = _IOChannel(5)
    io = DiscordIO(_IOClient(get_map={5: ch}))
    sent = await io.send(_io_msg(5), "hello")
    assert _none(ch.sent[0]["allowed_mentions"]) == (False, False, False)
    await io.edit(sent, "edited")
    assert _none(sent.edits[0]["allowed_mentions"]) == (False, False, False)


@pytest.mark.asyncio
async def test_discordio_send_embed_roundtrips_sources_dict():
    ch = _IOChannel(6)
    io = DiscordIO(_IOClient(get_map={6: ch}))
    embed_dict = sources_embed([{"url": "http://x", "title": "X"}], ["http://y"])
    await io.send_embed(_io_msg(6), embed_dict)
    sent_embed = ch.sent[0]["embed"]
    assert isinstance(sent_embed, discord.Embed)   # Embed.from_dict accepted the dict (no raise)
    assert sent_embed.title == "Sources"
    assert _none(ch.sent[0]["allowed_mentions"]) == (False, False, False)
