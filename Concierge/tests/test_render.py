import pytest
from concierge.render import (
    chunk_message, sources_embed, escape_markdown, EMBED_DESC_LIMIT,
)


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


@pytest.mark.parametrize("ch", list("\\`*_~|>"))
def test_escape_covers_every_special(ch):
    # Every char in _MD_SPECIALS must be backslash-escaped — this is the markdown/spoiler/
    # code-injection guard for untrusted source titles, so a dropped special is a real hole.
    assert escape_markdown(ch) == "\\" + ch


def test_escape_mixed_and_passthrough():
    assert escape_markdown("a*b_c") == "a\\*b\\_c"
    assert escape_markdown("a`b|c>d~e") == "a\\`b\\|c\\>d\\~e"
    assert escape_markdown("plain text 123") == "plain text 123"   # non-specials untouched


def test_chunk_no_empty_piece_on_whitespace_boundary():
    # A boundary inside a leading whitespace run must not yield a "" chunk (Discord rejects
    # empty content with HTTP 400); no characters may be dropped either.
    parts = chunk_message((" " * 2000) + ("a" * 5), limit=2000)
    assert "" not in parts
    assert "".join(parts).strip() == "a" * 5


def test_chunk_balances_code_fence_across_split():
    # A fenced code block longer than the limit must not leave a dangling unclosed ```:
    # every emitted piece is self-contained (an even number of fence markers) and stays
    # within the limit even after the synthetic close/reopen fences are added.
    code = "\n".join(f"row {i:03d} " + "x" * 40 for i in range(120))   # ~6 KB, many newlines
    parts = chunk_message(f"```python\n{code}\n```", limit=2000)
    assert len(parts) >= 2
    assert all(len(p) <= 2000 for p in parts)
    assert all(p.count("```") % 2 == 0 for p in parts)   # no half-open fence in any piece


def test_sources_embed_truncates_on_whole_lines_with_more_marker():
    # An unusually large source set must be budgeted line-wise (never slicing a Markdown link
    # mid-token) and flagged with a "+N more" marker; description stays within the embed limit.
    many = [{"url": f"http://example.com/source-{i}", "title": f"Source number {i}"}
            for i in range(400)]
    e = sources_embed(many, [])
    assert len(e["description"]) <= EMBED_DESC_LIMIT
    assert "more)" in e["description"]                    # dropped-count marker present
    for line in e["description"].splitlines():
        if line.startswith("•"):
            assert line.count("[") == line.count("]")     # no link cut mid-token
            assert line.count("(") == line.count(")")
