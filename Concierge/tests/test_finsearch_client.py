from pathlib import Path
import aiohttp
import pytest
from concierge.finsearch_client import iter_sse_data, reduce_events, ChatResult, FinSearchClient

FIX = Path(__file__).parent / "fixtures" / "sse_chat_stream.txt"


def test_iter_and_reduce_full_stream():
    lines = FIX.read_text().splitlines()
    events = list(iter_sse_data(lines))
    acc, result = reduce_events(events)
    assert "".join(acc) == "AAPL is Apple."
    assert isinstance(result, ChatResult)
    assert result.text == "AAPL is Apple."
    assert result.truncated is False
    assert result.used_urls == ["http://y"]
    assert result.used_sources[0]["url"] == "http://x"


def test_partial_stream_is_truncated():
    lines = ['data: {"content": "half", "done": false}']   # no done frame
    acc, result = reduce_events(iter_sse_data(lines))
    assert result.text == "half"
    assert result.truncated is True


def test_iter_skips_non_data_and_bad_json():
    lines = ["event: connected", "data: not-json", ": comment", 'data: {"content":"x","done":false}']
    objs = list(iter_sse_data(lines))
    assert objs == [{"content": "x", "done": False}]


def test_in_band_error_frame_is_surfaced_not_clean():
    # The backend yields {"error": ..., "done": true} over an already-200 stream (it cannot
    # be caught by raise_for_status). Partial must be kept, error surfaced, and the result
    # forced truncated so it is never rendered as a complete answer.
    lines = ['data: {"content": "partial ", "done": false}',
             'data: {"error": "agent crashed", "done": true}']
    acc, result = reduce_events(iter_sse_data(lines))
    assert result.text == "partial "
    assert result.error == "agent crashed"
    assert result.truncated is True


@pytest.mark.asyncio
async def test_aclose_closes_session_and_is_idempotent():
    client = FinSearchClient("http://x", None, 1.0, "gpt-4o-mini")
    class _S:
        def __init__(self): self.closed = False
        async def close(self): self.closed = True
    s = _S()
    client._session = s
    await client.aclose()
    assert s.closed is True
    await client.aclose()      # already closed -> no-op, must not raise
    # never-opened client: aclose is a no-op
    await FinSearchClient("http://x", None, 1.0, "m").aclose()


# --- stream_chat against a fake aiohttp session (the real async consumer) ----------------

class _FakeContent:
    """resp.content: async-iterates the given byte lines, then raises `exc` (or stops)."""
    def __init__(self, lines, exc=None): self._lines = lines; self._exc = exc
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i < len(self._lines):
            v = self._lines[self._i]; self._i += 1; return v
        if self._exc is not None:
            raise self._exc
        raise StopAsyncIteration


class _FakeResp:
    def __init__(self, content): self.content = content
    def raise_for_status(self): pass


class _FakeGetCtx:
    def __init__(self, resp): self._resp = resp
    async def __aenter__(self): return self._resp
    async def __aexit__(self, *a): return False


class _FakeSession:
    def __init__(self, resp): self._resp = resp; self.closed = False
    def get(self, url): return _FakeGetCtx(self._resp)
    async def close(self): self.closed = True


def _client_with(content):
    c = FinSearchClient("http://x", None, 1.0, "m")
    c._session = _FakeSession(_FakeResp(content))
    return c


async def _drain(client):
    return [item async for item in client.stream_chat(
        question="q", session_id="discord:1:1", user_timezone="UTC", user_time="t")]


@pytest.mark.asyncio
async def test_stream_chat_transport_drop_finalizes_partial_as_truncated():
    content = _FakeContent([b'data: {"content": "partial", "done": false}\n'],
                           exc=aiohttp.ClientPayloadError("reset"))
    out = await _drain(_client_with(content))
    assert isinstance(out[-1], ChatResult)
    assert out[-1].text == "partial"        # kept "rather than losing it"
    assert out[-1].truncated is True
    assert out[-1].error is None            # a transport drop is not an in-band error


@pytest.mark.asyncio
async def test_stream_chat_drop_before_any_content_reraises():
    content = _FakeContent([], exc=aiohttp.ServerDisconnectedError())
    with pytest.raises(aiohttp.ClientError):
        await _drain(_client_with(content))


@pytest.mark.asyncio
async def test_stream_chat_in_band_error_frame_surfaced():
    content = _FakeContent([b'data: {"content": "partial", "done": false}\n',
                            b'data: {"error": "boom", "done": true}\n'])
    out = await _drain(_client_with(content))
    assert out[-1].text == "partial"
    assert out[-1].error == "boom"
    assert out[-1].truncated is True
