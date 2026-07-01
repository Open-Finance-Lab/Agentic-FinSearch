# Discord Bot Setup — Agentic FinSearch News Heartbeat

**Audience:** Felix (FlyM1ss). You know your way around APIs; this is just your first Discord bot.
**Goal:** create the **Agentic FinSearch** bot, invite it with minimal permissions, create the
read-only `#market-news` channel, and point the droplet heartbeat at it.
**Design context:** `Docs/superpowers/specs/2026-06-10-news-heartbeat-design.md`.

The heartbeat only ever does one thing on Discord: `POST /api/v10/channels/{channel_id}/messages`
with a bot token. **No gateway connection, no discord.py, no privileged intents.** Discord's REST
API works standalone; the WebSocket Gateway is a separate, optional system we don't use.

> Note: Discord's developer docs now live at `docs.discord.com/developers/...`
> (old `discord.com/developers/docs/...` links 301-redirect there).

## Prerequisites

- A Discord account that **owns** (or has Manage Server on) the Agentic FinSearch Community server.
- SSH access to the droplet as `deploy` (heartbeat already installed, currently `HEARTBEAT_DRY_RUN=1`).
- 15 minutes.

## Part 1 — Create the app + bot

1. Open <https://discord.com/developers/applications> and sign in.
2. Click **Create App** (top right). Name it exactly `Agentic FinSearch`. Accept the developer ToS, create.
3. You land on **General Information**. Optional but recommended: set the description to something
   factual, e.g. *"Posts a daily sourced market-news digest. FinSearch outputs are not financial advice."*
   Note the **Application ID** here — you need it in Part 2.
4. Go to the **Bot** page (left sidebar).
   - Under **Token**, click **Reset Token** (new apps don't show a token until you generate one).
     Confirm, then **copy the token immediately** — Discord never shows it again; you can only reset it.
   - Turn **OFF** the **Public Bot** toggle. This controls whether other users can install your app;
     off means only you can add it to servers. Click **Save Changes**.
   - Leave all three **Privileged Gateway Intents** (Presence, Server Members, Message Content) **OFF**.
     REST-only posting needs none of them.

### Token hygiene (read this once, seriously)

- The token *is* the bot's password. Anyone holding it can post as Agentic FinSearch.
- **Never** commit it, paste it into the repo, an issue, or a chat log.
- It lives in exactly two places: your password manager, and
  `/home/deploy/fingpt/envs/.env.heartbeat` on the droplet (mode 600, owner `deploy`).
- If it ever leaks: Developer Portal → Bot → **Reset Token**, then update the droplet env.
  (Discord auto-resets tokens it finds in public GitHub repos — don't rely on that.)

## Part 2 — Invite the bot to the server (minimal permissions)

The bot needs exactly three permissions. Verified bit values:

| Permission    | Bit     | Decimal |
|---------------|---------|---------|
| View Channel  | 1 << 10 | 1024    |
| Send Messages | 1 << 11 | 2048    |
| Embed Links   | 1 << 14 | 16384   |
| **Total**     |         | **19456** |

(Embed Links matters: the digest posts as rich embeds. Discord's Create Message docs only hard-require
Send Messages, but Embed Links is the permission governing embed/link rendering — grant it so the
embeds are never suppressed.)

1. Build the invite URL — substitute your Application ID from Part 1:

   ```text
   https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&scope=bot&permissions=19456
   ```

   (Equivalent: Developer Portal → **OAuth2** → URL Generator → scope `bot` → tick
   View Channel, Send Messages, Embed Links — it generates the same URL.)
2. Open the URL in a browser where you're logged in, pick the Agentic FinSearch server,
   click **Continue**, leave all three permissions checked, click **Authorise**.
3. The bot now appears in the member list (offline — normal and permanent: it never connects
   to the gateway, it only fires REST calls once a day). Discord also auto-created a managed
   role named **Agentic FinSearch** holding those permissions — you'll use it in Part 3.

## Part 3 — Create read-only #market-news and grab its ID

1. In the server: **+ Create Channel** → type **Text** → name `market-news` → Create.
2. Make it read-only for members, writable for the bot. Channel settings (gear icon) →
   **Permissions** → **Advanced permissions**:
   - `@everyone`: **View Channel ✓ (allow)**, **Send Messages ✗ (deny)**. Also deny
     **Create Public Threads / Create Private Threads / Add Reactions** if you want it fully quiet.
   - Click **+** next to Roles/Members, add the **Agentic FinSearch** role:
     **View Channel ✓**, **Send Messages ✓**, **Embed Links ✓** (allow).
   - Channel-level role allows override the `@everyone` deny — that's Discord's documented
     overwrite order (base perms → @everyone overwrites → role overwrites).
3. Post and **pin** the disclaimer yourself (right-click the message → Pin Message):
   > FinSearch outputs are not financial advice.
4. Get the channel ID:
   - **User Settings** (cogwheel, bottom left) → **Advanced** → toggle **Developer Mode** on.
   - Right-click the `#market-news` channel in the sidebar → **Copy Channel ID**.
   - It's a ~19-digit number (a snowflake). That's `DISCORD_CHANNEL_ID`.

## Part 4 — Wire the droplet and flip dry-run off

SSH to the droplet as `deploy`, then:

```bash
nano /home/deploy/fingpt/envs/.env.heartbeat
```

Add the two new lines and change the dry-run flag (keep everything else as-is):

```bash
DISCORD_BOT_TOKEN=paste-the-token-here
DISCORD_CHANNEL_ID=paste-the-channel-id-here
HEARTBEAT_DRY_RUN=0
```

No quotes, no `Bot ` prefix in the env value (the script adds the `Authorization: Bot ...`
header itself). Then lock the file down and run a manual beat:

```bash
chmod 600 /home/deploy/fingpt/envs/.env.heartbeat
systemctl --user start finsearch-heartbeat.service
journalctl --user -u finsearch-heartbeat.service -n 50
```

## Part 5 — Verify

- `#market-news` shows 1–2 messages from **Agentic FinSearch**, each with up to 10 embeds,
  clickable story links, and the attribution + disclaimer footer.
- `journalctl` output above ends with a successful delivery line, exit code 0.
- A fresh digest pair exists on the droplet:
  `ls -lt /home/deploy/fingpt/heartbeat/digests/ | head` →
  `digest-YYYY-MM-DD.md` and `items-YYYY-MM-DD.jsonl` dated today.
- Done. The timer (`finsearch-heartbeat.timer`) fires daily at 11:00 UTC; nothing else to do.

### Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| HTTP 401 Unauthorized | Bad/expired token, or header isn't exactly `Authorization: Bot <token>` | Re-copy the token (Reset Token if lost); check for stray quotes/whitespace in the env file |
| HTTP 403, code 50001 "Missing Access" | Bot can't see the channel | Bot not invited to this server, or `@everyone` View Channel deny without an allow for the Agentic FinSearch role — fix Part 3 step 2 |
| HTTP 403, code 50013 "Missing Permissions" | Bot sees the channel but can't post | Allow **Send Messages** (and **Embed Links**) for the bot role in the channel overwrites |
| HTTP 404 "Unknown Channel" | Wrong `DISCORD_CHANNEL_ID` | You probably copied the **server** ID or a message ID — right-click the *channel* → Copy Channel ID |
| HTTP 429 | Rate limited (per-route, or global 50 req/s) | The script already honors `Retry-After`/`retry_after` with backoff; at 1–2 messages/day you should never see this — if you do, something else is using the token |
| Embeds show raw `[title](url)` text | Masked link placed in an embed **title** — titles don't render markdown links | Put masked links in the embed `description` or field values (or hyperlink the title via the embed's `url` field) |
| Bot shows offline | Expected | REST-only bots never appear online; posting still works |

## Appendix — curl smoke test (before involving the heartbeat)

Run this from anywhere (droplet or laptop) to prove token + channel + permissions work,
independently of the heartbeat code:

```bash
export DISCORD_BOT_TOKEN='paste-token'
export DISCORD_CHANNEL_ID='paste-channel-id'

curl -sS -X POST \
  "https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/messages" \
  -H "Authorization: Bot ${DISCORD_BOT_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [{
      "title": "Agentic FinSearch — wiring test",
      "description": "If you can read this and [this link](https://finance.yahoo.com) is clickable, the bot is wired correctly. This test message can be deleted.",
      "footer": { "text": "FinSearch outputs are not financial advice." }
    }]
  }'
```

Success = JSON response containing an `"id"` and your embed, and the message visible in
`#market-news`. A `code`/`message` JSON error instead maps onto the troubleshooting table above.
Delete the test message afterwards (you can; members can't — it's read-only).

Then unset the vars in your shell (`unset DISCORD_BOT_TOKEN DISCORD_CHANNEL_ID`) and, if you
ran it on a shared machine, clear it from history (`history -d` or start the command with a space).
