import json
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Iterator, Optional, Union
from urllib.parse import urlencode

import aiohttp


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
    error = final.get("error")
    text = "".join(acc) or (final.get("wrapped_content") or "")
    # An in-band error frame arrives WITH done:true over an already-200 stream, so
    # raise_for_status can't see it. Surface `error`, and force truncated so a partial
    # answer is never rendered as authoritative even if a consumer ignores `error`.
    return acc, ChatResult(text=text,
                           used_sources=final.get("used_sources") or [],
                           used_urls=final.get("used_urls") or [],
                           truncated=(not done) or bool(error),
                           error=(str(error) if error else None))


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
        error = final.get("error")
        text = "".join(acc) or (final.get("wrapped_content") or "")
        yield ChatResult(text=text,
                         used_sources=final.get("used_sources") or [],
                         used_urls=final.get("used_urls") or [],
                         truncated=(not done) or bool(error),
                         error=(str(error) if error else None))

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
