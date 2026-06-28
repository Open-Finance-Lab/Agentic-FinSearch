import asyncio
import json
import re
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Iterator, Optional, Union
from urllib.parse import urlencode

import aiohttp
from aiohttp.http_exceptions import LineTooLong

# The backend's final-frame `wrapped_content` carries claim values wrapped in literal HTML
# (<span data-claim-id="...">value</span>) meant for the web client. Discord has no HTML, so
# strip those tags to their inner text before using wrapped_content as a fallback. Only the
# open/close <span ...> tags are removed; the value text inside is kept.
_SPAN_TAG_RE = re.compile(r"</?span[^>]*>")


def _unwrap_spans(text: str) -> str:
    return _SPAN_TAG_RE.sub("", text)


@dataclass(frozen=True)
class ChatChunk:
    content: str


@dataclass(frozen=True)
class ChatResult:
    text: str
    used_sources: list
    used_urls: list
    truncated: bool
    error: Optional[str] = None   # backend in-band failure ({"error": ..., "done": true})


def iter_sse_data(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        line = line.rstrip("\n")
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _finalize(acc: list, done: bool, final: dict) -> ChatResult:
    """Single source of truth for the terminal ChatResult — shared by the pure reducer and
    the live streaming path so the text fallback, source lists, and truncated/error policy
    can never drift between them.

    An in-band error frame arrives WITH done:true over an already-200 stream, so
    raise_for_status can't see it. We surface `error` and force truncated, so a partial answer
    is never rendered as authoritative even if a consumer ignores `error`. The text falls back
    to the final frame's wrapped_content (span markup stripped) only when no chunks streamed.
    """
    error = final.get("error")
    return ChatResult(
        text="".join(acc) or _unwrap_spans(final.get("wrapped_content") or ""),
        used_sources=final.get("used_sources") or [],
        used_urls=final.get("used_urls") or [],
        truncated=(not done) or bool(error),
        error=(str(error) if error else None),
    )


def reduce_events(events: Iterable[dict]):
    """Pure reducer -> (content chunks, ChatResult). Mirrors stream_chat's accumulation."""
    acc, done, final = [], False, {}
    for ev in events:
        if ev.get("done") is True:
            done, final = True, ev
            break
        c = ev.get("content")
        if c:
            acc.append(c)
    return acc, _finalize(acc, done, final)


class FinSearchClient:
    def __init__(self, base_url: str, api_key: Optional[str],
                 timeout_s: float, default_model: str) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._model = default_model
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=headers)
        return self._session

    async def stream_chat(self, *, question: str, session_id: str,
                          user_timezone: str, user_time: str
                          ) -> AsyncIterator[Union[ChatChunk, ChatResult]]:
        params = {
            "question": question, "session_id": session_id, "models": self._model,
            "is_advanced": "false", "use_memory": "true",
            "current_url": "https://discord.com",
            "user_timezone": user_timezone, "user_time": user_time,
        }
        url = f"{self._base}/get_chat_response_stream/?{urlencode(params)}"
        session = await self._ensure_session()
        acc, done, final = [], False, {}
        try:
            async with session.get(url) as resp:
                resp.raise_for_status()
                # INVARIANT: resp.content yields ONE line per iteration (aiohttp readline),
                # so each iter_sse_data() sees a single SSE event and the break-on-done below
                # cannot strand a sibling content frame. Do NOT switch to iter_chunked()/iter_any()
                # without restructuring this into a line-buffer reducer.
                async for raw in resp.content:
                    for ev in iter_sse_data([raw.decode("utf-8", "replace")]):
                        if ev.get("done") is True:
                            done, final = True, ev
                            break
                        c = ev.get("content")
                        if c:
                            acc.append(c)
                            yield ChatChunk(c)
                    if done:
                        break
        except (aiohttp.ClientError, asyncio.TimeoutError, LineTooLong):
            # Transport-level failure (dropped connection, timeout, or an oversized SSE line —
            # LineTooLong is NOT an aiohttp.ClientError, so it must be named explicitly or it
            # would escape and discard the partial). With NO partial text (backend down / 5xx /
            # pre-stream drop) re-raise so the caller can show the friendly error. With partial
            # text, fall through and finalize it as truncated "rather than losing it" (spec §6) —
            # translating the transport failure into the domain ChatResult here keeps the
            # handler transport-agnostic and stops it from having to swallow exceptions.
            if not acc:
                raise
        yield _finalize(acc, done, final)

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
