from concierge.render import chunk_message, sources_embed, escape_markdown


def test_chunk_under_limit_single():
    assert chunk_message("hello") == ["hello"]
    assert chunk_message("") == []


def test_chunk_splits_on_boundary():
    text = "a" * 1500 + "\n" + "b" * 1500
    parts = chunk_message(text, limit=2000)
    assert len(parts) == 2
    assert all(len(p) <= 2000 for p in parts)
    assert parts[0].endswith("a")          # split at the newline, not mid-token


def test_chunk_hard_splits_giant_token():
    parts = chunk_message("x" * 5000, limit=2000)
    assert len(parts) == 3
    assert all(len(p) <= 2000 for p in parts)


def test_sources_embed_dedups_and_masks():
    e = sources_embed([{"url": "http://a", "title": "A"},
                       {"url": "http://a", "title": "dup"}], ["http://b"])
    assert e["title"] == "Sources"
    assert e["description"].count("http://a") == 1
    assert "http://b" in e["description"]


def test_sources_embed_none_when_empty():
    assert sources_embed([], []) is None


def test_escape():
    assert escape_markdown("a*b_c") == "a\\*b\\_c"


def test_chunk_no_empty_piece_on_whitespace_boundary():
    # A boundary inside a leading whitespace run must not yield a "" chunk (Discord rejects
    # empty content with HTTP 400); no characters may be dropped either.
    parts = chunk_message((" " * 2000) + ("a" * 5), limit=2000)
    assert "" not in parts
    assert "".join(parts).strip() == "a" * 5
