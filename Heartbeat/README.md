# Heartbeat

Proactive news retrieval for the Agentic FinSearch community: a daily (24-hour
heartbeat) pipeline that fetches Yahoo Finance news, aggregates and de-duplicates
it, ranks importance, summarizes via one cheap LLM call (deterministic extractive
fallback), writes a digest log, and posts it to a Discord channel.

- **Design:** `Docs/superpowers/specs/2026-06-10-news-heartbeat-design.md`
- **Discord bot setup (for going live):** `Heartbeat/DISCORD_BOT_SETUP.md`
- **Strategy anchor:** `InternalDocs/strategy/COMMUNITY_KICKSTART.md` (see the
  2026-06-10 proposed amendment)

## Why it looks like this

The production droplet is 1 vCPU / 2 GB RAM with no pip on the host, so
`news_heartbeat.py` is a **single stdlib-only Python file** — deploying is
copying one file; running costs ~30 MB RSS. It is fully decoupled from the
`fingpt-api` container. Summaries are short, attributed, and always link out to
the source article (Yahoo ToS posture: orchestration, not raw-data
redistribution).

## Run

```bash
# tests (no network, fixtures are real captures of the live endpoints)
cd Heartbeat && python3 -m unittest discover -s tests

# manual run (Yahoo is UA-gated and geo-fussy: run from the droplet, not WSL)
python3 news_heartbeat.py --dry-run --env-file /home/deploy/fingpt/envs/.env.heartbeat
```

Outputs land in `$HEARTBEAT_HOME/digests/`: `digest-YYYY-MM-DD.md` (the human
log) and `items-YYYY-MM-DD.jsonl` (every normalized story with scores).
`state.json` remembers seen stories for 7 days so a story is never posted twice.

## Deploy (droplet, as `deploy`)

Merging to `main` deploys automatically: `.github/workflows/heartbeat-tests.yml`
runs the test suite, then copies `news_heartbeat.py` to the droplet (checksum-
verified, byte-compiled, atomically installed). Only the script auto-deploys —
the systemd units, directories, and env file below are droplet config and stay
manual.

```bash
# first time only: directories + config
ssh finsearch-deploy 'mkdir -p ~/fingpt/heartbeat/digests ~/fingpt/envs ~/.config/systemd/user'
scp Heartbeat/.env.heartbeat.example finsearch-deploy:/home/deploy/fingpt/envs/.env.heartbeat
ssh finsearch-deploy 'chmod 600 ~/fingpt/envs/.env.heartbeat'   # then fill it in

# manual deploy (first install, unit changes, or CI fallback)
scp Heartbeat/news_heartbeat.py finsearch-deploy:/home/deploy/fingpt/heartbeat/
scp Heartbeat/systemd/finsearch-heartbeat.* \
    finsearch-deploy:/home/deploy/.config/systemd/user/
ssh finsearch-deploy 'systemctl --user daemon-reload &&
  systemctl --user enable --now finsearch-heartbeat.timer'

# drift check — repo and droplet must match before debugging anything
sha256sum Heartbeat/news_heartbeat.py
ssh finsearch-deploy 'sha256sum ~/fingpt/heartbeat/news_heartbeat.py'

# one-off beat + logs (prefer systemctl over bare python3: systemd serializes
# concurrent starts; the script also holds a flock either way)
ssh finsearch-deploy 'systemctl --user start finsearch-heartbeat.service'
ssh finsearch-deploy 'journalctl --user -u finsearch-heartbeat.service -n 50'
```

Config lives in `/home/deploy/fingpt/envs/.env.heartbeat` (template:
`.env.heartbeat.example`). The unit ships with `HEARTBEAT_DRY_RUN=1`; flipping
to live Discord posting is Part 4 of `DISCORD_BOT_SETUP.md`.

Exit codes: `0` digest written (or legitimately nothing new) · `1` Discord
delivery failed after retries · `2` every feed failed (network/Yahoo outage) ·
`3` another run already in progress. Dry runs never touch `state.json`; a
second same-day beat writes `digest-YYYY-MM-DD-HHMMSS.md` instead of
overwriting. Logs older than 90 days are pruned automatically.

## News → signals (news_signals.py)

Turns each `digests/items-*.jsonl` batch into a per-ticker sentiment artifact
`signals/signals-<same-stem>.json` (design + contracts:
`Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md`).
A 20-minute systemd user timer sweeps unprocessed batches (tracked in
`signals_state.json`); one batched LLM call per batch; artifacts are written
atomically **before** state, so a crash costs a duplicate LLM call, never a
silent gap. A daily canary alerts on Discord when the newest artifact is
older than `SIGNALS_STALENESS_ALERT_H` (default 20 h — tuned so a single
fully-missed day reliably fires; see the timer's own comment for the
arithmetic).

Manual run (same env file as the heartbeat):

    python3 news_signals.py --env-file ~/fingpt/envs/.env.heartbeat
    python3 news_signals.py --canary --env-file ~/fingpt/envs/.env.heartbeat

Exit codes: 0 ok (including poison-pill batches, by design), 1 canary-stale,
2 config error, 3 another run holds the lock.

Deploy (droplet, systemd --user, mirrors the heartbeat):

    ssh finsearch-deploy 'mkdir -p ~/fingpt/heartbeat/signals'
    scp Heartbeat/news_signals.py finsearch-deploy:/home/deploy/fingpt/heartbeat/
    scp Heartbeat/systemd/finsearch-signals.* \
        Heartbeat/systemd/finsearch-signals-canary.* \
        finsearch-deploy:/home/deploy/.config/systemd/user/
    ssh finsearch-deploy 'systemctl --user daemon-reload &&
      systemctl --user enable --now finsearch-signals.timer finsearch-signals-canary.timer'

Universe change (spec D2) — set on the droplet in
`~/fingpt/envs/.env.heartbeat` (35 tickers: heartbeat default ∪ DJIA-30;
ATL's bogus `AMEX` slot is deliberately excluded — it can never have data):

    HEARTBEAT_WATCHLIST=AAPL AMZN AXP BA BRK-B BTC-USD CAT CSCO CVX DIS GOOGL GS HD IBM INTC JNJ JPM KO MA MCD META MMM MRK MSFT NKE NVDA PFE PG TRV TSLA UNH V WBA WMT XOM

Staging checklist (run once at deploy time, spec §9):
1. Drop two items files back-to-back; `systemctl --user start finsearch-signals.service`;
   confirm both artifacts exist and a second start is a no-op.
2. Start a run and `kill -9` the python process mid-LLM-call; confirm
   `signals_state.json` is unchanged and the next sweep reprocesses cleanly.
3. `SIGNALS_STALENESS_ALERT_H=0.001 python3 news_signals.py --canary --env-file …`
   fires the Discord alert.
