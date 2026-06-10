# News Heartbeat — Design

**Date:** 2026-06-10
**Status:** Implemented (v1) — autonomous decisions pending Felix ratification (§13)
**Component:** `Heartbeat/` (new top-level monorepo directory)
**Author:** Claude (Fable 5), commissioned by FlyM1ss

## 1. Purpose

Give the Agentic FinSearch Community Discord a reason to exist on day one: a **proactive
information retrieval** service that fetches Yahoo Finance news on a 24-hour heartbeat,
aggregates and de-duplicates it, curates the important stories, summarizes them, and posts
a sourced, linked digest into a read-only INFO channel.

This executes the recurring-content programming layer of
`InternalDocs/strategy/COMMUNITY_KICKSTART.md` (v2, 2026-05-26): the plan's own words are
that *"silence kills momentum"* and *"empty channels signal this is dead"*, and it requires
pre-seeding 3–5 posts per channel. The heartbeat industrializes that cadence from three
manual posts per week to one automated daily digest — and doubles as a daily public demo of
the product's retrieval → aggregation → summarization pipeline. Per
`POSITIONING_STRATEGY.md`: *"the value is orchestration, not raw data."*

It also extends the 2026-01-15 architecture decision
(`Docs/plans/2026-01-15-playwright-integration-design.md`: Yahoo Finance MCP for numerical
data, direct scraping for news) from a pull model (user asks) to a push model (heartbeat
publishes).

## 2. Constraints (discovered 2026-06-10, all verified live)

| Constraint | Consequence |
|---|---|
| Droplet: 1 vCPU, 2 GB RAM; `fingpt-api` container capped at 1.7 GB; ~1 GB free | Heartbeat must be lightweight; no pandas/yfinance on host |
| No `pip` on droplet host (stdlib `venv`+`ensurepip` exist but unused) | **Stdlib-only script** → zero-install deploy |
| Yahoo endpoints are UA-gated: no browser User-Agent ⇒ instant 429/404 | Always send Chrome UA (same one as `datascraper/url_tools.py`) |
| Any unknown `/rss/*` path returns a **fake-alive copy of topstories** (HTTP 200 + valid XML) | Source health checks must verify content identity, not status codes |
| Yahoo Finance ToS prohibits commercial data redistribution (kickstart §triggers) | Digest = short summaries + attribution + links **out** to Yahoo; never republish article text; community/educational framing |
| Kickstart governance: *"No third-party bots until actually needed"* | This is a **first-party product bot**; policy amendment proposed (§13) |
| LLM token burn is a standing open issue | Exactly **one** cheap LLM call per heartbeat (default `gpt-4o-mini`, the repo's established summarizer precedent from `url_tools._smart_compress`) |
| Discord renders masked links `[t](url)` only inside **embeds**, not message content | Deliver digest as embeds (≤4096 chars/description, ≤6000/message) |

## 3. Approaches considered

- **A. Extend the Django backend** (management command in `Main/backend`) — rejected:
  couples the heartbeat to the delicate API container; any dep change triggers container
  rebuilds; an API outage would kill the news feed too.
- **B. uv project with yfinance on the droplet host** (repo-recon suggestion) — rejected:
  yfinance drags pandas/numpy onto a 1-vCPU/2GB box with no pip; install weight and memory
  risk buy us only the `summary` field, which per-ticker RSS `description` + capped
  `og:description` enrichment already covers.
- **C. Stdlib-only standalone script + systemd user timer — CHOSEN.** One Python file,
  `urllib`/`xml.etree`/`json` only. Zero installs, ~30 MB RSS, fully decoupled from the API
  container, testable anywhere.
- **D. `podman exec` into the running container for yfinance** — rejected: couples the
  heartbeat to container uptime and turns the API container into a single point of failure
  for community content.

## 4. Architecture

```
fetch ──► normalize ──► dedupe ──► window ──► rank ──► curate+summarize ──► render ──► deliver
 (3+N        (one         (guid/    (24 h,     (det.      (ONE LLM call,      (md log     (dry-run
  feeds)      schema)      URL)      state)     score)      JSON out;           + jsonl)    or Discord
                                                            extractive                      embeds)
                                                            fallback)
```

Single file: `Heartbeat/news_heartbeat.py`. Stages are pure functions over a common
`Story` dict; only `fetch`, `summarize`, `deliver` touch the network.

### Sources (all verified live from the droplet, 2026-06-10)

| Source | Endpoint | Role | Notes |
|---|---|---|---|
| Market-wide A | `https://finance.yahoo.com/rss/topstories` | primary breadth | ~42 items, ISO-8601 dates, **no description** |
| Market-wide B | `https://finance.yahoo.com/news/rssindex` | merged w/ A | ~40 items, ~70% overlap w/ A → union ≈ 50 unique/day |
| Per-ticker | `https://feeds.finance.yahoo.com/rss/2.0/headline?s={T}&region=US&lang=en-US` | watchlist depth | 20 items/ticker, RFC-822 dates, **only feed with description** |
| Enrichment | article page `og:description` (bounded read ≤256 KB) | top market-wide stories only, cap 8/run | full-article fetch verified working from droplet US IP; no consent wall |

Watchlist default: `AAPL MSFT NVDA GOOGL AMZN META TSLA BRK-B JPM BTC-USD`
(env-overridable, `HEARTBEAT_WATCHLIST`). Volume: ≤15 polite requests/run, ≥1 s apart —
far below observed limits; channel `ttl` is 5 min so daily polling is trivial.

### Normalize / dedupe / window

- One `Story` schema: `guid, title, link, source, published (epoch), description, tickers, feeds`.
- Both pubDate formats parsed (ISO-8601 and RFC-822).
- Dedupe key: RSS guid (== basename of article URL); cross-feed merge unions `tickers`/`feeds`.
- Near-dup collapse: normalized-title token-set Jaccard ≥ 0.7 keeps the higher-scored item.
- Window: `published` within last `HEARTBEAT_WINDOW_HOURS` (default 24) **and** guid not in
  state file (`state.json`: guid → first-seen epoch, pruned at 7 days) → no repeats across runs.

### Rank (deterministic, no LLM)

Score = source-tier weight (wire/major outlets > aggregator-promotional)
+ corroboration bonus (story in ≥2 feeds, or ≥2 tickers)
+ watchlist-ticker bonus
+ keyword boosts (earnings, Fed, rates, SEC, acquisition, guidance, bankruptcy, upgrade/downgrade …)
− listicle/personal-finance penalty ("best credit cards", "renters insurance", "X things to…")
+ recency tiebreak. Top 25 candidates proceed; everything is logged to the JSONL either way.

### Curate + summarize (the only LLM stage)

One `chat.completions` call (raw HTTPS via urllib; `HEARTBEAT_MODEL`, default
`gpt-4o-mini`, temperature 0.3, JSON response): input = candidates (title, source, age,
tickers, description ≤ 300 chars); output = strict JSON — a 2–3 sentence **market pulse**
overview, a curated `market` section and `companies` section (each item: story id, 1–2
sentence summary, no advice, no hype). Editorial rules in the prompt mirror the kickstart
tone: factual, sourced, accuracy-first, **"FinSearch outputs are not financial advice."**

**Fallback (LLM key missing / call fails / invalid JSON):** extractive digest — top items by
deterministic score, first sentence of description as summary. The heartbeat never skips a
beat because a vendor API hiccuped.

### Render + deliver

- Always: `digests/digest-YYYY-MM-DD.md` (human log) + `digests/items-YYYY-MM-DD.jsonl`
  (full normalized item dump) + journald via systemd.
- `HEARTBEAT_DRY_RUN=1` (current default): stop after render.
- Live: Discord Bot REST `POST /api/v10/channels/{id}/messages` (no gateway, no persistent
  process, no discord.py): 1–2 messages, each ≤10 embeds, masked links in embed
  descriptions, footer = attribution + disclaimer. 3 retries, exponential backoff, honors
  `Retry-After` on 429. Bot token + channel id via env; **no token exists yet** — see
  `Heartbeat/DISCORD_BOT_SETUP.md`.

## 5. Operations

- **Unit:** systemd **user** units under `deploy` (Linger=yes verified):
  `finsearch-heartbeat.service` (oneshot, `MemoryMax=256M`, `Nice=10`) +
  `finsearch-heartbeat.timer` (`OnCalendar=*-*-* 11:00 UTC` ≈ 7 am ET pre-market,
  `Persistent=true`, `RandomizedDelaySec=300`).
- **Layout on droplet:** script + state + digests under `/home/deploy/fingpt/heartbeat/`;
  env at `/home/deploy/fingpt/envs/.env.heartbeat` (only `OPENAI_API_KEY` + heartbeat vars —
  deliberately *not* sourcing `.env.production`, least privilege).
- **Failure policy:** a dead source is skipped (content-identity check, §2); LLM failure →
  extractive fallback; Discord failure → 3 retries, then state is already persisted so the
  next beat cannot double-post — the run logs ERROR and exits 1. The run never crash-loops
  (oneshot, next beat in 24 h). Exit codes: 0 = digest written or legitimately nothing new;
  1 = Discord delivery failed; 2 = every feed failed (fails the unit loudly — covers the
  post-reboot catch-up beat firing before the network is up, since `network-online.target`
  does not exist in the user manager); 3 = concurrent run (flock). Dry runs never touch
  `state.json`; same-day re-beats write timestamped supplemental files instead of
  overwriting; digests are pruned after 90 days.
- **Hardening (2026-06-10 review):** feed text is treated as attacker-controlled — square
  brackets are substituted before entering markdown/embed link syntax (masked-link
  injection), enrichment fetches are gated to `*.yahoo.com` with redirects disabled (SSRF →
  cloud metadata/localhost), embeds carry `allowed_mentions: {parse: []}` and the
  disclaimer footer on every message. Candidate `IPAddressDeny`/`ProtectSystem` unit
  sandboxing was deliberately deferred: unverified under the *user* manager on Fedora 42,
  and the code-level guards cover the reachable risk.

## 6. Testing

Fixtures are **real captures** from the 2026-06-10 droplet probes
(`tests/fixtures/`: topstories, rssindex, per-ticker AAPL/NVDA, search JSON). Unit tests
(stdlib `unittest`, no deps) cover: both RSS schemas + both date formats, single-line-XML
parsing, dedupe/merge, near-dup collapse, windowing + state pruning, ranking order,
extractive fallback, digest rendering, Discord embed chunking limits, LLM-response
validation. Network and LLM are injected/fake in tests.

## 7. Traceability

| Section | Anchor |
|---|---|
| Purpose, cadence, channel rules, tone, disclaimer | `InternalDocs/strategy/COMMUNITY_KICKSTART.md` v2 |
| "Orchestration, not raw data"; Yahoo ToS posture | `InternalDocs/strategy/POSITIONING_STRATEGY.md` |
| Yahoo news architecture lineage | `Docs/plans/2026-01-15-playwright-integration-design.md` |
| UA + scrape patterns, gpt-4o-mini summarizer precedent | `Main/backend/datascraper/url_tools.py` |
| Endpoint facts, droplet facts | live probes 2026-06-10 (recon, saved fixtures) |

## 8. Autonomous decisions pending Felix ratification

1. **New INFO channel** `#market-news` (read-only) — not in the kickstart channel plan;
   amendment proposed in `InternalDocs/strategy/COMMUNITY_KICKSTART.md` (marked PROPOSED).
2. **Bot policy amendment** — first-party FinSearch bot vs. the "no third-party bots" line.
3. **Stdlib-only over yfinance** (§3 B vs C) — trades the rich `summary` field for
   zero-dependency ops on the weak droplet.
4. **11:00 UTC daily** beat (pre-market ET). Easily re-timed in the timer unit.
5. Default watchlist + `gpt-4o-mini` + dry-run-first rollout.
