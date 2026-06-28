from pathlib import Path
from concierge.finsearch_client import iter_sse_data, reduce_events, ChatResult

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
