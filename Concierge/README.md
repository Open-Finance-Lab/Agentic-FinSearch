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

## Discord-side setup & testing
Same Discord **application** as the News Heartbeat — one app, one token, two processes.
The Heartbeat is REST-only (`Bot {token}`, no Gateway); Concierge is the **sole** Gateway
consumer. A token allows only ONE live Gateway connection, so never run two Concierge
processes against it.

### 1. Developer Portal — <https://discord.com/developers/applications>
- **Token** — reuse the Heartbeat's `DISCORD_BOT_TOKEN`. **Do not** click *Reset Token*;
  that invalidates the Heartbeat's copy.
- **Privileged Gateway Intents** — leave **all three OFF** (Presence, Server Members,
  Message Content). The message intents Concierge needs (`GUILD_MESSAGES`,
  `DIRECT_MESSAGES`) are *non-privileged*: they are requested in code via
  `Intents.default()` and have **no Dev-Portal toggle**. Discord still delivers message
  content for DMs and @mentions with Message Content OFF (platform exemption), which is
  exactly the trigger surface we use.
- **Invite the bot** — it needs, per channel it serves: *View Channel, Send Messages,
  Embed Links, Add Reactions* (those four = integer `19520`). The recommended invite below
  also grants *Read Message History* (optional today, used by a future history-scan
  trigger) — scope `bot`, permissions integer `85056`:
  `https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot&permissions=85056`

### 2. Configure & run
Follow **Run locally** above. `FINSEARCH_API_BASE` must be reachable from wherever
Concierge runs (it streams from the backend's `/get_chat_response_stream/`). On the
droplet the backend is co-located, so the default `http://localhost:8000` works; from a
laptop, point it at a tunnel (e.g. `ssh -L 8000:localhost:8000 finsearch-deploy`).

### 3. Test matrix
Beyond the happy path in **Live smoke (manual)** above:

| Input | Expected |
|-------|----------|
| `@Concierge what is AAPL's PE?` (server) | `💭 Thinking…` → throttled streamed answer → Sources embed |
| `what is AAPL's PE?` (DM, no @mention) | same — DMs need no mention |
| `@Concierge` with no text | `Ask me something 🙂` — **no** backend call |
| 4+ rapid messages from one user | extras queue (cooldown); when the per-user queue (3) fills → `⏳ I'm still working…` |
| bot lacks Send/Embed perms in the channel | ❌ reaction on your message |

### 4. Refresh the SSE fixture (optional)
`tests/fixtures/sse_chat_stream.txt` is the recorded byte stream the client tests replay.
To re-capture from a live backend (e.g. after a backend frame-format change), save the raw
`/get_chat_response_stream/` response body verbatim — including `event:`/`data:` lines and
the terminal `{"done": true, ...}` frame — over that file, then re-run `pytest -q`.
