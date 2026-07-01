import contextlib
import pytest
from concierge.handlers import chat_handler
from concierge.router import InboundMessage
from concierge.finsearch_client import ChatChunk, ChatResult
from concierge.identity import IdentityStore
from concierge.session import make_session_id
from concierge.throttle import EditThrottle
from concierge.render import chunk_message


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


@pytest.mark.asyncio
async def test_in_band_error_frame_surfaced_with_partial_preserved():
    # Backend emits {"error": ..., "done": true} mid-stream: partial kept, error shown,
    # and it must NOT be presented as a clean/complete answer.
    d = FakeDiscord()
    f = FakeFinSearch([ChatChunk("partial "),
                       ChatResult("partial ", [], [], True, error="agent crashed")])
    await chat_handler(_msg(), FakeApp(d, f))
    assert "partial " in d.edits[-1]
    assert "error before finishing" in d.edits[-1]


@pytest.mark.asyncio
async def test_unexpected_error_is_not_masked_as_cutoff():
    # A non-transport error mid-stream (e.g. a bug) must surface the friendly error AND
    # re-raise (so on_message logs it) — never be silently relabeled a "cut off".
    d = FakeDiscord()
    class Bug:
        async def stream_chat(self, **kw):
            yield ChatChunk("partial")
            raise AttributeError("boom")
    with pytest.raises(AttributeError):
        await chat_handler(_msg(), FakeApp(d, Bug()))
    assert "Couldn't reach FinSearch" in d.edits[-1]
    assert "cut off" not in d.edits[-1]


class _ThrottledApp(FakeApp):
    def new_throttle(self): return EditThrottle(1.2, 1500)   # production throttle values


@pytest.mark.asyncio
async def test_streaming_edits_are_throttled_and_capped_at_discord_limit():
    # Drive the streaming-edit loop with the PRODUCTION throttle (1.2s / 1500 chars) and a
    # deterministic +1.0s/chunk clock, so realistic batching AND the _preview 2000-char cap
    # are exercised — the other handler tests all use the always-flush EditThrottle(0.0, 1).
    d = FakeDiscord()
    chunks = [ChatChunk("a" * 600) for _ in range(5)]            # 3000 chars streamed
    f = FakeFinSearch(chunks + [ChatResult("a" * 3000, [], [], False)])
    await chat_handler(_msg(), _ThrottledApp(d, f))
    assert all(len(e) <= 2000 for e in d.edits)                 # never exceed Discord's limit
    assert max(len(e) for e in d.edits) == 2000                 # _preview capped a >2000 acc to 2000
    assert len(d.edits) - 1 < len(chunks)                       # throttled: <1 streaming edit/chunk
    assert "".join([d.edits[-1]] + d.followups) == "a" * 3000   # full answer delivered, nothing lost


@pytest.mark.asyncio
async def test_overflow_uses_followups():
    # >2000 chars -> placeholder gets part 0, the rest go out as followups.
    d = FakeDiscord()
    big = ("A" * 1500) + "\n" + ("B" * 1500)
    f = FakeFinSearch([ChatResult(big, [], [], False)])
    await chat_handler(_msg(), FakeApp(d, f))
    parts = chunk_message(big)
    assert len(parts) == 2
    assert d.edits[-1] == parts[0]
    assert d.followups == [parts[1]]
