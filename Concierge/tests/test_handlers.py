import asyncio
import contextlib
import pytest
from concierge.handlers import chat_handler
from concierge.router import InboundMessage
from concierge.finsearch_client import ChatChunk, ChatResult
from concierge.identity import IdentityStore
from concierge.session import make_session_id
from concierge.throttle import EditThrottle


class FakeDiscord:
    def __init__(self): self.sent=[]; self.edits=[]; self.embeds=[]; self.followups=[]
    async def send(self, msg, content): self.sent.append(content); return {"id": len(self.sent)}
    async def edit(self, ph, content): self.edits.append(content)
    async def send_followup(self, msg, content): self.followups.append(content)
    async def send_embed(self, msg, embed): self.embeds.append(embed)
    def typing(self, msg): return contextlib.nullcontext()   # async-with no-op (Py3.10+)


class FakeFinSearch:
    def __init__(self, items): self._items=items
    async def stream_chat(self, **kw):
        for it in self._items: yield it


class FakeApp:
    def __init__(self, discord, finsearch):
        self.discord=discord; self.finsearch=finsearch
        self.identity=IdentityStore(":memory:")
        self._t=0.0
    def make_session(self, u, l): return make_session_id(u, l)
    def new_throttle(self): return EditThrottle(0.0, 1)   # always flush -> exercise edit path
    def clock(self): self._t+=1.0; return self._t
    def now_iso(self): return "2026-06-28T00:00:00+00:00"


def _msg(): return InboundMessage(user_id="1", location_id="2", text="hi", is_dm=True)


@pytest.mark.asyncio
async def test_happy_path_posts_placeholder_then_final_and_sources():
    d = FakeDiscord()
    f = FakeFinSearch([ChatChunk("AAPL "), ChatChunk("is Apple."),
                       ChatResult("AAPL is Apple.", [{"url":"http://x","title":"X"}], [], False)])
    app = FakeApp(d, f)
    await chat_handler(_msg(), app)
    assert d.sent[0] == "\U0001f4ad Thinking…"      # placeholder posted first
    assert d.edits[-1] == "AAPL is Apple."                # final edit is the full answer
    assert len(d.embeds) == 1                              # sources embed sent


@pytest.mark.asyncio
async def test_truncated_marks_cutoff():
    d = FakeDiscord()
    f = FakeFinSearch([ChatChunk("half"), ChatResult("half", [], [], True)])
    await chat_handler(_msg(), FakeApp(d, f))
    assert "cut off" in d.edits[-1]


@pytest.mark.asyncio
async def test_backend_error_shows_friendly_message():
    d = FakeDiscord()
    class Boom:
        async def stream_chat(self, **kw):
            if False: yield
            raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        await chat_handler(_msg(), FakeApp(d, Boom()))
    assert "Couldn't reach FinSearch" in d.edits[-1]
