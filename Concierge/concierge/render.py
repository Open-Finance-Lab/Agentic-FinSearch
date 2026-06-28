from typing import Optional

DISCORD_MSG_LIMIT = 2000
EMBED_DESC_LIMIT = 4096
_MD_SPECIALS = set("\\`*_~|>")


def escape_markdown(text: str) -> str:
    return "".join("\\" + ch if ch in _MD_SPECIALS else ch for ch in text)


_FENCE = "```"


def chunk_message(text: str, limit: int = DISCORD_MSG_LIMIT) -> list:
    if not text:
        return []
    chunks, remaining = [], text
    open_fence = False             # is the ORIGINAL text inside an unclosed ``` fence here?
    while True:
        prefix = _FENCE + "\n" if open_fence else ""   # reopen a fence carried over from the prior piece
        # Reserve room for both the (possible) reopen prefix and a (possible) closing fence so
        # the emitted piece — markers included — never exceeds the limit.
        budget = limit - len(prefix) - (len(_FENCE) + 1)
        if len(prefix) + len(remaining) <= limit:      # everything left fits in one final piece
            piece = prefix + remaining
            if piece.strip():
                chunks.append(piece)
            break
        window = remaining[:budget]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = budget           # no boundary: hard split
        body = remaining[:cut].rstrip()
        close_here = open_fence ^ (body.count(_FENCE) % 2 == 1)   # fence state AFTER this body
        if body:                   # a boundary inside a whitespace run can strip to "";
            piece = prefix + body  # Discord rejects empty content, so never emit it
            if close_here:         # ended mid-fence with more to come -> close it so Discord renders cleanly
                piece += "\n" + _FENCE
            chunks.append(piece)
            open_fence = close_here
        remaining = remaining[cut:].lstrip()
    return chunks


def sources_embed(used_sources: list, used_urls: list) -> Optional[dict]:
    lines, seen = [], set()
    for src in used_sources or []:
        url = (src.get("url") or "").strip()
        title = (src.get("title") or url or "source").strip()
        if url and url not in seen:
            seen.add(url)
            lines.append(f"• [{escape_markdown(title)}]({url})")
    for url in used_urls or []:
        url = (url or "").strip()
        if url and url not in seen:
            seen.add(url)
            lines.append(f"• {url}")
    if not lines:
        return None
    # Append whole bullet lines until the next would overflow, so a Markdown link is never
    # sliced mid-token by a blind [:limit] cut; surface a count of any sources we dropped.
    kept, used = [], 0
    for ln in lines:
        extra = len(ln) + (1 if kept else 0)         # +1 for the joining newline
        if used + extra > EMBED_DESC_LIMIT:
            break
        kept.append(ln)
        used += extra
    if not kept:                                     # pathological single oversized line
        kept, used = [lines[0][:EMBED_DESC_LIMIT]], EMBED_DESC_LIMIT
    description = "\n".join(kept)
    dropped = len(lines) - len(kept)
    if dropped:
        marker = f"\n…(+{dropped} more)"
        if used + len(marker) <= EMBED_DESC_LIMIT:
            description += marker
    return {"title": "Sources", "description": description, "color": 0x2E86C1}
