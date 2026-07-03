import asyncio
import collections
import json
import re
from dataclasses import dataclass
from typing import AsyncIterator, Iterable, Iterator, Optional, Union

import aiohttp
from aiohttp.http_exceptions import LineTooLong

# Backend SESSION_COOKIE_NAME. The backend roots the conversation/history cache key in this
# SIGNED cookie (P1 C-session / IDOR fix), so the same cookie must come back on every turn of
# a conversation for `use_memory` history to persist.
_SESSION_COOKIE_NAME = "fingpt_sessionid"

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
                 timeout_s: float, default_model: str,
                 max_tracked_cookies: int = 10000) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._model = default_model
        self._session: Optional[aiohttp.ClientSession] = None
        # Per-conversation `fingpt_sessionid` store, keyed by session_id (one Discord
        # conversation = one root). Bounded LRU so a long-lived public bot can't leak a cookie
        # per conversation forever; evicting one only costs that conversation its continuity on
        # its next turn (a fresh root) and never crosses to another conversation.
        self._cookies: "collections.OrderedDict[str, str]" = collections.OrderedDict()
        self._max_tracked_cookies = max_tracked_cookies

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # FINSEARCH_API_BASE points at the co-located backend over a plain-HTTP loopback, but
            # the backend (Django SECURE_SSL_REDIRECT) 301-redirects any request it doesn't deem
            # secure. The edge proxy stamps X-Forwarded-Proto for browser traffic; we present it
            # too so this direct loopback call is treated as already-HTTPS and served — not bounced
            # into an https:// redirect that would strand us in a doomed TLS handshake.
            headers = {"X-Forwarded-Proto": "https"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            # DummyCookieJar: aiohttp's default jar would (a) DROP the backend's Secure
            # `fingpt_sessionid` cookie because this loopback hop is plain HTTP, and (b) share
            # ONE jar across every conversation. We disable automatic cookie handling and resend
            # the right cookie per conversation by hand (see _cookie_header / _remember_cookie).
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, headers=headers,
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        return self._session

    def _cookie_header(self, session_id: str) -> dict:
        """Manual Cookie header carrying this conversation's stored `fingpt_sessionid`, or {}
        on the first turn (nothing captured yet). Reading also marks the entry recently-used."""
        value = self._cookies.get(session_id)
        if not value:
            return {}
        self._cookies.move_to_end(session_id)
        return {"Cookie": f"{_SESSION_COOKIE_NAME}={value}"}

    def _remember_cookie(self, session_id: str, resp) -> None:
        """Capture a `fingpt_sessionid` Set-Cookie from the response, if present, and store it
        for this conversation (bounded LRU). The backend only re-emits it when the session is
        modified (first turn), so a turn with no Set-Cookie keeps the existing value."""
        cookies = getattr(resp, "cookies", None)
        if not cookies:
            return
        morsel = cookies.get(_SESSION_COOKIE_NAME)
        value = getattr(morsel, "value", None)
        if not value:
            return
        self._cookies[session_id] = value
        self._cookies.move_to_end(session_id)
        while len(self._cookies) > self._max_tracked_cookies:
            self._cookies.popitem(last=False)   # evict least-recently-used

    async def stream_chat(self, *, question: str, session_id: str,
                          user_timezone: str, user_time: str
                          ) -> AsyncIterator[Union[ChatChunk, ChatResult]]:
        # Frozen POST contract: same path, NO query string. Every request parameter travels
        # in a flat JSON body with ALL values as strings. The backend dual-accepts GET and
        # POST during the migration window, with a JSON-body value winning over any same-key
        # query-string value; the SSE response is byte-identical to the old GET's.
        payload = {
            "question": question, "session_id": session_id, "models": self._model,
            "is_advanced": "false", "use_memory": "true",
            "current_url": "https://discord.com",
            "user_timezone": user_timezone, "user_time": user_time,
        }
        url = f"{self._base}/get_chat_response_stream/"
        session = await self._ensure_session()
        acc, done, final = [], False, {}
        try:
            # allow_redirects=False: we already assert HTTPS via the X-Forwarded-Proto header, so a
            # 3xx here means the backend is misrouting this client. Following it (e.g. http->https on
            # a plain-HTTP port) strands us in a ~60s TLS handshake before it aborts — refuse it and
            # fail fast into the friendly error instead.
            async with session.post(url, json=payload, allow_redirects=False,
                                    headers=self._cookie_header(session_id) or None) as resp:
                # Capture the conversation root cookie as soon as headers arrive (before the
                # body streams), so we still remember it even if the stream later drops.
                self._remember_cookie(session_id, resp)
                resp.raise_for_status()
                if resp.status >= 300:
                    raise aiohttp.ClientError(
                        f"unexpected {resp.status} redirect to {resp.headers.get('Location')!r}")
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
