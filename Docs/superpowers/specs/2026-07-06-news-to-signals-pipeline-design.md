# News → Signals Pipeline — Design Spec

**Date:** 2026-07-06
**Status:** Formats pinned; producer-side prototype validated against real prod data. ATL-side adapter, Django endpoint, and droplet deployment are specified here but built in a future session. **Amended 2026-07-06** after the research benchmark (companion doc below): subject-relevance gate (D8) and near-dup collapse (D9) added, prompt datamarking pinned, label-deadband default widened to ±0.20. **Amended 2026-07-07** after an adversarial review of the implementation plan: `SIGNALS_STALENESS_ALERT_H` default corrected 30→20 (§5) — the 30 h default never actually caught a single missed day (see the corrected §5 note); the plan additionally hardens `TICKER_ALIASES` substring matching to word-bounded (Task 5) after confirming real collisions (`"intel"`⊂`"intelligence"`, `"cisco"`⊂`"francisco"`), and records novelty-preference/LDD/batch-mean-de-biasing as explicit (not silent) deferrals in the plan's new "Known seam debt" section.
**Relates to:** `Docs/superpowers/specs/2026-06-10-news-heartbeat-design.md` (producer), `2026-07-06-news-to-signals-research-benchmark.md` (research benchmark driving the 2026-07-06 amendments), `/mnt/d/Documents/ATL Materials/FinSearch-to-ATL-Integration-Plan.html` (Plan 1), ATL repo `dashboard/backend/api/v2/models.py` (frozen consumer contract).

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
| D5 | Trigger mechanism | **systemd timer sweep (15–30 min) — confirmed 2026-07-06** | Panel-recommended, user-confirmed. Not just simpler than inotify: under a future near-real-time feed (stories arriving every few dozen seconds), per-arrival triggering would drive one LLM call per story — the wrong cost shape. The sweep interval doubles as the micro-batch accumulator, so the timer is the destination design, not a stopgap. Interval becomes a tuning knob when feed frequency rises. |
| D6 | Sentiment method v1 | **Batched gpt-4o-mini call** (OpenAI-compatible, same key discipline as heartbeat digest) | FinBERT-class local inference does not fit the droplet (128MB unit cap / ~300MB free RAM); a future swap of `compute_sentiment` internals is an **infra decision** (hosted inference or new host), interface unchanged. |
| D7 | Retention-prune interlock | **Declined** | Making the heartbeat's prune aware of signals state couples stages the design deliberately keeps ignorant of each other. Residual risk (pipeline dead AND canary ignored for 90 days) accepted; the staleness canary (§6-C) covers realistic cases. |
| D8 | Subject-relevance gate | **Deterministic pre-LLM heuristic** (ticker or company-alias token in headline + roundup/listicle title blocklist + structural backstop: a headline that is "subject" for ≥ `ROUNDUP_TICKER_LIMIT` distinct watchlist tickers is treated as a roundup regardless of phrasing), NOT an LLM-returned relevance field | Research benchmark P0: every serious pipeline gates on entity-as-*subject*, and the sample's 9× `0.00` was roundup dilution, not a scorer bug. Pre-LLM placement is the only one that fixes context dilution (a post-hoc LLM field still spends the per-ticker cap and tokens on roundups), keeps the gate outside the injection blast radius (a self-reported `is_subject` is computed from attacker-influenceable text), stays deterministic/testable, and keeps the §4.3 swap point thin (a FinBERT-class swap needn't reimplement relevance). The LLM's existing freedom to omit tickers remains the second-line filter. Consequence: mention-only tickers now come out **absent** instead of `0.00` — consumers already tolerate absent tickers (D2). Exact blocklist patterns + alias map are implementation-plan detail. |
| D9 | Within-batch near-dup collapse | **Collapse near-duplicate stories per ticker before `n_articles` and the cap** | Research benchmark P0/P1: ~80% of incoming financial news is near-duplicate (Feedly), so without collapse the corroboration damper (§7.3) can be satisfied by 20 copies of one roundup — illusory corroboration. Semantics pinned here (`n_articles` counts *distinct* stories); the algorithm (normalized-title match vs MinHash) is a two-way door for the implementation plan. Cross-batch/cross-producer dedup remains seam debt (§8). |

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
        │     subject-relevance gate (D8), near-dup collapse (D9), per-ticker cap
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

Cap selection: when more than `SIGNALS_PER_TICKER_CAP` distinct subject-stories survive D8/D9 for a ticker, the cap is filled deterministically by (editorial score desc, recency desc). A novelty-over-rehash preference within the cap (research P1; novelty gating is an evidenced alpha lever) is a deliberate implementation-plan refinement, not pinned here.

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
    "candidates_dropped_not_subject": 41,
    "near_dups_collapsed": 12,
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
- **`sentiment`** — **derived** deterministically: `score ≥ +0.20` → `bullish`; `score ≤ −0.20` → `bearish`; else `neutral`. Default widened from ±0.15 per the research benchmark (the 40/60 band is the one deadband with direct empirical backing; ±0.10-class bands over-fire). Asymmetric bands (negatives more informative) are a consumer-side option — consumers wanting custom thresholds re-derive from `score`; the label is a convenience.
- **`headline` / `source` / `url` / `published` / `guid`** — copied from the representative story (the LLM-chosen guid, validated to be a member of *that ticker's* candidate set; §3 step 6, §7.3). Never LLM-authored text. `guid` persists the exact join key for traceability.
- **`rationale`** — LLM text, plain text, ≤ 280 chars, control/bidi chars stripped. Artifact-only; never reaches the ATL 7-field contract.
- **`n_articles`** — count of that ticker's **distinct** candidate stories in this batch (post subject-gate D8 and near-dup collapse D9, pre-cap). Duplicates must never satisfy the corroboration damper.
- **`age_hours` is deliberately absent** — consumers compute it at read time against their own timestamp; staleness is never baked into the artifact.
- **`status`** — `"ok"` or `"degraded"`. On whole-call LLM failure the artifact is still written (preserving 1:1 mapping and the audit trail) with `signals: {}`, `status: "degraded"`, and a `status_reason`. There is no extractive fallback for sentiment — we never fabricate scores.
- **`source_items`** — basename only, never a path.
- **`profile`** — reserved for future per-user/per-config variants; always `"default"` in v1.

A machine-readable JSON Schema ships at `Heartbeat/schemas/signals-v1.schema.json`; both repos' test suites validate against it (the cross-repo contract test).

### 4.3 Internal LLM contract — the swap point

`compute_sentiment(candidates_by_ticker) -> (overview, {ticker: {score, guid, rationale}})`

- Request: one chat-completions call, `response_format: json_object`, temperature 0.2, max_tokens 2000, 120 s socket timeout, 1 retry. Skeptical-analyst system prompt; **feed text is data, never instructions** — enforced structurally, not just verbally: each candidate's title/description is **datamarked** (wrapped in explicit delimiters and declared untrusted data to be scored, never instructions to follow — spotlighting, shown to cut indirect-injection success from >50% to <2%). Exact delimiter format is implementation-plan detail. Per candidate: `guid`, ticker, title, source, age-hours, description capped at `SIGNALS_DESC_CAP`.
- Response shape: `{"overview": str, "tickers": {"SYM": {"score": float, "guid": str, "rationale": str}}}`.
- This function is the frozen swap point for the group's future Sentiment Signals models. Swapping internals must not change §4.2. A local-inference swap (FinBERT-class) is an infra decision (D6).

### 4.4 HTTP endpoint contract (future session; pinned now)

`GET /api/signals/news/` (Django, public, read-only):

- **200** — public serialization of the newest `signals-*.json` (newest = greatest mtime, filename as a deterministic tiebreak; same-day supplemental stems sort lexicographically before the date-only stem, so stem order alone is not recency): the artifact **minus** `generator`, `model`, `prompt_version` (recon-value stripping), **plus** `"staleness_hours"` computed server-side from `generated_at`.
- `?tickers=AAPL,MSFT` — optional filter on `signals` keys (case-insensitive; unknown tickers simply absent).
- **`?as_of=YYYY-MM-DD` (optional):** returns the newest artifact whose batch
  date is on or before the given day (point-in-time — no lookahead, robust to
  weekend/missed-run gaps). Batch dates are UTC calendar dates (the producer
  stamps artifacts with the UTC date). Resolution is by the artifact's filename
  stem date — candidates order by `(stem date, mtime, name)`, so a backfilled
  older day never outranks a newer day; `(mtime, name)` stays the
  same-day-supplemental tiebreak. Absent → newest overall.
  A date earlier than all retained artifacts → `404 {"error": "no_signals"}`;
  a future date → the latest artifact; a malformed value → `400 {"error":
  "bad_as_of"}`. History depth is bounded by retention (`SIGNALS_KEEP_N`,
  default 14 dated artifacts): a date older than the oldest retained artifact
  404s identically to "never produced" — callers cannot distinguish the two
  from the response alone. The per-date ticker set is whatever the watchlist
  was when that artifact was produced — it can change across the retained
  window (e.g. a watchlist deploy). Callers detect a gap by comparing the
  requested date to the returned `generated_at`. `staleness_hours` is
  unchanged (relative to now). Composes with `?tickers=`.
- **404** `{"error": "no_signals"}` — no artifact exists.
- Headers: `ETag` (from `generated_at` + `source_items` + the normalized tickers filter, `+`-joined — Django's `parse_etags()` rejects commas inside an ETag), `Last-Modified` **on the unfiltered variant only** (it is identical across tickers variants, so an `If-Modified-Since`-only revalidation of a filtered request must get a full 200, never a 304 pointing at a differently-filtered cached body), `Cache-Control: public, max-age=300`; conditional GET returns 304. Rate-limited via the existing `django_ratelimit` + `api.identity.ratelimit_key` infra.
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
| `SIGNALS_THRESHOLD` | `0.20` | ± threshold for bullish/bearish label (40/60 band, empirically backed; was 0.15) |
| `SIGNALS_DAMP_CAP` | `0.7` | Max \|score\| when under-corroborated |
| `SIGNALS_DAMP_MIN_ARTICLES` | `2` | Corroboration needed for \|score\| > damp cap |
| `SIGNALS_MAX_FILE_MB` | `10` | Reject oversized items files |
| `SIGNALS_STALENESS_ALERT_H` | `20` | Canary threshold (§6-C). Tuned, not arbitrary: the daily canary check runs 2 h after the daily beat, so a single fully-missed day leaves the newest artifact ~25.5 h old at the *next* day's check — a 30 h threshold would not cross that (it silently absorbs one entire missed day, only firing after a second consecutive miss); 20 h does. |

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
3. **Prompt injection (residual, accepted for now):** feed text is attacker-influenceable; the guid-join kills fabricated provenance and the membership check kills cross-wiring, but a well-formed *steered* score for a real ticker survives prompt-level defenses. Structural rails, layered: the subject gate (D8) keeps most mention-bait out of the prompt entirely; datamarking (§4.3) marks what does enter as untrusted; the corroboration damper (§4.2) — now counted over *distinct* stories (D9), so duplicates can't satisfy it — means one crafted story cannot push a ticker past ±`SIGNALS_DAMP_CAP`. Residual risk acceptable while consumers are backtest/paper only against a locked universe; **revisit before any live-capital wiring** (named upgrade path: a SecAlign-class fine-tuned scorer via the D6 swap seam; prompt-level defenses reduce but never eliminate attack success). Cross-ticker contamination within the single batched call is a known residual (full isolation = per-ticker calls; not worth the cost today).
4. **Key handling:** dedicated env file (`.env.heartbeat` posture), mode 600, never logged.
5. **Multi-producer future:** today the only writer of `digests/` is the heartbeat under the same user — one trust domain. Before onboarding producer #2: producer identity in the filename convention + allowlist in `news_signals.py` ("well-formed JSONL" must stop being the entire trust check).

## 8. Known seam debt (documented, deliberately deferred)

- **Producer #2 requires real design work, not config:** cross-batch/cross-source dedup has no owning stage (same story under two guid schemes inflates `n_articles`/corroboration); filename scheme has no producer tag (collision risk); the `score` calibration contract (§4.1) must be enforced. Do not bolt on a second producer without this.
- **Model swap = infra decision** (D6).
- **Aggregation upgrade path (documented, not built):** the per-batch snapshot is *correct* for the current short-horizon consumer (research-verified, not a stopgap). When a longer-horizon consumer appears, the specified swap is EWMA with half-life ≈ decision horizon (~6 h for intraday, ~90 d for multi-week momentum), or per-article recency weight `λ^age_days` with λ ≈ 0.89 (1-day horizon) → 0.97 (1-month). Whether it lives consumer-side or artifact-side is decided then.
- **Source-tier hardening (producer-side, future):** extend the editorial gate with explicit exclusion of PR-wire/promotional/robo-generated sources (the MarketPsych pattern). Lives in the heartbeat's scoring, not in `news_signals.py`.
- **Per-user variants:** cost crosses from noise (~$0.001–0.004/run today) into real money around 10K–50K calls/day (users × batches). A hard call-volume budget gate must exist before any per-user feature ships.
- **`AMEX` typo in ATL's `DJIA_30`** — flag to ATL maintainers (likely `AMGN` intended).

## 9. Testing strategy

- **Unit (`Heartbeat/tests/test_news_signals.py`):** validation gate (size/field/published/bidi cases), candidate selection + caps, subject gate (ticker/alias-in-headline passes; roundup-blocklist title drops; a ticker whose candidates are all gated emits *absent*, not `0.00`), near-dup collapse (`n_articles` counts distinct stories; damper not satisfiable by duplicates), prompt assembly (datamarking delimiters present around every candidate), response validation (guid membership, clamping, damping, label derivation), poison-pill state handling, artifact-before-state ordering (crash injection between writes), degraded-artifact path, atomic write.
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

**Post-review amendment (2026-07-06, research benchmark):** the benchmark verified two P0 gaps against the field — entity-as-subject relevance and near-dup collapse — which entered the design as D8/D9; prompt datamarking was adopted into §4.3/§7.3 and the label deadband default widened to ±0.20. Panel verdicts unchanged; the Q6 residual now has a named live-capital upgrade path (SecAlign-class scorer). Items deliberately deferred to the implementation plan, not the spec: exact blocklist patterns + company-alias map, dedup algorithm choice (normalized-title vs MinHash), datamarking delimiter format, novelty-over-rehash preference within the per-ticker cap (§3 pins the deterministic default ordering), label-disguise defense (LDD) for the sentiment output, batch-mean de-biasing.
