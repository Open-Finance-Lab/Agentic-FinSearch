# Score field disambiguation — `editorial_score` / `sentiment_score`

**Date:** 2026-07-14
**Status:** Approved design (FlyM1ss), pre-implementation
**Amends:** 2026-07-06-news-to-signals-pipeline-design.md (§4.2 artifact schema, §4.4 endpoint contract)

## 1. Problem

Both news endpoints serve a field named `score` that mean unrelated things:

- `GET /api/news/items/` — the pipeline's **editorial** score (newsworthiness;
  gates sentiment candidacy at `SIGNALS_MIN_EDITORIAL_SCORE`, default 2.0).
- `GET /api/signals/news/` — the LLM-derived **sentiment** score in [-1, 1]
  (label threshold ±0.20).

Nothing in either schema distinguishes them. A consumer wiring up both gets
plausible numbers in the wrong semantic space — a silent failure. The ATL
adapter is exactly the consumer positioned to hit it (documented as a
`.. note::` in `Docs/source/api_reference.rst` when PR #359 shipped).

## 2. Decisions (settled with FlyM1ss, 2026-07-14)

1. **Names:** editorial `score` → `editorial_score`; sentiment `score` →
   `sentiment_score`.
2. **Depth: full.** Disk artifacts AND wire rename. Disk name == wire name
   again; `_ITEMS_WIRE_RENAMES` stays `{title→headline, link→url}`.
3. **No backwards compatibility.** No dual-write on disk, no dual-serve on
   the wire, no old-name acceptance in the validation gates. Everything
   strictly follows the renamed fields. The one-deploy-gap consequence
   (§6 runbook) is accepted: fail-closed 404 on items / stale-but-served
   signals for the window between Heartbeat deploy and the first new-format
   batch (minutes, given the manual run kick).
4. **ATL migration: consumer-first fallback.** ATL PR-1 lands
   `sig.get("sentiment_score", sig.get("score"))` *before* FinSearch deploys,
   making deploy order irrelevant; ATL PR-2 later deletes the fallback so the
   consumer, too, ends strict. This transitional shim is the only place any
   old-name tolerance exists, and it dies in PR-2.
5. **Two deliberate normalizations that are NOT old-name compat** (they exist
   to make the *new* name universal):
   - The signals view normalizes historical v1 artifacts at serve time
     (`score` → `sentiment_score`, body `schema_version: 2`) so `?as_of`
     queries against pre-rename artifacts still emit only the new name.
     Without it, point-in-time reads would resurrect the collision.
   - Served `schema_version` becomes 2 on both endpoints (wire contract is
     versioned and this is a breaking change to shipped v1 contracts).

## 3. Wire contracts (v2)

### 3.1 `GET /api/news/items/` — news-story v2

Identical to v1 except: per-item `score` → `editorial_score`; top-level
`schema_version: 2`. Per-item keys:
`guid, headline, url, source, published, description, tickers, editorial_score`.

### 3.2 `GET /api/signals/news/` — signals v2

Identical to signals-v1 except: per-ticker entry `score` → `sentiment_score`;
`schema_version: 2`. Entry keys:
`sentiment, sentiment_score, rationale, headline, source, url, published, guid, n_articles`.
`diagnostics.scores_damped` keeps its name (internal diagnostics; §7).

## 4. FinSearch changes (single PR)

### 4.1 `Heartbeat/news_heartbeat.py` (producer, deployed — VERSION bump)

- `s["editorial_score"] = score_story(...)`; the rank/dedup/digest sorts and
  any `get("score", 0.0)` defaults follow the new key.
- items-*.jsonl stories carry `editorial_score` only (decision 3: no
  dual-write).

### 4.2 `Heartbeat/news_signals.py` (reader + producer, deployed — VERSION bump)

- Reader: `REQUIRED_FIELDS` tuple, the float-parse line, the
  `min_editorial` candidacy gate, and the candidate sort read
  `editorial_score`. (`SIGNALS_MIN_EDITORIAL_SCORE` env name already
  correct — unchanged.)
- Writer: signal entries emit `sentiment_score`; `SCHEMA_VERSION = 2`.
- LLM I/O unchanged: the prompt still asks for `"score"` per ticker and the
  clamp still reads it from the model reply; the rename happens where the
  artifact entry is built. No `prompt_version` bump (§7).

### 4.3 `Heartbeat/schemas/`

- New `signals-v2.schema.json`: diffs from v1 are `schema_version const: 2`
  and `sentiment_score`. v1 schema stays in-tree — it describes historical
  artifacts that remain on disk and feed the view's normalizer.

### 4.4 `Main/backend/api/signals_views.py`

- Vendored items gate edited **byte-faithfully with 4.2's reader, same
  commit** (three-copy parity contract): `REQUIRED_FIELDS`, float-parse
  line, `_ITEMS_CONTRACT_FIELDS`. `_ITEMS_SCHEMA_VERSION = 2`.
- Signals view: serve-time normalizer for legacy artifacts — for each entry,
  if `score` present and `sentiment_score` absent, rename it; body always
  states `schema_version: 2`. Defensive (no KeyError on odd entries),
  deterministic per artifact (ETag semantics unaffected).

### 4.5 Parity guard (`Heartbeat/tests/test_port_parity.py`)

- Constant pins updated to the new tuples/lines.
- Corpus widened per the mutation-testing rule: lines with only legacy
  `score` (must now be *rejected* as missing `editorial_score` — the strict
  stance is itself behavior worth pinning), only `editorial_score`, and
  both keys (extra `score` key ignored, dropped at projection).

### 4.6 Tests

- `test_news_heartbeat.py`, `test_news_signals.py`, `test_news_items_endpoint.py`,
  `test_signals_endpoint.py`, `test_signals_contract.py`,
  `fixtures/make_signals_fixture.py`: field/fixture renames.
- New assertions: both wire bodies contain the new field and **not** `score`;
  both serve `schema_version == 2`; a v1 artifact served via `?as_of` comes
  out normalized (`sentiment_score`, version 2); an items batch containing
  only legacy `score` yields 404 (fail-closed, strict).
- Signals output validates against `signals-v2.schema.json`; the existing
  schema-parity test (code caps ↔ schema maxLength pins, news_signals.py:52)
  repoints from the v1 to the v2 schema file.
- The staleness canary is `news_signals.py --canary` (same single file) —
  covered by §4.2, no separate validator to update.

### 4.7 Docs (user-facing, readthedocs)

- `Docs/source/api_reference.rst`: both response shapes renamed; the
  score-collision `.. note::` (lines ~751–757) replaced by a short note per
  endpoint stating what its field measures and cross-linking the other —
  the collision it warned about no longer exists.
- Amendment pointer added to the 2026-07-06 news-to-signals spec.

## 5. ATL changes (coordinated, separate repo)

Context: ATL adapter #107 is live on main reading `sig["score"]`
(`dashboard/backend/integrations/news_sentiment.py:239` on post-#110
`origin/main` — local checkout stale; use `git grep origin/main`). It does
**not** pin FinSearch's `schema_version` in production code. The headline/url
items-feed fix, ATL PR #110, **merged 2026-07-14 15:36Z** (merge commit
`802c0852`) with post-review hardening beyond the head first verified:

- a shared `_story_fields()` projection (headline/source/url) used by both
  the backtest entry and the panel feed;
- a **wire-shape drift alarm** (`_alarm_if_all_dropped`): a projection
  yielding 0 usable entries from a non-empty batch logs ERROR and escalates
  the panel payload to `degraded` (rendered as a badge);
- a recorded items wire fixture (`tests/fixtures/items-wire-fixture.json`)
  plus key-set pins — `ITEMS_STORY_KEYS` (includes `score`) and
  `schema_version == 1` asserts in `test_news_sentiment_fixture.py` — built
  precisely so a producer rename must edit that file in review.

Re-verified against the merge commit: `_feed_from_items()` reads only
`headline/url/source/published/tickers`, never the items `score`, so the
items rename still breaks zero ATL *production* code, and neither rename
trips the drift alarm. But "docs-only" no longer holds: PR-2 must also flip
the items fixture and its pins (below). During the cutover's items-404
window the panel silently falls back to the Phase-A representative feed
(a 404 is "no items yet", not drift — no badge), bounded to minutes by the
§6 kick.

- **ATL PR-1 (lands + deploys BEFORE FinSearch deploys):**
  `sig.get("sentiment_score", sig.get("score"))` in `_project_entry`;
  fixtures cover both shapes; `docs/integrations/finsearch-news-sentiment.md`
  names `sentiment_score` canonical (contract table, example, projection row,
  reference sketch).
- **ATL PR-2 (after FinSearch v2 verified live):** delete the fallback —
  strict `sig["sentiment_score"]`; fixtures v2-only. Covers BOTH pipelines'
  fixtures: `signals-fixture.json` **and** `signals-wire-fixture.json` (a
  base↔wire parity test couples the pair), plus `items-wire-fixture.json`
  (`editorial_score`, `schema_version: 2`) with its `ITEMS_STORY_KEYS` /
  version pins flipped to v2.
- **Items contract docs (in PR-2, now that #110 merged with v1 vocabulary):**
  `docs/integrations/finsearch-news-items.md` and the news-story contract
  spec (`2026-07-14-finsearch-news-story-contract-design.md`) state `score`
  and `schema_version: 1` — update to `editorial_score` / `2`. The items doc
  also narrates a *hypothetical* "v2 that renamed a field" fallback scenario;
  rewrite it as actual history (the rename shipped as v2 and did NOT trip
  the drift alarm, because the projection never read `score`).
- Out of scope: ATL-internal names (`NewsSentimentEntry.score`, panel JS
  `s.score`) — ATL's own v2 API contract, not FinSearch's wire.
- Stale references to update opportunistically ATL-side:
  `finsearch-news-signals-panel-design.md:77,91`,
  `finsearch-news-signals-panel.md` (score mentions incl. step 8 field list).

## 6. Deploy runbook (order matters — cadence facts)

`finsearch-heartbeat.timer` is **daily 11:00 UTC**; `finsearch-signals.timer`
fires every 20 min. Without a kick, a mid-day Heartbeat deploy leaves the
newest batch old-format for up to ~24 h — during which signals runs
poison-pill (existing artifact keeps serving; `staleness_hours` grows) and a
deployed new Django 404s items.

1. ATL PR-1 merged + Render deploy verified (fallback live).
2. Merge FinSearch PR.
3. Deploy Heartbeat scripts to the droplet; **immediately
   `systemctl start finsearch-heartbeat.service`** and verify the new batch
   carries `editorial_score`.
4. Deploy Django. Verify: items 200 with `editorial_score`/v2; signals 200
   with `sentiment_score`/v2 (normalizer, since the newest artifact is
   still v1); `?as_of` on an old date also v2-normalized.
5. Within 20 min the next signals run writes the first native-v2 artifact;
   re-verify. ATL Home panel: real items rendering, no `degraded` drift
   badge. (During the pre-kick 404 window the panel shows the Phase-A
   representative feed with no badge — expected, not a failure.)
6. ATL PR-2 (drop fallback) whenever convenient after step 4 verification.

**Rollback:** roll Heartbeat and Django back **together**, then kick a manual
heartbeat run (a new-format newest batch fails the old gate exactly as an old
batch fails the new one — the strictness is symmetric). ATL's PR-1 fallback
keeps the panel alive in either direction until PR-2; if PR-2 already landed,
rolling back FinSearch requires reverting PR-2 too.

## 7. Non-goals

- LLM prompt/reply key stays `score` (ephemeral in-run data; renaming forces
  a `prompt_version` bump and model-behavior revalidation for no wire benefit).
- `diagnostics.scores_damped` unchanged.
- ATL-internal field names unchanged (§5).
- No renames in historical on-disk artifacts (no rewrite of old
  items-*.jsonl / signals-*.json; the view normalizer handles serving them).

## 8. Risks

- **Three-copy trust boundary churn** — mitigated by same-commit edits + the
  widened mutation-tested parity corpus (§4.5).
- **Deploy-gap outage** — accepted by decision 3; bounded to minutes by the
  manual run kick (§6).
- **Unknown consumers** — swept both repos 2026-07-14: none beyond the ATL
  adapter (signals) and ATL's broken-anyway items feed. Bearer gating means
  no anonymous third-party consumers can exist.
