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
