# News → Signals Pipeline — Design Spec

**Date:** 2026-07-06
**Status:** Formats pinned; producer-side prototype validated against real prod data. ATL-side adapter, Django endpoint, and droplet deployment are specified here but built in a future session.
**Relates to:** `Docs/superpowers/specs/2026-06-10-news-heartbeat-design.md` (producer), `/mnt/d/Documents/ATL Materials/FinSearch-to-ATL-Integration-Plan.html` (Plan 1), ATL repo `dashboard/backend/api/v2/models.py` (frozen consumer contract).

---

## 1. Goal & scope

Turn the News Heartbeat feed into per-ticker sentiment signals consumable by the Agent Trading Lab (ATL), whose consumer side already shipped (typed `NewsSentimentEntry`, fail-closed loader importing `dashboard.backend.integrations.news_sentiment.get_news_sentiment(universe, timestamp)`).

**This session delivers:** pinned signal/API formats (this doc + JSON Schema), the `news_signals.py` computation design, and a real sample run logged for human review.

**Future sessions deliver:** droplet deployment (systemd units), Django `GET /api/signals/news/` endpoint, ATL-side adapter + fixture, universe env change on prod.

**Explicit non-goals now:** raw-news public endpoint, frontend "News Sentiment" tab, per-user tuning UI, historical no-lookahead signal cache, second news producer.

Guardrail (advisor): signals are **observational/measurement only**, not alpha-seeking. ATL agents threshold scores into decisions themselves; the signal layer never fires trades.

## 2. Decisions log

| # | Decision | Choice | Notes |
|---|----------|--------|-------|
| D1 | Bridge (Gap 2) | **Live HTTP + fixture mode** | FinSearch serves signals over HTTP; ATL adapter fetches live for paper-style runs, reads a committed fixture for deterministic backtests/CI. |
| D2 | Universe (Gap 3 / A1) | **Union watchlist via env** | `HEARTBEAT_WATCHLIST` = current list ∪ DJIA-30 (~36 tickers). No code change. ATL's `DJIA_30` contains `"AMEX"` (almost certainly a typo for `AMGN`; `AXP` already covers American Express) — that slot will simply never have data; consumers must tolerate absent tickers. |
| D3 | Exposure scope (Gap 4) | **Signals endpoint only** | `/api/news` raw endpoint + frontend tab deferred. |
| D4 | Compute placement | **FinSearch side, batch, event-driven on batch arrival** | One batched LLM call per items batch; artifact precomputed; no LLM in any request path. |
| D5 | Trigger mechanism | **systemd timer sweep (15–30 min), provisional** | Chosen while user AFK per architecture-review panel recommendation; the scan is trigger-agnostic so swapping to a path unit later is a one-line change. User may override. |
| D6 | Sentiment method v1 | **Batched gpt-4o-mini call** (OpenAI-compatible, same key discipline as heartbeat digest) | FinBERT-class local inference does not fit the droplet (128MB unit cap / ~300MB free RAM); a future swap of `compute_sentiment` internals is an **infra decision** (hosted inference or new host), interface unchanged. |
| D7 | Retention-prune interlock | **Declined** | Making the heartbeat's prune aware of signals state couples stages the design deliberately keeps ignorant of each other. Residual risk (pipeline dead AND canary ignored for 90 days) accepted; the staleness canary (§6-C) covers realistic cases. |

## 3. Architecture & data flow

Contract-first pipes-and-filters; stages communicate only through durable file artifacts with pinned schemas; no central coordinator; every boundary fails closed to "no data".

```
[any news producer]                     today: Heartbeat/news_heartbeat.py (daily 11:00 UTC)
        │  atomic write (temp + os.replace — REQUIRED PATCH, mirrors state.json idiom)
        ▼
$HEARTBEAT_HOME/digests/items-YYYY-MM-DD[-HHMMSS].jsonl          ← INPUT CONTRACT (§4.1)
        │  finsearch-signals.timer (OnUnitActiveSec=20min sweep; see D5)
        ▼
finsearch-signals.service (oneshot) → Heartbeat/news_signals.py
        │  1. flock guard (same pattern as heartbeat)
        │  2. scan digests/ for items files not in signals_state.json
        │  3. validation gate (§7.1): size caps, JSONL parse, field caps,
        │     published sanity, control/bidi strip  — poison pill → §6.1
        │  4. candidate selection: ticker-tagged stories, editorial score gate,
        │     per-ticker cap
        │  5. ONE batched LLM call → per-ticker {score, guid, rationale} (§4.3)
        │  6. guid-membership check per ticker; join guid → real story fields;
        │     clamp; corroboration damping; derive label
        ▼  atomic write, artifact BEFORE state update (§6.2)
$HEARTBEAT_HOME/signals/signals-YYYY-MM-DD[-HHMMSS].json         ← OUTPUT CONTRACT (§4.2)
        │        (own directory — narrows the future :ro mount, §7.2)
        ▼  (future session)
GET /api/signals/news/  — Django view, serves latest artifact (§4.4)
        ▼  (future session)
ATL get_news_sentiment(universe, timestamp) — projection rules (§4.5)
```

Each `items-X.jsonl` yields exactly one `signals-X.json` (same stem) — traceable and diffable. Every batch covers a rolling 24 h window, so the latest artifact is self-contained; nothing ever merges.

## 4. Pinned contracts

### 4.1 Input contract — items JSONL (existing, restated)

One JSON object per line:

| Field | Type | Req | Semantics |
|-------|------|-----|-----------|
| `guid` | str | ✓ | Unique per story within a batch; dedup/join key |
| `title` | str | ✓ | Raw headline |
| `link` | str | ✓ | Article URL |
| `source` | str | ✓ | Publisher name |
| `published` | float | ✓ | Unix epoch UTC |
| `description` | str |  | Excerpt (may be empty) |
| `tickers` | list[str] |  | Uppercase symbols; `[]` = market-wide |
| `feeds` | list[str] |  | Which feeds carried the story |
| `score` | float | ✓ | **Editorial relevance** on the heartbeat's deterministic scale (source tier + corroboration + keywords − penalties, roughly 0–10). NOT sentiment. Any future producer must calibrate to this scale — this is part of the contract. |

### 4.2 Output contract — signals artifact v1

File: `$HEARTBEAT_HOME/signals/signals-<stem>.json` where `<stem>` matches the source items file. Atomic write (temp in same dir + `os.replace`).

```json
{
  "schema_version": 1,
  "profile": "default",
  "generated_at": "2026-07-06T12:34:56+00:00",
  "generator": "news_signals.py/2026-07-06.1",
  "model": "gpt-4o-mini",
  "prompt_version": 1,
  "source_items": "items-2026-07-06.jsonl",
  "window_hours": 24,
  "watchlist": ["AAPL", "MSFT", "..."],
  "status": "ok",
  "status_reason": null,
  "news_overview": "One-line market synthesis (≤300 chars) or null.",
  "diagnostics": {
    "stories_total": 82,
    "candidates_selected": 34,
    "tickers_with_candidates": 14,
    "tickers_no_candidates": 22,
    "tickers_capped": 3,
    "tickers_omitted_by_llm": 1,
    "tickers_dropped_guid_mismatch": 0,
    "scores_damped": 1
  },
  "signals": {
    "AAPL": {
      "sentiment": "bullish",
      "score": 0.62,
      "rationale": "Two tier-1 outlets report stronger-than-expected iPhone guidance.",
      "headline": "Apple raises guidance on iPhone demand",
      "source": "Reuters",
      "url": "https://finance.yahoo.com/news/...",
      "published": 1783330868.0,
      "guid": "fb8e3537-d0ab-3498-9618-5d263f80eb9c",
      "n_articles": 3
    }
  }
}
```

Field rules (normative):

- **`signals`** — keyed by uppercase ticker. A ticker is present **iff** it had ≥1 valid candidate AND the LLM returned a valid, gate-passing entry. Absent = no data. Never emit `n_articles: 0`.
- **`score`** — float in [-1, 1] (clamped). **Source of truth.** Damping: if `n_articles < SIGNALS_DAMP_MIN_ARTICLES`, |score| is capped at `SIGNALS_DAMP_CAP` (anti prompt-injection rail, §7.3).
- **`sentiment`** — **derived** deterministically: `score ≥ +0.15` → `bullish`; `score ≤ −0.15` → `bearish`; else `neutral`. Consumers wanting custom thresholds re-derive from `score`; the label is a convenience.
- **`headline` / `source` / `url` / `published` / `guid`** — copied from the representative story (the LLM-chosen guid, validated to be a member of *that ticker's* candidate set; §6.3). Never LLM-authored text. `guid` persists the exact join key for traceability.
- **`rationale`** — LLM text, plain text, ≤ 280 chars, control/bidi chars stripped. Artifact-only; never reaches the ATL 7-field contract.
- **`n_articles`** — count of that ticker's candidate stories in this batch (post-dedup, pre-cap).
- **`age_hours` is deliberately absent** — consumers compute it at read time against their own timestamp; staleness is never baked into the artifact.
- **`status`** — `"ok"` or `"degraded"`. On whole-call LLM failure the artifact is still written (preserving 1:1 mapping and the audit trail) with `signals: {}`, `status: "degraded"`, and a `status_reason`. There is no extractive fallback for sentiment — we never fabricate scores.
- **`source_items`** — basename only, never a path.
- **`profile`** — reserved for future per-user/per-config variants; always `"default"` in v1.

A machine-readable JSON Schema ships at `Heartbeat/schemas/signals-v1.schema.json`; both repos' test suites validate against it (the cross-repo contract test).

### 4.3 Internal LLM contract — the swap point

`compute_sentiment(candidates_by_ticker) -> (overview, {ticker: {score, guid, rationale}})`

- Request: one chat-completions call, `response_format: json_object`, temperature 0.2, max_tokens 2000, 120 s socket timeout, 1 retry. Skeptical-analyst system prompt; **feed text is data, never instructions**. Per candidate: `guid`, ticker, title, source, age-hours, description capped at `SIGNALS_DESC_CAP`.
- Response shape: `{"overview": str, "tickers": {"SYM": {"score": float, "guid": str, "rationale": str}}}`.
- This function is the frozen swap point for the group's future Sentiment Signals models. Swapping internals must not change §4.2. A local-inference swap (FinBERT-class) is an infra decision (D6).

### 4.4 HTTP endpoint contract (future session; pinned now)

`GET /api/signals/news/` (Django, public, read-only):

- **200** — public serialization of the newest `signals-*.json` (newest = greatest filename stem; date-stamped stems sort lexicographically): the artifact **minus** `generator`, `model`, `prompt_version` (recon-value stripping), **plus** `"staleness_hours"` computed server-side from `generated_at`.
- `?tickers=AAPL,MSFT` — optional filter on `signals` keys (case-insensitive; unknown tickers simply absent).
- **404** `{"error": "no_signals"}` — no artifact exists.
- Headers: `ETag` (from `generated_at`), `Last-Modified`, `Cache-Control: public, max-age=300`; conditional GET returns 304. Rate-limited via the existing `django_ratelimit` + `api.identity.ratelimit_key` infra.
- Serving path: container gets a **runtime-enforced `:ro` mount of `$HEARTBEAT_HOME/signals/` only** — never the whole digests tree (§7.2).

### 4.5 ATL adapter projection (future session; pinned now)

`dashboard/backend/integrations/news_sentiment.py :: get_news_sentiment(universe, timestamp) -> {"news_sentiment": {...}, "news_overview": str|None}`

- Mode by env: `NEWS_SENTIMENT_URL` (live HTTP, ~3 s timeout) **or** `NEWS_SENTIMENT_FIXTURE` (local artifact file). Neither set → `({}, None)`.
- For each artifact entry with ticker ∈ `universe`:
  - **No-lookahead:** drop if `published > t` (t = parsed request timestamp).
  - **Staleness:** drop if `(t − published) > 48 h` (tunable).
  - `age_hours = round((t − published) / 3600, 1)`.
  - Project to the frozen 7-field `NewsSentimentEntry`: `sentiment, score, headline, source, url, age_hours, n_articles`. (`rationale`, `guid`, `published` do not cross; Pydantic model stays untouched.)
- Any exception anywhere → `({}, None)` (preserves ATL's shipped fail-closed tests).

## 5. Configuration (the v1 tuning surface)

Env vars read by `news_signals.py` (module constants as defaults). This is the surface future user-facing tuning builds on:

| Var | Default | Meaning |
|-----|---------|---------|
| `SIGNALS_HOME` | `$HEARTBEAT_HOME` | Base dir (digests/ input, signals/ output) |
| `SIGNALS_MODEL` | `gpt-4o-mini` | Falls back to `HEARTBEAT_MODEL` |
| `SIGNALS_MIN_EDITORIAL_SCORE` | `2.0` | Candidate gate on items `score` |
| `SIGNALS_PER_TICKER_CAP` | `3` | Candidate stories per ticker sent to LLM |
| `SIGNALS_DESC_CAP` | `200` | Chars of description per candidate |
| `SIGNALS_THRESHOLD` | `0.15` | ± threshold for bullish/bearish label |
| `SIGNALS_DAMP_CAP` | `0.7` | Max \|score\| when under-corroborated |
| `SIGNALS_DAMP_MIN_ARTICLES` | `2` | Corroboration needed for \|score\| > damp cap |
| `SIGNALS_MAX_FILE_MB` | `10` | Reject oversized items files |
| `SIGNALS_STALENESS_ALERT_H` | `30` | Canary threshold (§6-C) |

## 6. Failure policy (every mode decided)

1. **Poison pill:** validation-gate failure → `ERROR` log with filename, batch recorded in `signals_state.json` as `processed-with-error`, **exit 0** (a deterministic local failure must not count as a unit failure or trip start-rate limits). Durable evidence: the state entry + journal line.
2. **Write ordering:** artifact written fully (temp + `os.replace`) **before** `signals_state.json` is updated. A crash in between causes one duplicate LLM call on the next sweep — cheap — never a silent gap. **Deliberately opposite** to the heartbeat's state-before-delivery order, which optimizes against duplicate Discord posts; our failure economics are inverted (duplicates free, gaps invisible).
3. **LLM failure (timeout / malformed JSON / retry exhausted):** degraded artifact (§4.2 `status`), batch marked processed. No fabricated scores, ever.
4. **Unit hardening (pinned unit-file lines):** `StartLimitIntervalSec=0`, `TimeoutStartSec=600` (headroom over 120 s × 2 LLM attempts), `MemoryHigh=96M`, `MemoryMax=128M`, `Nice=10`, `PrivateTmp=true`. `flock` on `$SIGNALS_HOME/signals/.lock` guards manual-vs-timer races.
5. **Disk full:** ENOSPC raises during temp write, before `os.replace` — run aborts, state untouched, retried next sweep. Implementation must not blanket-catch `OSError` around the write-replace step.
6. **Missed/coalesced triggers:** irrelevant by construction — every sweep rescans all unprocessed batches against state.

### 6-C. Staleness canary

A fully-wedged pipeline must be distinguishable from a quiet news day. A separate `finsearch-signals-canary.timer` (daily) checks that a `signals-*.json` newer than `SIGNALS_STALENESS_ALERT_H` exists; if not, logs `CRIT` and pings the existing Discord webhook. Independent of both producer and consumer failure paths. The future endpoint additionally exposes `staleness_hours` (§4.4) so consumers see stale data as stale rather than fresh-looking.

## 7. Security

1. **Validation gate (input trust boundary):** max file size before open; per-field length caps (title 500, description 5000, url 2000); `published` sane within `[file mtime − 30 d, file mtime + 1 h]` (blocks recency/staleness gaming via forged epochs); strip Unicode control/bidi characters from title/description (extends the heartbeat's masked-link precedent to this pipeline).
2. **Serving trust boundary:** signals live in their own directory; the future endpoint container mounts **only** that directory, runtime-enforced `:ro`. A Django-container compromise must not be able to forge or overwrite artifacts. Public serialization strips model/prompt metadata (§4.4).
3. **Prompt injection (residual, accepted for now):** feed text is attacker-influenceable; the guid-join kills fabricated provenance and the membership check kills cross-wiring, but a well-formed *steered* score for a real ticker survives prompt-level defenses. Structural rail: the corroboration damper (§4.2) — one crafted story cannot push a ticker past ±`SIGNALS_DAMP_CAP`. Residual risk acceptable while consumers are backtest/paper only against a locked universe; **revisit before any live-capital wiring**. Cross-ticker contamination within the single batched call is a known residual (full isolation = per-ticker calls; not worth the cost today).
4. **Key handling:** dedicated env file (`.env.heartbeat` posture), mode 600, never logged.
5. **Multi-producer future:** today the only writer of `digests/` is the heartbeat under the same user — one trust domain. Before onboarding producer #2: producer identity in the filename convention + allowlist in `news_signals.py` ("well-formed JSONL" must stop being the entire trust check).

## 8. Known seam debt (documented, deliberately deferred)

- **Producer #2 requires real design work, not config:** cross-batch/cross-source dedup has no owning stage (same story under two guid schemes inflates `n_articles`/corroboration); filename scheme has no producer tag (collision risk); the `score` calibration contract (§4.1) must be enforced. Do not bolt on a second producer without this.
- **Model swap = infra decision** (D6).
- **Per-user variants:** cost crosses from noise (~$0.001–0.004/run today) into real money around 10K–50K calls/day (users × batches). A hard call-volume budget gate must exist before any per-user feature ships.
- **`AMEX` typo in ATL's `DJIA_30`** — flag to ATL maintainers (likely `AMGN` intended).

## 9. Testing strategy

- **Unit (`Heartbeat/tests/test_news_signals.py`):** validation gate (size/field/published/bidi cases), candidate selection + caps, prompt assembly, response validation (guid membership, clamping, damping, label derivation), poison-pill state handling, artifact-before-state ordering (crash injection between writes), degraded-artifact path, atomic write.
- **Contract:** sample artifacts validate against `signals-v1.schema.json`; a fixture copy + the same schema go to the ATL repo next session (its adapter tests validate projection from the same fixture).
- **Staging (deploy session):** drop two items files back-to-back, confirm sweep processes both exactly once; kill the process mid-LLM-call, confirm no state corruption and reprocessing next sweep.

## 10. Architecture review verdict (2026-07-06 panel: failure/observability, security, cost/reality lenses)

- Q0 principle **PASS** · Q1 scalability **PASS** · Q2 customizability **PASS**
- Q3 failure: **BLOCKER as first drawn → resolved** by §6 (write ordering, poison pill, unit hardening, canary).
- Q4 observability: **BLOCKER as first drawn → resolved** by diagnostics block, persisted guid, canary, endpoint staleness.
- Q5 cost **PASS** (≈$0.03–0.10/month at current scope; cliff quantified in §8).
- Q6 security **CONCERN accepted** (§7.3 residual) + `:ro` mount pinned as hard requirement for the endpoint session.
- Q7 reality check **CONCERN accepted**: plug-and-unplug is real for consumers and the model swap's data contract; overstated for producer #2 and model-swap infra — both written down in §8 instead of hand-waved.

Planning call: every box is named, every seam has a contract, every failure has a decided behavior → walking skeleton is specable; build this session's slice.
