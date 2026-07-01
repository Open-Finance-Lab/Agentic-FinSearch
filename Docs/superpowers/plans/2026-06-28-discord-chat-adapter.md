# Discord Chat Adapter (Concierge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the passive News Heartbeat into an interactive chatbot — a thin Discord Gateway adapter ("Concierge") that maps free-form @mention/DM messages onto the *existing* FinSearch extension pipeline (`/get_chat_response_stream/`) and posts replies back, with no backend change.

**Architecture:** A separate, persistent `discord.py` service (Approach A from the spec). It is **transport-thin**: a `chat_handler` orchestrates through a tiny `DiscordIO` interface + an injected `AppContext`, so almost all logic is unit-testable with fakes (no Gateway, no network). Two-layer identity (durable SQLite record + ephemeral per-location `session_id`) is the only forward-compat seam built now; strategy control is deferred to backend ATL MCP tools.

**Tech Stack:** Python 3.12, `discord.py` (brings `aiohttp`), `sqlite3` (stdlib), `pytest` + `pytest-asyncio`. Self-contained package; mirrors (does not import) the heartbeat's patterns.

**Spec:** `Docs/superpowers/specs/2026-06-28-discord-chat-adapter-design.md`

**Base branch note:** This plan assumes a build branch off `main` (which carries the live `Heartbeat/` for the shared-bot co-existence and the droplet deploy). The design spec currently lives on `discord-chat-adapter` (off the truthlayer branch); cherry-pick/rebase the spec onto the `main`-based build branch, or merge as convenient.

**✋ Learning-mode note:** Three steps are marked **HANDS-ON** — the throttle policy (Task 6), the in-flight guard (Task 7), and the router dispatch (Task 9). These are genuine UX/cost trade-offs, not boilerplate. Each gives you a failing test + signature + guidance; try authoring the body yourself, then compare against the reference implementation provided below it.

---

## File Structure (interface contract — keep these signatures stable across tasks)

```
Concierge/
  concierge/
    __init__.py
    __main__.py          entrypoint: load config -> wire stores/client -> client.run()
    config.py            Config dataclass + load_config(env) -> Config
    session.py           make_session_id(user, loc) / parse_session_id(s) -> SessionRef
    identity.py          IdentityStore(db_path): resolve()/get()/close(); Identity dataclass
    render.py            chunk_message(), sources_embed(), escape_markdown()
    throttle.py          EditThrottle.should_flush()/mark_flushed()   [HANDS-ON]
    ratelimit.py         InFlightGuard.run(user, factory); QueueFullError  [HANDS-ON]
    router.py            InboundMessage, Router.route()/register_command()  [HANDS-ON]
    finsearch_client.py  iter_sse_data(), reduce_events(), FinSearchClient.stream_chat()
    handlers.py          chat_handler(msg, app) — orchestration
    bot.py               DiscordIO, register_handlers(), should_handle(), _strip_mention()
    app.py               AppContext (binds config + stores + DiscordIO for handlers)
  tests/
    test_config.py  test_session.py  test_identity.py  test_render.py
    test_throttle.py  test_ratelimit.py  test_router.py  test_finsearch_client.py
    test_bot.py  test_handlers.py
    fixtures/sse_chat_stream.txt
  systemd/concierge.service
  requirements.txt
  .env.concierge.example
  README.md
  .gitignore            (data/)
```

**Frozen types (defined once, referenced everywhere):**

```python
# config.py
@dataclass(frozen=True)
class Config:
    discord_bot_token: str
    finsearch_api_base: str
    finsearch_api_key: Optional[str]
    identity_db_path: str
    default_model: str = "gpt-4o-mini"
    request_timeout_s: float = 1260.0
    cooldown_s: float = 3.0
    edit_interval_s: float = 1.2
    edit_min_chars: int = 1500
    max_queue_per_user: int = 3

# session.py
@dataclass(frozen=True)
class SessionRef:
    discord_user_id: str
    location_id: str

# identity.py
@dataclass(frozen=True)
class Identity:
    discord_user_id: str
    finsearch_user_id: str
    created_at: str
    atl_account_id: Optional[str]   # RESERVED — always None in v1

# finsearch_client.py
@dataclass(frozen=True)
class ChatChunk:
    content: str
@dataclass(frozen=True)
class ChatResult:
    text: str
    used_sources: list
    used_urls: list
    truncated: bool

# router.py
@dataclass(frozen=True)
class InboundMessage:
    user_id: str
    location_id: str
    text: str
    is_dm: bool
```

**Task order (dependencies):** 0 scaffold → 1 config → 2 session → 3 identity → 4 render → 5 *(none; throttle)* → 6 throttle → 7 ratelimit → 8 finsearch_client → 9 router → 10 handlers → 11 bot → 12 app+entrypoint → 13 infra(systemd/env/README) → 14 CI.

---

## Task 0: Scaffold the package

**Files:**
- Create: `Concierge/concierge/__init__.py` (empty)
- Create: `Concierge/requirements.txt`
- Create: `Concierge/.gitignore`
- Create: `Concierge/tests/__init__.py` (empty)

- [ ] **Step 1: Create the package skeleton**

```bash
mkdir -p Concierge/concierge Concierge/tests/fixtures Concierge/systemd
touch Concierge/concierge/__init__.py Concierge/tests/__init__.py
```

- [ ] **Step 2: Write `Concierge/requirements.txt`**

```
discord.py>=2.4,<3
# dev/test
pytest>=8
pytest-asyncio>=0.23,<0.24   # strict mode + explicit @pytest.mark.asyncio (what the tests assume)
```

- [ ] **Step 3: Write `Concierge/.gitignore`**

```
data/
.venv/
__pycache__/
*.pyc
```

- [ ] **Step 4: Create the venv and install**

Run:
```bash
cd Concierge && python3.12 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt && ./.venv/bin/python -c "import discord, pytest; print('ok')"
```
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add Concierge/requirements.txt Concierge/.gitignore Concierge/concierge/__init__.py Concierge/tests/__init__.py
git commit -m "chore(concierge): scaffold Discord chat-adapter package"
```

---

## Task 1: `config.py` — load + validate env

**Files:**
- Create: `Concierge/concierge/config.py`
- Test: `Concierge/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest
from concierge.config import load_config, Config, ConfigError

def test_load_full_env():
    cfg = load_config({"DISCORD_BOT_TOKEN": "tok",
                       "FINSEARCH_API_BASE": "http://localhost:8000/"})
    assert isinstance(cfg, Config)
    assert cfg.discord_bot_token == "tok"
    assert cfg.finsearch_api_base == "http://localhost:8000"   # trailing slash stripped
    assert cfg.finsearch_api_key is None                       # absent -> None
    assert cfg.default_model == "gpt-4o-mini"

def test_missing_token_raises():
    with pytest.raises(ConfigError):
        load_config({"FINSEARCH_API_BASE": "http://x"})

def test_default_base_when_absent():
    cfg = load_config({"DISCORD_BOT_TOKEN": "tok"})
    assert cfg.finsearch_api_base == "http://localhost:8000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL (`ModuleNotFoundError: concierge.config`). Note: run pytest from `Concierge/` so `concierge` is importable, or add `pythonpath = .` (Task 14 adds `pytest.ini`).

- [ ] **Step 3: Write minimal implementation**

```python
# concierge/config.py
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class Config:
    discord_bot_token: str
    finsearch_api_base: str
    finsearch_api_key: Optional[str]
    identity_db_path: str
    default_model: str = "gpt-4o-mini"
    request_timeout_s: float = 1260.0
    cooldown_s: float = 3.0
    edit_interval_s: float = 1.2
    edit_min_chars: int = 1500
    max_queue_per_user: int = 3


class ConfigError(ValueError):
    pass


def load_config(env: Mapping[str, str]) -> Config:
    token = (env.get("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError("missing required env var: DISCORD_BOT_TOKEN")
    return Config(
        discord_bot_token=token,
        finsearch_api_base=(env.get("FINSEARCH_API_BASE") or "http://localhost:8000").rstrip("/"),
        finsearch_api_key=((env.get("FINGPT_API_KEY") or "").strip() or None),
        identity_db_path=(env.get("CONCIERGE_IDENTITY_DB") or "data/identity.sqlite"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_config.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add Concierge/concierge/config.py Concierge/tests/test_config.py
git commit -m "feat(concierge): config loader with env validation"
```

---

## Task 2: `session.py` — the per-location session-id contract

**Files:**
- Create: `Concierge/concierge/session.py`
- Test: `Concierge/tests/test_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session.py
import pytest
from concierge.session import make_session_id, parse_session_id, SessionRef

def test_round_trip():
    sid = make_session_id("123", "456")
    assert sid == "discord:123:456"
    assert parse_session_id(sid) == SessionRef("123", "456")

def test_parse_rejects_malformed():
    for bad in ["", "discord:123", "x:123:456", "discord::456", "discord:123:"]:
        with pytest.raises(ValueError):
            parse_session_id(bad)

def test_make_rejects_colon_in_ids():
    with pytest.raises(ValueError):
        make_session_id("12:3", "456")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_session.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# concierge/session.py
from dataclasses import dataclass

_PREFIX = "discord"


def make_session_id(discord_user_id: str, location_id: str) -> str:
    if not discord_user_id or not location_id:
        raise ValueError("discord_user_id and location_id are required")
    if ":" in discord_user_id or ":" in location_id:
        raise ValueError("ids must not contain ':'")
    return f"{_PREFIX}:{discord_user_id}:{location_id}"


@dataclass(frozen=True)
class SessionRef:
    discord_user_id: str
    location_id: str


def parse_session_id(session_id: str) -> SessionRef:
    parts = session_id.split(":")
    if len(parts) != 3 or parts[0] != _PREFIX or not parts[1] or not parts[2]:
        raise ValueError(f"malformed session_id: {session_id!r}")
    return SessionRef(discord_user_id=parts[1], location_id=parts[2])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_session.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add Concierge/concierge/session.py Concierge/tests/test_session.py
git commit -m "feat(concierge): per-location session-id contract (make/parse)"
```

---

## Task 3: `identity.py` — the durable identity store (ATL anchor)

**Files:**
- Create: `Concierge/concierge/identity.py`
- Test: `Concierge/tests/test_identity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_identity.py
from concierge.identity import IdentityStore, Identity

def test_resolve_creates_with_reserved_atl_none():
    store = IdentityStore(":memory:")
    ident = store.resolve("123", now_iso="2026-06-28T00:00:00+00:00")
    assert ident.discord_user_id == "123"
    assert ident.finsearch_user_id == "discord_123"      # deterministic
    assert ident.atl_account_id is None                   # reserved
    store.close()

def test_resolve_is_idempotent():
    store = IdentityStore(":memory:")
    a = store.resolve("123", now_iso="2026-06-28T00:00:00+00:00")
    b = store.resolve("123", now_iso="2099-01-01T00:00:00+00:00")  # later — must not overwrite
    assert a == b
    assert b.created_at == "2026-06-28T00:00:00+00:00"
    store.close()

def test_persists_across_reopen(tmp_path):
    db = str(tmp_path / "id.sqlite")
    s1 = IdentityStore(db); s1.resolve("123", now_iso="2026-06-28T00:00:00+00:00"); s1.close()
    s2 = IdentityStore(db)
    assert s2.get("123") is not None
    assert s2.get("999") is None
    s2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_identity.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# concierge/identity.py
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity (
    discord_user_id   TEXT PRIMARY KEY,
    finsearch_user_id TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    atl_account_id    TEXT
);
"""


@dataclass(frozen=True)
class Identity:
    discord_user_id: str
    finsearch_user_id: str
    created_at: str
    atl_account_id: Optional[str]


class IdentityStore:
    def __init__(self, db_path: str) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def get(self, discord_user_id: str) -> Optional[Identity]:
        row = self._conn.execute(
            "SELECT * FROM identity WHERE discord_user_id = ?", (discord_user_id,)
        ).fetchone()
        if row is None:
            return None
        return Identity(row["discord_user_id"], row["finsearch_user_id"],
                        row["created_at"], row["atl_account_id"])

    def resolve(self, discord_user_id: str, *, now_iso: str) -> Identity:
        existing = self.get(discord_user_id)
        if existing is not None:
            return existing
        finsearch_user_id = f"discord_{discord_user_id}"
        self._conn.execute(
            "INSERT INTO identity (discord_user_id, finsearch_user_id, created_at, atl_account_id) "
            "VALUES (?, ?, ?, NULL)",
            (discord_user_id, finsearch_user_id, now_iso),
        )
        self._conn.commit()
        return Identity(discord_user_id, finsearch_user_id, now_iso, None)

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_identity.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add Concierge/concierge/identity.py Concierge/tests/test_identity.py
git commit -m "feat(concierge): durable identity store (reserved atl_account_id)"
```

---

## Task 4: `render.py` — chunking, sources embed, escaping

**Files:**
- Create: `Concierge/concierge/render.py`
- Test: `Concierge/tests/test_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_render.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# concierge/render.py
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
        chunks.append(remaining[:cut].rstrip())
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_render.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add Concierge/concierge/render.py Concierge/tests/test_render.py
git commit -m "feat(concierge): message chunking + sources embed + md escape"
```

---

## Task 5: *(reserved — render covered the no-dependency tier; proceed to throttle)*

---

## Task 6: `throttle.py` — the streaming edit-throttle policy  ✋ HANDS-ON

**Files:**
- Create: `Concierge/concierge/throttle.py`
- Test: `Concierge/tests/test_throttle.py`

**Why this matters:** Discord rate-limits message edits (~5 per 5 s per channel). Edit too often → 429s and a janky UI; too rarely → the answer feels frozen. The policy that balances "feels live" vs "stays under the limit" is yours to set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_throttle.py
from concierge.throttle import EditThrottle

def test_no_flush_when_empty():
    t = EditThrottle(interval_s=1.2, min_chars=1500)
    assert t.should_flush(0, now_s=100.0) is False

def test_flush_after_interval():
    t = EditThrottle(interval_s=1.2, min_chars=1500)
    assert t.should_flush(10, now_s=0.0) is True          # first content, >interval since 0.0
    t.mark_flushed(10, now_s=0.0)
    assert t.should_flush(20, now_s=0.5) is False          # too soon, too few chars
    assert t.should_flush(20, now_s=1.5) is True           # interval elapsed

def test_flush_after_min_chars():
    t = EditThrottle(interval_s=1.2, min_chars=1500)
    t.mark_flushed(10, now_s=0.0)
    assert t.should_flush(1600, now_s=0.1) is True         # +1590 chars >= min_chars
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_throttle.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: ✋ Author `should_flush` yourself**

Signature to implement (in `concierge/throttle.py`):
```python
from dataclasses import dataclass

@dataclass
class EditThrottle:
    interval_s: float
    min_chars: int
    _last_flush_s: float = float("-inf")   # first content always clears the interval gate
    _last_len: int = 0

    def should_flush(self, accumulated_len: int, now_s: float) -> bool:
        ...  # YOUR LOGIC: never on empty; flush if enough time passed OR enough new chars

    def mark_flushed(self, accumulated_len: int, now_s: float) -> None:
        self._last_flush_s = now_s
        self._last_len = accumulated_len
```
Guidance: two independent triggers (time since last flush ≥ `interval_s`, OR chars since last flush ≥ `min_chars`), and never flush when `accumulated_len == 0`.

<details><summary>Reference implementation (compare after your attempt)</summary>

```python
def should_flush(self, accumulated_len: int, now_s: float) -> bool:
    if accumulated_len == 0:
        return False
    if now_s - self._last_flush_s >= self.interval_s:
        return True
    if accumulated_len - self._last_len >= self.min_chars:
        return True
    return False
```
</details>

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_throttle.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add Concierge/concierge/throttle.py Concierge/tests/test_throttle.py
git commit -m "feat(concierge): edit-throttle policy (interval OR char trigger)"
```

---

## Task 7: `ratelimit.py` — the per-user in-flight guard  ✋ HANDS-ON

**Files:**
- Create: `Concierge/concierge/ratelimit.py`
- Test: `Concierge/tests/test_ratelimit.py`

**Why this matters:** The spec chose *queue* (not reject) for a second message while one is in flight, plus a cooldown and a depth cap to bound abuse/token-burn. This is the trickiest hands-on point — concurrency policy.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ratelimit.py
import asyncio
import pytest
from concierge.ratelimit import InFlightGuard, QueueFullError

@pytest.mark.asyncio
async def test_serializes_same_user():
    order = []
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=5)
    async def job(tag):
        order.append(("start", tag)); await asyncio.sleep(0.01); order.append(("end", tag))
    await asyncio.gather(
        guard.run("u1", lambda: job("a")),
        guard.run("u1", lambda: job("b")),
    )
    # Same user: second job must not start before the first ends (queued).
    assert order in (
        [("start","a"),("end","a"),("start","b"),("end","b")],
        [("start","b"),("end","b"),("start","a"),("end","a")],
    )

@pytest.mark.asyncio
async def test_depth_cap_rejects():
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=1)
    async def slow(): await asyncio.sleep(0.05)
    running = asyncio.create_task(guard.run("u1", slow))   # holds the slot
    await asyncio.sleep(0.005)
    with pytest.raises(QueueFullError):                     # 2nd waiter exceeds cap=1
        await asyncio.gather(
            guard.run("u1", slow),
            guard.run("u1", slow),
        )
    await running

@pytest.mark.asyncio
async def test_other_users_not_blocked():
    guard = InFlightGuard(cooldown_s=0.0, max_queue_per_user=5)
    started = []
    async def job(tag): started.append(tag); await asyncio.sleep(0.02)
    await asyncio.gather(guard.run("u1", lambda: job("a")),
                         guard.run("u2", lambda: job("b")))
    assert set(started) == {"a", "b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_ratelimit.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: ✋ Author `InFlightGuard` yourself**

Signature to implement (in `concierge/ratelimit.py`):
```python
import asyncio
from dataclasses import dataclass, field


class QueueFullError(Exception):
    pass


@dataclass
class _UserState:
    lock: "asyncio.Lock" = field(default_factory=asyncio.Lock)
    waiting: int = 0
    last_done_s: float = 0.0


class InFlightGuard:
    def __init__(self, cooldown_s: float, max_queue_per_user: int):
        self._cooldown_s = cooldown_s
        self._max_queue = max_queue_per_user
        self._users: dict = {}

    async def run(self, user_id: str, coro_factory):
        ...  # YOUR LOGIC
```
Guidance: get-or-create a `_UserState`; if `waiting >= max_queue` raise `QueueFullError`; else increment `waiting`, `async with state.lock` (this is the *queue* — extras await the lock), enforce `cooldown_s` since `last_done_s` via `asyncio.sleep`, run `await coro_factory()`, record `last_done_s`, decrement `waiting`. Use `asyncio.get_event_loop().time()` for the clock.

<details><summary>Reference implementation (compare after your attempt)</summary>

```python
def _state(self, user_id):
    st = self._users.get(user_id)
    if st is None:
        st = _UserState()
        self._users[user_id] = st
    return st

async def run(self, user_id, coro_factory):
    st = self._state(user_id)
    if st.waiting >= self._max_queue:
        raise QueueFullError(user_id)
    st.waiting += 1
    try:
        async with st.lock:
            now = asyncio.get_event_loop().time()
            wait = self._cooldown_s - (now - st.last_done_s)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await coro_factory()
            finally:
                st.last_done_s = asyncio.get_event_loop().time()
    finally:
        st.waiting -= 1
```
</details>

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_ratelimit.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add Concierge/concierge/ratelimit.py Concierge/tests/test_ratelimit.py
git commit -m "feat(concierge): per-user in-flight guard (queue + cooldown + depth cap)"
```

---

## Task 8: `finsearch_client.py` — SSE client to the extension endpoint

**Files:**
- Create: `Concierge/concierge/finsearch_client.py`
- Create: `Concierge/tests/fixtures/sse_chat_stream.txt`
- Test: `Concierge/tests/test_finsearch_client.py`

**Note:** The protocol logic (`iter_sse_data`, `reduce_events`) is pure and fully tested against recorded frames. `stream_chat` is the thin async wrapper around `aiohttp`. **Re-capture** the fixture from a live call once the backend is reachable (`curl -N "$BASE/get_chat_response_stream/?question=hi&session_id=discord:1:1&models=gpt-4o-mini&use_memory=true"`); the inline fixture below is a representative shape.

- [ ] **Step 1: Write the fixture**

`Concierge/tests/fixtures/sse_chat_stream.txt`:
```
event: connected
data: {"status": "connected"}

data: {"status": {"label": "Preparing context"}}

data: {"content": "AAPL ", "done": false}

data: {"content": "is Apple.", "done": false}

data: {"content": "", "done": true, "wrapped_content": "AAPL is Apple.", "used_sources": [{"url": "http://x", "title": "X"}], "used_urls": ["http://y"]}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_finsearch_client.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_finsearch_client.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 4: Write implementation**

```python
# concierge/finsearch_client.py
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
    text = "".join(acc) or (final.get("wrapped_content") or "")
    return acc, ChatResult(text=text,
                           used_sources=final.get("used_sources") or [],
                           used_urls=final.get("used_urls") or [],
                           truncated=not done)


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
        text = "".join(acc) or (final.get("wrapped_content") or "")
        yield ChatResult(text=text,
                         used_sources=final.get("used_sources") or [],
                         used_urls=final.get("used_urls") or [],
                         truncated=not done)

    async def aclose(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
```

- [ ] **Step 5: Run test to verify it passes, then commit**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_finsearch_client.py -q`
Expected: 3 passed.
```bash
git add Concierge/concierge/finsearch_client.py Concierge/tests/test_finsearch_client.py Concierge/tests/fixtures/sse_chat_stream.txt
git commit -m "feat(concierge): SSE client for /get_chat_response_stream/ (pure reducers + async stream)"
```

---

## Task 9: `router.py` — handler dispatch  ✋ HANDS-ON

**Files:**
- Create: `Concierge/concierge/router.py`
- Test: `Concierge/tests/test_router.py`

**Why this matters:** v1 routes everything to chat. The dispatch *shape* determines how cheaply `/research` and `/strategy` slot in later. Keep it a one-liner now, but structured so commands register without touching callers.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router.py
import asyncio
from concierge.router import Router, InboundMessage

async def chat(msg, app): return "chat"
async def research(msg, app): return "research"

def _msg(text): return InboundMessage(user_id="1", location_id="2", text=text, is_dm=True)

def test_freeform_routes_to_chat():
    r = Router(chat)
    assert r.route(_msg("what is AAPL pe?")) is chat

def test_registered_command_routes_to_its_handler():
    r = Router(chat); r.register_command("/research", research)
    assert r.route(_msg("/research tesla")) is research

def test_unknown_slash_falls_back_to_chat():
    r = Router(chat)
    assert r.route(_msg("/nope hi")) is chat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_router.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: ✋ Author `Router.route` yourself**

Skeleton (in `concierge/router.py`):
```python
from dataclasses import dataclass
from typing import Awaitable, Callable

@dataclass(frozen=True)
class InboundMessage:
    user_id: str
    location_id: str
    text: str
    is_dm: bool

Handler = Callable[["InboundMessage", object], Awaitable[None]]

class Router:
    def __init__(self, chat_handler: Handler) -> None:
        self._chat_handler = chat_handler
        self._commands: dict = {}

    def register_command(self, name: str, handler: Handler) -> None:
        self._commands[name] = handler

    def route(self, msg: InboundMessage) -> Handler:
        ...  # YOUR LOGIC: leading "/token" in _commands -> that handler; else chat
```
Guidance: take the first whitespace-delimited token; if it starts with `/` and is a registered command, return that handler; otherwise return the chat handler (free-form fallback).

<details><summary>Reference implementation</summary>

```python
def route(self, msg: InboundMessage) -> Handler:
    token = msg.text.split(maxsplit=1)[0] if msg.text else ""
    if token.startswith("/") and token in self._commands:
        return self._commands[token]
    return self._chat_handler
```
</details>

- [ ] **Step 4: Run test to verify it passes, then commit**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_router.py -q`
Expected: 3 passed.
```bash
git add Concierge/concierge/router.py Concierge/tests/test_router.py
git commit -m "feat(concierge): handler router (chat now; command seam reserved)"
```

---

## Task 10: `handlers.py` — `chat_handler` orchestration

**Files:**
- Create: `Concierge/concierge/handlers.py`
- Test: `Concierge/tests/test_handlers.py`

**Design:** `chat_handler` talks only to `app` (an `AppContext`-shaped object) and `app.discord` (a `DiscordIO`-shaped object). Tests inject fakes — no discord.py, no network.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_handlers.py
import asyncio
import contextlib
import pytest
from concierge.handlers import chat_handler
from concierge.router import InboundMessage
from concierge.finsearch_client import ChatChunk, ChatResult
from concierge.identity import IdentityStore
from concierge.session import make_session_id
from concierge.throttle import EditThrottle

class FakeDiscord:
    def __init__(self): self.sent=[]; self.edits=[]; self.embeds=[]; self.followups=[]
    async def send(self, msg, content): self.sent.append(content); return {"id": len(self.sent)}
    async def edit(self, ph, content): self.edits.append(content)
    async def send_followup(self, msg, content): self.followups.append(content)
    async def send_embed(self, msg, embed): self.embeds.append(embed)
    def typing(self, msg): return contextlib.nullcontext()   # async-with no-op (Py3.10+)

class FakeFinSearch:
    def __init__(self, items): self._items=items
    async def stream_chat(self, **kw):
        for it in self._items: yield it

class FakeApp:
    def __init__(self, discord, finsearch):
        self.discord=discord; self.finsearch=finsearch
        self.identity=IdentityStore(":memory:")
        self._t=0.0
    def make_session(self, u, l): return make_session_id(u, l)
    def new_throttle(self): return EditThrottle(0.0, 1)   # always flush -> exercise edit path
    def clock(self): self._t+=1.0; return self._t
    def now_iso(self): return "2026-06-28T00:00:00+00:00"

def _msg(): return InboundMessage(user_id="1", location_id="2", text="hi", is_dm=True)

@pytest.mark.asyncio
async def test_happy_path_posts_placeholder_then_final_and_sources():
    d = FakeDiscord()
    f = FakeFinSearch([ChatChunk("AAPL "), ChatChunk("is Apple."),
                       ChatResult("AAPL is Apple.", [{"url":"http://x","title":"X"}], [], False)])
    app = FakeApp(d, f)
    await chat_handler(_msg(), app)
    assert d.sent[0] == "\U0001f4ad Thinking…"      # placeholder posted first
    assert d.edits[-1] == "AAPL is Apple."                # final edit is the full answer
    assert len(d.embeds) == 1                              # sources embed sent

@pytest.mark.asyncio
async def test_truncated_marks_cutoff():
    d = FakeDiscord()
    f = FakeFinSearch([ChatChunk("half"), ChatResult("half", [], [], True)])
    await chat_handler(_msg(), FakeApp(d, f))
    assert "cut off" in d.edits[-1]

@pytest.mark.asyncio
async def test_backend_error_shows_friendly_message():
    d = FakeDiscord()
    class Boom:
        async def stream_chat(self, **kw):
            if False: yield
            raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        await chat_handler(_msg(), FakeApp(d, Boom()))
    assert "Couldn't reach FinSearch" in d.edits[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_handlers.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write implementation**

```python
# concierge/handlers.py
from .render import DISCORD_MSG_LIMIT, chunk_message, sources_embed
from .finsearch_client import ChatChunk, ChatResult
from .router import InboundMessage

_THINKING = "\U0001f4ad Thinking…"   # 💭 Thinking…
_ERR = "⚠️ Couldn't reach FinSearch, try again in a moment."


def _preview(text: str) -> str:
    return text[:DISCORD_MSG_LIMIT] if len(text) > DISCORD_MSG_LIMIT else text


async def chat_handler(msg: InboundMessage, app) -> None:
    app.identity.resolve(msg.user_id, now_iso=app.now_iso())
    session_id = app.make_session(msg.user_id, msg.location_id)
    placeholder = await app.discord.send(msg, _THINKING)
    throttle = app.new_throttle()
    acc, result = "", None
    try:
        async with app.discord.typing(msg):          # "Bot is typing…" for the stream duration
            async for item in app.finsearch.stream_chat(
                question=msg.text, session_id=session_id,
                user_timezone="UTC", user_time=app.now_iso(),
            ):
                if isinstance(item, ChatChunk):
                    acc += item.content
                    now = app.clock()                # read the clock ONCE per chunk
                    if throttle.should_flush(len(acc), now):
                        throttle.mark_flushed(len(acc), now)
                        await app.discord.edit(placeholder, _preview(acc))
                elif isinstance(item, ChatResult):
                    result = item
    except Exception:
        await app.discord.edit(placeholder, _ERR)
        raise

    text = (result.text if result else acc) or "*(no response)*"
    if result and result.truncated:
        text += "\n\n*(response was cut off)*"
    parts = chunk_message(text)
    await app.discord.edit(placeholder, parts[0] if parts else "*(no response)*")
    for extra in parts[1:]:
        await app.discord.send_followup(msg, extra)
    if result:
        embed = sources_embed(result.used_sources, result.used_urls)
        if embed:
            await app.discord.send_embed(msg, embed)
```

- [ ] **Step 4: Run test to verify it passes, then commit**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_handlers.py -q`
Expected: 3 passed.
```bash
git add Concierge/concierge/handlers.py Concierge/tests/test_handlers.py
git commit -m "feat(concierge): chat_handler orchestration (placeholder -> stream -> final + sources)"
```

---

## Task 11: `bot.py` — discord.py wiring + `DiscordIO`

**Files:**
- Create: `Concierge/concierge/bot.py`
- Test: `Concierge/tests/test_bot.py`

**Design:** the pure decision helpers (`should_handle`, `_strip_mention`) are unit-tested; the discord.py event wiring (`register_handlers`) and `DiscordIO` are thin and verified by the live smoke test in the README (Task 13).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot.py
from concierge.bot import should_handle, _strip_mention

def test_should_handle_rules():
    assert should_handle(author_is_bot=False, is_dm=True, mentioned=False) is True
    assert should_handle(author_is_bot=False, is_dm=False, mentioned=True) is True
    assert should_handle(author_is_bot=False, is_dm=False, mentioned=False) is False
    assert should_handle(author_is_bot=True, is_dm=True, mentioned=True) is False

def test_strip_mention():
    assert _strip_mention("<@42> hello", 42).strip() == "hello"
    assert _strip_mention("<@!42> hi", 42).strip() == "hi"
    assert _strip_mention("no mention", 42) == "no mention"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_bot.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write implementation**

```python
# concierge/bot.py
import contextlib
import logging

import discord

from .ratelimit import QueueFullError
from .router import InboundMessage, Router

log = logging.getLogger("concierge")


def should_handle(*, author_is_bot: bool, is_dm: bool, mentioned: bool) -> bool:
    if author_is_bot:
        return False
    return is_dm or mentioned


def _strip_mention(content: str, bot_id: int) -> str:
    for token in (f"<@{bot_id}>", f"<@!{bot_id}>"):
        content = content.replace(token, "")
    return content


class DiscordIO:
    """Transport-thin wrapper so handlers never import discord.py types."""

    def __init__(self, client: discord.Client) -> None:
        self._client = client
        self._channels: dict = {}   # location_id -> live channel (avoids per-reply re-resolve)

    def remember_channel(self, channel) -> None:
        self._channels[str(channel.id)] = channel

    async def _channel(self, msg: InboundMessage):
        ch = self._channels.get(msg.location_id)
        if ch is not None:
            return ch
        cid = int(msg.location_id)
        ch = self._client.get_channel(cid) or await self._client.fetch_channel(cid)
        self._channels[msg.location_id] = ch
        return ch

    def typing(self, msg: InboundMessage):
        # "Bot is typing…" for the stream duration; reuse the remembered live channel.
        ch = self._channels.get(msg.location_id)
        return ch.typing() if ch is not None else contextlib.nullcontext()

    async def send(self, msg: InboundMessage, content: str):
        ch = await self._channel(msg)
        return await ch.send(content, allowed_mentions=discord.AllowedMentions.none())

    async def edit(self, message, content: str):
        return await message.edit(content=content,
                                  allowed_mentions=discord.AllowedMentions.none())

    async def send_followup(self, msg: InboundMessage, content: str):
        return await self.send(msg, content)

    async def send_embed(self, msg: InboundMessage, embed_dict: dict):
        ch = await self._channel(msg)
        return await ch.send(embed=discord.Embed.from_dict(embed_dict),
                             allowed_mentions=discord.AllowedMentions.none())


def register_handlers(client: discord.Client, app, router: Router, guard) -> None:
    @client.event
    async def on_message(message: discord.Message):
        me = client.user
        if me is None:                       # not logged in yet — ignore
            return
        is_dm = message.guild is None
        mentioned = me in message.mentions
        if not should_handle(author_is_bot=message.author.bot, is_dm=is_dm, mentioned=mentioned):
            return
        text = _strip_mention(message.content, me.id).strip()
        if not text:
            await message.channel.send("Ask me something \U0001f642",
                                       allowed_mentions=discord.AllowedMentions.none())
            return
        app.discord.remember_channel(message.channel)   # reuse the live channel (no REST re-resolve)
        inbound = InboundMessage(user_id=str(message.author.id),
                                 location_id=str(message.channel.id),
                                 text=text, is_dm=is_dm)
        handler = router.route(inbound)
        try:
            await guard.run(inbound.user_id, lambda: handler(inbound, app))
        except QueueFullError:
            await message.channel.send("⏳ I'm still working on your previous messages — one moment.",
                                       allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:            # spec §6: missing channel perms -> react ❌
            try:
                await message.add_reaction("❌")
            except discord.HTTPException:
                log.warning("no perms to react or reply for user %s", inbound.user_id)
        except Exception:
            log.exception("handler failed for user %s", inbound.user_id)

    @client.event
    async def on_interaction(interaction: discord.Interaction):
        # SEAM: future Confirm/Cancel buttons + slash commands. No-op in v1.
        log.info("interaction received (ignored in v1): %s", getattr(interaction, "type", "?"))
```

- [ ] **Step 4: Run test to verify it passes, then commit**

Run: `cd Concierge && ./.venv/bin/python -m pytest tests/test_bot.py -q`
Expected: 2 passed.
```bash
git add Concierge/concierge/bot.py Concierge/tests/test_bot.py
git commit -m "feat(concierge): discord.py wiring (on_message filter, DiscordIO, on_interaction seam)"
```

---

## Task 12: `app.py` + `__main__.py` — context + entrypoint

**Files:**
- Create: `Concierge/concierge/app.py`
- Create: `Concierge/concierge/__main__.py`

- [ ] **Step 1: Write `app.py`**

```python
# concierge/app.py
import asyncio
import datetime as dt

from .config import Config
from .identity import IdentityStore
from .finsearch_client import FinSearchClient
from .session import make_session_id
from .throttle import EditThrottle


class AppContext:
    """Everything chat_handler needs, injectable for tests."""

    def __init__(self, cfg: Config, identity: IdentityStore,
                 finsearch: FinSearchClient, discord_io) -> None:
        self.cfg = cfg
        self.identity = identity
        self.finsearch = finsearch
        self.discord = discord_io

    def make_session(self, user_id: str, location_id: str) -> str:
        return make_session_id(user_id, location_id)

    def new_throttle(self) -> EditThrottle:
        return EditThrottle(self.cfg.edit_interval_s, self.cfg.edit_min_chars)

    def clock(self) -> float:
        return asyncio.get_event_loop().time()

    def now_iso(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat()
```

- [ ] **Step 2: Write `__main__.py`**

```python
# concierge/__main__.py
import logging
import os

import discord

from .app import AppContext
from .bot import DiscordIO, register_handlers
from .config import load_config
from .finsearch_client import FinSearchClient
from .handlers import chat_handler
from .identity import IdentityStore
from .ratelimit import InFlightGuard
from .router import Router


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(os.environ)

    identity = IdentityStore(cfg.identity_db_path)
    finsearch = FinSearchClient(cfg.finsearch_api_base, cfg.finsearch_api_key,
                                cfg.request_timeout_s, cfg.default_model)
    guard = InFlightGuard(cfg.cooldown_s, cfg.max_queue_per_user)
    router = Router(chat_handler)

    intents = discord.Intents.default()
    # Message content is delivered ONLY for DMs + messages that @mention us (Discord
    # platform exemption) — so we need NO privileged Message Content intent. A future
    # NON-mentioning trigger (prefix command, history scan) would read empty content
    # until that privileged intent is enabled.
    intents.message_content = False
    client = discord.Client(intents=intents)

    app = AppContext(cfg, identity, finsearch, DiscordIO(client))
    register_handlers(client, app, router, guard)

    try:
        client.run(cfg.discord_bot_token, log_handler=None)
    finally:
        identity.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify it imports + Discord intents are correct**

Run:
```bash
cd Concierge && ./.venv/bin/python -c "import os; os.environ['DISCORD_BOT_TOKEN']='x'; from concierge.__main__ import main; from concierge.app import AppContext; print('import ok')"
```
Expected: prints `import ok` (no network; we don't call `main()`).

- [ ] **Step 4: Commit**

```bash
git add Concierge/concierge/app.py Concierge/concierge/__main__.py
git commit -m "feat(concierge): AppContext + entrypoint wiring (no privileged intent)"
```

---

## Task 13: Runtime — systemd unit, env example, README, pytest config

**Files:**
- Create: `Concierge/systemd/concierge.service`
- Create: `Concierge/.env.concierge.example`
- Create: `Concierge/README.md`
- Create: `Concierge/pytest.ini`

- [ ] **Step 1: Write `Concierge/pytest.ini`** (so `pytest` finds the package + async mode)

```ini
[pytest]
pythonpath = .
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 2: Write `Concierge/systemd/concierge.service`** (a `deploy`-user service)

```ini
[Unit]
Description=Agentic FinSearch - Discord conversational chat adapter (Concierge)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/deploy/fingpt/concierge
EnvironmentFile=/home/deploy/fingpt/envs/.env.concierge
ExecStart=/home/deploy/fingpt/concierge/.venv/bin/python -m concierge
Restart=on-failure
RestartSec=5
MemoryHigh=256M
MemoryMax=384M

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Write `Concierge/.env.concierge.example`**

```
# The SAME Discord application/token as the Heartbeat bot (the Heartbeat opens no
# Gateway connection, so one identity / two processes coexist). Enable the Gateway
# + the two NON-privileged intents (GUILD_MESSAGES, DIRECT_MESSAGES) in the Dev Portal.
DISCORD_BOT_TOKEN=

# Co-located API container; prefer localhost over the public host.
FINSEARCH_API_BASE=http://localhost:8000

# Optional. Usually unset — the extension endpoints aren't Bearer-gated.
# FINGPT_API_KEY=

# Optional. Durable identity store (the ATL anchor). Default: data/identity.sqlite
# CONCIERGE_IDENTITY_DB=data/identity.sqlite
```

- [ ] **Step 4: Write `Concierge/README.md`**

````markdown
# Concierge — Agentic FinSearch Discord chat adapter

Interactive sibling of the News Heartbeat. A persistent `discord.py` Gateway service
that maps free-form @mention/DM messages onto the **existing** FinSearch extension
pipeline (`/get_chat_response_stream/`) and posts replies back. Backend is unchanged.

See `Docs/superpowers/specs/2026-06-28-discord-chat-adapter-design.md`.

## Develop & test
```bash
cd Concierge
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m pytest -q          # all tests, no network
```

## Run locally
```bash
cp .env.concierge.example .env.concierge   # fill DISCORD_BOT_TOKEN
set -a && . ./.env.concierge && set +a
./.venv/bin/python -m concierge
```

## Live smoke (manual)
1. Start the FinSearch backend reachable at `FINSEARCH_API_BASE`.
2. Run the service; in Discord, DM the bot or `@mention` it in a channel: "what is AAPL's PE?"
3. Expect a `💭 Thinking…` placeholder that streams into the answer, then a Sources embed.

## Deploy (droplet, `deploy` user — manual, mirrors the Heartbeat)
```bash
ssh finsearch-deploy 'mkdir -p ~/fingpt/concierge ~/fingpt/envs ~/.config/systemd/user'
rsync -a --exclude .venv --exclude data Concierge/ finsearch-deploy:/home/deploy/fingpt/concierge/
ssh finsearch-deploy 'cd ~/fingpt/concierge && python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt'
scp Concierge/.env.concierge.example finsearch-deploy:/home/deploy/fingpt/envs/.env.concierge   # then fill + chmod 600
scp Concierge/systemd/concierge.service finsearch-deploy:/home/deploy/.config/systemd/user/
ssh finsearch-deploy 'chmod 600 ~/fingpt/envs/.env.concierge && systemctl --user daemon-reload && systemctl --user enable --now concierge.service'
```

## Discord-side config (separate session — out of scope here)
Same application as the Heartbeat. Enable **Gateway** + the **non-privileged**
`GUILD_MESSAGES` and `DIRECT_MESSAGES` intents. **Do not** enable the privileged
Message Content intent — mentions & DMs deliver content without it.
````

- [ ] **Step 5: Re-run the full suite (pytest.ini now active) + commit**

Run: `cd Concierge && ./.venv/bin/python -m pytest -q`
Expected: all tests pass (run from `Concierge/` — `pytest.ini` sets `pythonpath=.`).
```bash
git add Concierge/pytest.ini Concierge/systemd/concierge.service Concierge/.env.concierge.example Concierge/README.md
git commit -m "chore(concierge): systemd unit, env example, README, pytest config"
```

---

## Task 14: CI — gate `Concierge/**`

**Files:**
- Create: `.github/workflows/concierge-tests.yml`

- [ ] **Step 1: Write the workflow** (mirrors `heartbeat-tests.yml` path-partitioning)

```yaml
name: concierge-tests
on:
  push:
    paths: ["Concierge/**", ".github/workflows/concierge-tests.yml"]
  pull_request:
    paths: ["Concierge/**", ".github/workflows/concierge-tests.yml"]

jobs:
  test:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: Concierge
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: python -m pytest -q
```

- [ ] **Step 2: Validate the workflow locally (YAML well-formed)**

Run: `cd Concierge && ./.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('../.github/workflows/concierge-tests.yml')); print('yaml ok')"`
Expected: `yaml ok`. (If `yaml` isn't installed: `./.venv/bin/pip install pyyaml` first, dev-only.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/concierge-tests.yml
git commit -m "ci(concierge): gate Concierge/** with the pytest suite"
```

---

## Final verification

- [ ] **Run the whole suite**

Run: `cd Concierge && ./.venv/bin/python -m pytest -q`
Expected: all tests pass (~24 across 10 files).

- [ ] **Confirm no privileged intent + no backend change**

Run: `cd Concierge && grep -rn "message_content" concierge/` → expect exactly `intents.message_content = False`.
Confirm: nothing under `Main/backend/` was modified (this plan touches only `Concierge/` and `.github/workflows/`).

---

## Spec coverage map (self-review)

| Spec section | Task(s) |
|---|---|
| §2 Approach A (reuse extension endpoint) | 8 (`stream_chat` → `/get_chat_response_stream/`), 12 |
| §2 free-form @mention/DM, no privileged intent | 11 (`should_handle`, `_strip_mention`), 12 (`message_content=False`) |
| §2 thinking-only; research deferred | 8 (`is_advanced=false`), 9 (command seam) |
| §4 durable identity (reserved `atl_account_id`) | 3 |
| §4 ephemeral per-location `session_id` contract | 2 |
| §5 flow (placeholder + typing → stream → throttled edits → final + sources) | 10, 11 |
| §6 backend-down / truncated / friendly errors; `Forbidden → ❌ react` | 8, 10, 11 |
| §6 `allowed_mentions:none`; cost guard; 429 `Retry-After` (discord.py rate-limiter) | 11 (AllowedMentions.none), 7 (in-flight guard) |
| §7 handler router seam; `on_interaction` skeleton | 9, 11 |
| §8 module layout; same-token coexistence; discord.py venv | 0, 12, 13 |
| §9 logic-first tests + recorded SSE fixture + CI gating | 1–11, 8 (fixture), 14 |

**Deferred per spec (no task, intentional):** `/research` & `/strategy` slash commands, ATL MCP tools, the Confirm/Cancel *flow* (only the `on_interaction` seam exists), per-thread scoping, a Validate button. **Adaptive 429 throttle-widening** is also deferred — discord.py's built-in rate-limiter already respects `Retry-After` (the spec's core 429 requirement); only the *adaptive widening* refinement is dropped for v1.
