from pathlib import Path
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
