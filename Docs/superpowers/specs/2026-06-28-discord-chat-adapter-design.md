# Discord Chat Adapter — Conversational FinSearch on Discord (reuse the extension pipeline) — Design

- **Date:** 2026-06-28
- **Status:** Approved (design); implementation plan to follow
- **Author:** FlyMiss
- **Scope:** v1 conversational bridge (free-form @mention/DM → thinking mode) over the **existing** FinSearch pipeline, plus the forward-compat seams for future strategy control (ATL). Discord-side application/permission/intent configuration is **out of scope** (handled in a separate session).
- **Related:** `Heartbeat/news_heartbeat.py` (the passive feed this complements); central-db `decisions/2026-03.md` (2026-03-21 OpenClaw channel-adapter precedent); central-db `knowledge/xbrl-truth-layer-atl-forward-compat.md` (ATL bridge + `as_of` forward-compat philosophy); `2026-06-26-xbrl-truth-layer-p0p1-design`.

## 1. Context & goal

**Today.** Two relevant pieces exist, and they don't touch:

- **News Heartbeat** (`Heartbeat/news_heartbeat.py`) — a stdlib-only, REST-only **one-way poster**. A systemd *timer* runs it daily; it fires `POST`s to Discord's REST API and exits. It opens **no** Gateway connection, so it *cannot receive messages*. This is the "passive feed" the user wants to make interactive — but this file structurally can't be, so the interactive surface is a new component.
- **FinSearch backend** — Django REST at `agenticfinsearch.org`. The browser extension calls `/get_chat_response_stream/` (thinking, SSE) and `/get_adv_response_stream/` (research, SSE). Sessions are keyed by a client-supplied `session_id` → `UnifiedContextManager` (Django cache, **1 h TTL**) holding conversation history + fetched context. The agent entry point is `datascraper.create_agent_response()`.
- **Strategies / portfolios** are **not** in the backend. "Control your strategies" maps to **ATL** (Agent Trading Lab — separate FastAPI+SQLite+Alpaca platform; the FinSearch→ATL bridge is forward-compat-*designed* but not built). Strategy control will arrive as the **backend agent gaining ATL MCP tools** (backtest / paper-trade / live-trade), not as logic in this adapter.

**Goal of this unit.** Turn the passive feed into a chatbot users **talk to directly** on Discord, by adding a thin **ingress adapter** that maps Discord events onto the *same request contract the extension already speaks*, and posts replies back. **No second pipeline. The backend is untouched.**

**Governing principle (OpenClaw precedent, 2026-03-21).** A channel adapter does *transport only*, decoupled from the executor, holding no credentials/logic — "security blast radius contained." Here: the adapter holds only a Discord token + the FinSearch API base URL (+ an optional, currently-unused API key). The agent, the model API keys, and the context manager all stay in the backend.

## 2. Locked decisions (and why)

| Decision | Choice | Rationale |
|---|---|---|
| Runtime / reuse shape | **Approach A — a separate Gateway adapter service that calls the existing extension HTTP endpoints** ("headless extension") | Only option satisfying both *free-form chat* and the *thin-decoupled-adapter* constraint. Rejected: (B) importing `datascraper` in-process (the bot would need every backend dep + the model keys → blast radius; re-implements the view layer); (C) a Django `/discord/interactions/` endpoint (slash-commands only — conflicts with free-form; 15-min follow-up window can't cover 20-min research calls). |
| Invocation | **Free-form @mention + DM** | "Talk directly" = natural language. Gateway bot, but **no privileged Message Content intent** — Discord grants message content for @mentions and DMs without it, so no app verification. |
| Mode routing | **Every free-form message → thinking mode** (`/get_chat_response_stream/`) | Keeps normal chat a single, fast path with zero extra user gesture. Research is deferred to a future `/research` *slash command* — no reaction/keyword hack bolted onto normal chat (explicit user call). |
| Identity model | **Two layers: durable identity (never expires) + ephemeral conversation session (rides the 1 h cache TTL)** | A future ATL brokerage binding must be permanent; conversation context is *meant* to evaporate. Conflating them would make a broker link vanish every idle hour. Same "separate the permanent from the point-in-time" instinct as the truth-layer `as_of` work. |
| Conversation scoping | **Per-location** — `session_id = "discord:{discord_user_id}:{location_id}"`, `location_id` = channel/DM id | A DM is one continuous thread; each channel keeps its own memory. The format is a written-down contract that also lets the backend later recover *who is acting* for ATL tool calls. |
| Dependency posture | **Adopt `discord.py`** in its own venv on the droplet | Deliberate, documented break from the heartbeat's stdlib-only rule. A hand-rolled stdlib Gateway client (WebSocket + resume + rate-limits) is hundreds of lines of fragile protocol code; `discord.py` is the maintained standard. |
| Bot identity | **Same Discord application/token as the heartbeat** | Discord allows one Gateway connection per bot; the heartbeat opens *none* (REST-only). So one "Agentic FinSearch" identity, two processes, no conflict — and the bot stops showing permanently offline. |
| Backend changes | **None** | The whole point: a new ingress on the same body. |

## 3. Architecture & data flow

```
  Discord  ──@mention / DM──▶  ┌──────────────────────────────────────────────┐
                               │   Concierge — Discord Adapter Service         │
                               │   [droplet, systemd *service*, persistent]    │
                               │                                              │
                               │   bot.py        Gateway client: on_message    │
                               │                 (DM + mention; no priv intent)│
                               │                 on_interaction (skeleton)     │
                               │   identity.py   Layer-1 durable store ◀── SQLite
                               │   session.py    Layer-2 session_id fmt/parse   │
                               │   router.py     handler dispatch (chat now)    │
                               │   ratelimit.py  per-user in-flight guard       │
                               │   finsearch_client.py  SSE → chunks + final ──┐│
                               │   render.py     chunks→throttled edits,       ││
                               │                 2000-char split, sources embed││
                               └───────────────────────────────────────────────┼┘
                                                                               │ HTTP, same
                                                                               │ contract as
                                                                               ▼ the extension
                               ┌──────────────────────────────────────────────┐
                               │   EXISTING FinSearch backend (UNCHANGED)      │
                               │   /get_chat_response_stream/ → datascraper    │
                               │   agent → (future) ATL MCP tools.             │
                               │   Holds the model keys, the agent, the cache. │
                               └──────────────────────────────────────────────┘
```

Flow: `Discord message → resolve identity + session_id → SSE call to the extension endpoint → stream chunks back as throttled message edits → final edit with sources embed`. The adapter persists nothing beyond the Layer-1 identity record; the backend owns all conversation state.

## 4. Identity & session model (two layers)

**Layer 1 — durable identity (the ATL anchor).** One record per Discord user, **never expires**, stored in SQLite on the droplet:

```
identity(
  discord_user_id  TEXT PRIMARY KEY,
  finsearch_user_id TEXT,            -- stable id we mint on first contact
  created_at        TEXT,            -- ISO 8601
  atl_account_id    TEXT             -- RESERVED: NULL today, bound when ATL arrives
)
```

`atl_account_id` is a reserved column nobody reads yet — the exact pattern as the truth layer's reserved `entity_tickers` history: write the column now, fill it later. This is the *one expensive-to-undo* seam, so it is built in v1.

**Layer 2 — conversation session (the context window).** A `session_id` handed to the backend's existing endpoints, **ephemeral** (rides the backend's 1 h cache TTL → bounded memory, natural reset after idle):

```
session_id = "discord:{discord_user_id}:{location_id}"
  location_id = DM channel id  (DMs)  |  text channel id  (guild mentions)
```

The format is a **written-down contract** doing double duty: same user + same place ⇒ continuity, *and* the backend can parse "who is acting" from the session string later when the agent calls an ATL tool. `parse_session_id()` is provided so that contract is testable from day one.

## 5. Message flow (free-form chat — the v1 happy path)

1. **`on_message`** — keep only (a) DMs and (b) guild messages mentioning the bot; drop the bot's own messages and other bots; strip the mention prefix to recover the raw question. Empty question → "Ask me something 🙂".
2. **Resolve** Layer-1 identity (create on first contact) and the Layer-2 `session_id`.
3. **In-flight guard** — if this user already has a request running, politely defer/queue (see §6); else mark in-flight.
4. **Instant feedback** — post a `💭 Thinking…` placeholder and start the typing indicator (Gateway has no hard ack deadline; UX should feel instant).
5. **Call the pipeline** — open an SSE connection to `/get_chat_response_stream/` with the extension's exact params: `question`, `session_id`, `models` (default `gpt-4o-mini`), `is_advanced=false`, `use_memory=true`, `current_url="https://discord.com"`, and `user_timezone`/`user_time` (default UTC / now — we don't know the user's tz).
6. **Stream → edit** — accumulate `content` chunks; edit the placeholder on a **throttle** (~every 1.2 s or ~1500 chars) to stay under Discord's ~5-edits / 5 s / channel ceiling.
7. **Finish** — on the `done:true` frame, final-edit with the complete answer split into ≤2000-char messages on word boundaries; render `used_sources` / `used_urls` as a compact embed (reuse the heartbeat's escape/embed helpers). `has_axiom_claims:true` is reserved to later attach a "Validate" button (a seam, not built).
8. **Release** the in-flight guard.

**Concurrency.** Each request runs as its own `asyncio` task so the Gateway heartbeat never blocks (research can run ~20 min). The per-user in-flight guard prevents spam and context races on the shared per-location session.

## 6. Error handling, resilience & safety

| Failure | Handling |
|---|---|
| Backend down / 5xx / restart (deploys bounce the API) | Edit placeholder → `⚠️ Couldn't reach FinSearch, try again in a moment.` Bot stays up. Client read timeout ~1260 s (just above the backend's 1200 s gunicorn ceiling). |
| SSE drops mid-stream (no `done`) | Finalize with the partial text + a `(response cut off)` marker rather than losing it. |
| Discord **429** on edits | Respect `Retry-After`, widen the edit throttle — reuse the heartbeat's backoff logic. |
| Missing channel perms (`Forbidden`) | React ❌ on the user's message, or fall back to DM. |
| Gateway disconnect | `discord.py` auto-resumes; an in-flight reply that fails to post just logs. |
| Crash | systemd `Restart=on-failure`; the on-disk identity store survives; in-flight requests are lost (acceptable — user re-asks). |
| Second request while one is in flight | In-flight guard: queue (preferred) or politely reject with a one-line notice. |

**Safety (reusing the heartbeat's hardening):**
- Every reply sets `allowed_mentions:{parse:[]}` so the bot cannot be weaponized to mass-ping `@everyone`.
- Output escaping reuses the heartbeat's helpers; user text is passed only as the `question` param (the agent already handles arbitrary web input — no new injection surface the backend doesn't already own).
- The adapter holds **no model keys** → a compromised bot cannot drain OpenAI/Anthropic credits (OpenClaw blast-radius containment).
- **Cost/abuse:** the per-user in-flight guard + a simple per-user cooldown. Token burn is a known project issue, so this knob is first-class.

## 7. Forward-compat seams (build now / write down now / defer)

| Seam | v1 action | Why |
|---|---|---|
| **Durable identity (Layer 1)** | **Build now** | Only expensive-to-undo thing; a future ATL account binds here. |
| **Parseable `session_id` contract** | **Write down now** (`make/parse_session_id`) | Backend can recover "who is acting" for ATL tool calls later — a contract, not early code. |
| **Handler router** (dispatch) | **Build now (thin)** | `/research`, `/strategy` slot in additively; the abstraction costs ~nothing. |
| **Interactive Confirm/Cancel buttons** | **Skeleton + documented contract, not built** | Money-touching ATL actions need a human-in-the-loop confirm. Wire an empty `on_interaction` and write down the flow: *agent emits a "needs confirmation" payload → adapter renders buttons → click sends a confirmation back to the agent*. |
| **ATL MCP layer** (backtest / paper / live tools) | **Out of scope** | Backend concern. The agent *gains tools*; the adapter never changes. |
| `/research`, `/strategy` slash commands | **Out of scope (declared)** | Additive later, no spine change. |

**Punchline:** strategy control = the backend agent + ATL MCP tools. The adapter stays pure transport; the *only* strategy-related work it will ever need is identity (built now) and a confirmation UI (skeletoned now).

## 8. Module layout & runtime

```
Concierge/                         (name TBD — interactive counterpart to Heartbeat/)
  bot.py               discord.py client; on_message (DM+mention), on_interaction skeleton; wiring
  identity.py          Layer-1 durable store (SQLite): resolve_user(discord_id) -> finsearch_user_id
  session.py           Layer-2: make_session_id(discord_id, location_id) / parse_session_id(s)
  router.py            handler dispatch (chat now; /research, /strategy declared) — the seam
  finsearch_client.py  async SSE client → yields content chunks + the final {sources, urls, ...}
  render.py            chunk_2000(text), sources_embed(...), throttle policy, escaping (reuse heartbeat helpers)
  ratelimit.py         per-user in-flight guard + cooldown
  config.py            env loading: DISCORD_BOT_TOKEN, FINSEARCH_API_BASE, FINGPT_API_KEY (optional)
  data/                identity.sqlite (gitignored)
  tests/               pure-logic units + recorded SSE fixtures
  systemd/concierge.service
  .env.concierge.example
  README.md
```

**Runtime:** systemd **service** (not a timer) — `Restart=on-failure`, `MemoryHigh≈256M` (discord.py needs more headroom than the heartbeat's 96 M), runs as `deploy`, own venv. Env file mode 600 at `/home/deploy/fingpt/envs/.env.concierge`. `FINSEARCH_API_BASE` prefers `localhost`/the co-located API container over the public host. `FINGPT_API_KEY` is optional/reserved (the extension endpoints aren't Bearer-gated; the adapter calls them exactly as the extension does).

**Discord-side (out of scope, documented for the other session):** enable the Gateway + the two *non-privileged* intents `GUILD_MESSAGES` and `DIRECT_MESSAGES` on the existing application; keep the heartbeat's REST-only flow as-is.

## 9. Testing strategy

Keep the bulk **logic-first, transport-thin** so it is fast pure-Python:

- **Unit (no network):** identity resolution + first-contact creation; `session_id` format/parse round-trip; 2000-char word-boundary chunking; mention-stripping; the edit-throttle policy; the in-flight guard / cooldown; sources→embed rendering; `allowed_mentions` invariant. Mirrors the heartbeat's ~50 stdlib tests.
- **SSE client:** test `finsearch_client.py` against **recorded real frames** captured from `/get_chat_response_stream/` (the way the heartbeat captured live RSS fixtures) — including the partial-stream / no-`done` case.
- **Handlers:** `discord.py` `on_message` via mocked `Message` objects (DM, mention, bot-self, empty).
- **CI:** a new workflow gating the `Concierge/**` path, `paths`-partitioned from the API pipeline like `heartbeat-tests.yml`.

## 10. Out of scope / deferred

- Discord application/permission/intent configuration (separate session).
- `/research` and `/strategy` slash commands (declared seams only).
- The ATL MCP layer and any trade/backtest/paper-trade logic.
- The interactive confirmation *flow* (skeleton + contract only).
- A "Validate" button on claim-bearing answers (`has_axiom_claims` reserved).
- Discord-thread-scoped conversations (per-thread scoping — a possible later UX upgrade over per-location).

## 11. Open items (defaults chosen; override on review)

- **Service directory name** — proposed `Concierge/` (the one that serves users, vs `Heartbeat/` the passive pulse). Alternatives: `DiscordBot/`, `ChatBridge/`.
- **Per-user rate-limit defaults** — proposed: 1 in-flight request + a short cooldown (e.g. a few seconds); tune against observed token burn.
- **In-flight collision behavior** — proposed: *queue* the second request (vs. reject with a notice).

## 12. Implementation decision points (hands-on)

Spots where the meaningful logic — business rules with multiple valid approaches — is best authored by hand rather than scaffolded, and is small (~5–10 lines each):

- **`ratelimit.py` — the in-flight guard / cooldown policy** (queue vs. reject; cooldown window; per-user vs. global). A UX-vs-cost trade-off.
- **`render.py` — the streaming edit-throttle policy** (flush cadence vs. Discord's edit rate-limit; how to batch). A responsiveness-vs-rate-limit trade-off.
- **`router.py` — the dispatch shape** (how chat / future `/research` / `/strategy` are selected) — the abstraction that determines how cheaply later commands slot in.
