"""End-to-end wiring: a real Discord message driven through the actual
register_handlers -> Router(chat_handler) -> InFlightGuard.run -> chat_handler -> DiscordIO
chain, with only the network edges (the gateway client + the FinSearch stream) faked.

The per-component tests stub the *other* half (test_bot uses a _Guard stub + _noop_chat;
test_handlers uses a FakeApp with no router/guard). This is the only test that exercises the
seams between them, so a wiring regression (wrong arg to guard.run, a kwarg rename between the
handler and the client, a router mis-dispatch) surfaces here.
"""
import contextlib
import discord
import pytest

from concierge.app import AppContext
from concierge.bot import DiscordIO, register_handlers
from concierge.config import Config
from concierge.finsearch_client import ChatChunk, ChatResult
from concierge.identity import IdentityStore
from concierge.handlers import chat_handler
from concierge.ratelimit import InFlightGuard
from concierge.router import Router


class _Sent:
    def __init__(self): self.edits = []
    async def edit(self, *, content=None, allowed_mentions=None): self.edits.append(content)


class _Channel:
    def __init__(self, cid):
        self.id = cid; self.contents = []; self.embeds = []; self.msgs = []
    async def send(self, content=None, *, embed=None, allowed_mentions=None):
        if embed is not None:
            self.embeds.append(embed)
        else:
            self.contents.append(content)
        m = _Sent(); self.msgs.append(m); return m
    def typing(self): return contextlib.nullcontext()


class _User:
    def __init__(self, uid, bot=False): self.id = uid; self.bot = bot


class _Message:
    def __init__(self, *, content, author, channel, guild=None, mentions=()):
        self.content = content; self.author = author; self.channel = channel
        self.guild = guild; self.mentions = list(mentions)
    async def add_reaction(self, emoji): pass


class _StubClient:
    """Gateway client for register_handlers: captures the on_message coroutine + exposes .user."""
    def __init__(self, user): self.user = user; self.events = {}
    def event(self, coro): self.events[coro.__name__] = coro; return coro


class _GatewayClient:
    """The client DiscordIO resolves channels through — must never be hit, because on_message
    remembers the live channel before dispatch."""
    def get_channel(self, cid): return None
    async def fetch_channel(self, cid): raise AssertionError("should reuse the remembered channel")


class _FakeFinSearch:
    def __init__(self, items): self._items = items
    async def stream_chat(self, **kw):
        for it in self._items:
            yield it


@pytest.mark.asyncio
async def test_message_flows_through_router_guard_handler_to_discord():
    bot_user = _User(42)
    cfg = Config(discord_bot_token="x", finsearch_api_base="http://x",
                 finsearch_api_key=None, identity_db_path=":memory:")
    identity = IdentityStore(":memory:")
    finsearch = _FakeFinSearch([
        ChatChunk("AAPL "), ChatChunk("is Apple."),
        ChatResult("AAPL is Apple.", [{"url": "http://x", "title": "X"}], [], False),
    ])
    app = AppContext(cfg, identity, finsearch, DiscordIO(_GatewayClient()))
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=3)

    client = _StubClient(bot_user)
    register_handlers(client, app, Router(chat_handler), guard)
    on_message = client.events["on_message"]

    ch = _Channel(cid=77)
    msg = _Message(content="<@42> what is AAPL", author=_User(1), channel=ch,
                   guild=object(), mentions=[bot_user])
    await on_message(msg)

    assert "Thinking" in ch.contents[0]                 # placeholder posted via the real DiscordIO
    assert ch.msgs[0].edits[-1] == "AAPL is Apple."      # streamed answer finalized onto it
    assert len(ch.embeds) == 1                           # sources embed sent
    assert isinstance(ch.embeds[0], discord.Embed) and ch.embeds[0].title == "Sources"
    assert identity.get("1") is not None                 # the real guarded handler ran resolve()
