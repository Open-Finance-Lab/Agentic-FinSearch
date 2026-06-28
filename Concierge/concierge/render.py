from typing import Optional

DISCORD_MSG_LIMIT = 2000
EMBED_DESC_LIMIT = 4096
_MD_SPECIALS = set("\\`*_~|>")


def escape_markdown(text: str) -> str:
    return "".join("\\" + ch if ch in _MD_SPECIALS else ch for ch in text)


def chunk_message(text: str, limit: int = DISCORD_MSG_LIMIT) -> list:
    if not text:
        return []
    chunks, remaining = [], text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit            # no boundary: hard split
        piece = remaining[:cut].rstrip()
        if piece:                  # a boundary inside a whitespace run can strip to "";
            chunks.append(piece)   # Discord rejects empty content, so never emit it
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
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
    return {"title": "Sources", "description": "\n".join(lines)[:EMBED_DESC_LIMIT], "color": 0x2E86C1}
