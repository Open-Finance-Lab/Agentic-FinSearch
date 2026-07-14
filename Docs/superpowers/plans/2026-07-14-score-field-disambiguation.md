# Score Field Disambiguation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the two unrelated `score` fields — items pipeline → `editorial_score`, signals pipeline → `sentiment_score` — on disk and wire, bumping both endpoints to `schema_version: 2`, with zero backwards compatibility on the FinSearch side and a consumer-first fallback on the ATL side.

**Architecture:** Full-depth rename per `Docs/superpowers/specs/2026-07-14-score-field-disambiguation-design.md`. Tasks are ordered so every commit leaves both suites green: ATL fallback first (deploy-order freedom), then FinSearch by *pipeline* (items producer → items reader/server across the three-copy trust boundary → signals writer+schema → signals wire normalizer → docs), then the deploy runbook, then ATL strict cleanup.

**Tech Stack:** Python stdlib-only Heartbeat scripts (unittest), Django + pytest backend (uv), JSON Schema draft-07, GitHub Actions CI deploys (curl+sha256 atomic file replace for Heartbeat; podman image for Django), ATL = FastAPI/pydantic + pytest.

## Global Constraints

- FinSearch repo: `/mnt/d/fingpt/github/fingpt_rcos`, branch `feat/score-field-rename` (exists; carries the spec). ATL repo: `/mnt/d/Github/agent-trading-lab` — its working tree is STALE; branch from `origin/main` after `git fetch origin`, and read main-state via `git grep origin/main` / `git show origin/main:<path>`.
- `news_heartbeat.py` and `news_signals.py` are stdlib-only single-file deploys. Any edit bumps that file's `VERSION`: news_heartbeat `"2026-07-11.1"` → `"2026-07-14.1"`; news_signals `"2026-07-14.1"` → `"2026-07-14.2"`.
- Three-copy trust boundary: `news_signals.py` `validation_gate`/`clean_text` ↔ `Main/backend/api/signals_views.py` `_validate_items`/`_clean_text` may only be edited **byte-faithfully in the same commit** (modulo the pinned rename map in `Heartbeat/tests/test_port_parity.py:33-42`).
- Strictly no old-name tolerance in FinSearch: after this PR, `score` appears nowhere on either wire, and gates treat a legacy `score`-only story as *missing required field* (batch-level poison pill → 404). The only old-name tolerance anywhere is ATL PR-1's transitional fallback, deleted in PR-2.
- LLM prompt/reply key stays `score` (`news_signals.py:399-407`, `:477`); `PROMPT_VERSION` unchanged. `diagnostics.scores_damped` unchanged. ATL-internal names (`NewsSentimentEntry.score`, panel JS `s.score`) unchanged.
- Test commands — Heartbeat: `cd /mnt/d/fingpt/github/fingpt_rcos/Heartbeat && python3 -m unittest discover -s tests -v`. Backend: `cd /mnt/d/fingpt/github/fingpt_rcos/Main/backend && uv run pytest tests -q`. ATL: `cd /mnt/d/Github/agent-trading-lab && pytest dashboard/backend/tests/ --timeout=180 -p no:cacheprovider`.
- PR titles short, `type(scope): summary` convention. Commits end with the Claude Co-Authored-By trailer.
- Merge gate ordering: ATL PR-1 must be **merged and deployed** before the FinSearch PR merges (Task 8 checkpoint). FinSearch merge auto-deploys Heartbeat + Django concurrently via CI.

---

### Task 1: ATL PR-1 — adapter fallback (consumer-first)

**Files:**
- Modify: `/mnt/d/Github/agent-trading-lab/dashboard/backend/integrations/news_sentiment.py` (`_project_entry`, lines 236-244 on post-#110 origin/main; the `"score": sig["score"],` read is line 239, directly above `**_story_fields(sig)`)
- Modify: `/mnt/d/Github/agent-trading-lab/dashboard/backend/tests/test_news_sentiment_fixture.py` (the SIGNALS pin in `test_fixture_matches_contract_essentials`, line ~105)
- Create: `/mnt/d/Github/agent-trading-lab/dashboard/backend/tests/test_sentiment_score_fallback.py`

**Interfaces:**
- Consumes: FinSearch signals wire, today `{"score": float}` per entry (v1), soon `{"sentiment_score": float}` (v2).
- Produces: unchanged `NewsSentimentEntry`-shaped dict with internal key `"score"` — no downstream ATL change.

- [ ] **Step 1: Branch off fresh origin/main**

```bash
cd /mnt/d/Github/agent-trading-lab
git fetch origin
git checkout -b feat/sentiment-score-fallback origin/main
```

PR #110 MERGED 2026-07-14 15:36Z (merge commit `802c0852`) — branching off fresh origin/main picks up its items feed, the `_alarm_if_all_dropped` drift alarm, and the items fixture pins. This branch touches none of those.

- [ ] **Step 2: Write the failing test**

Create `dashboard/backend/tests/test_sentiment_score_fallback.py`. Copy the exact import prefix used for `news_sentiment` from the top of `test_news_sentiment_adapter.py` (`from dashboard.backend.integrations import news_sentiment as ns` — the fixture test file no longer imports the adapter); shown here as `dashboard.backend.…`, adjust only if the adapter test differs:

```python
"""PR-1 of the FinSearch score-field disambiguation (see FinSearch spec
2026-07-14-score-field-disambiguation-design.md): _project_entry must read
sentiment_score (signals v2) and fall back to score (v1) until PR-2 deletes
the fallback."""
from dashboard.backend.integrations.news_sentiment import _project_entry

BASE = {"sentiment": "bullish", "rationale": "r", "headline": "h",
        "source": "Reuters", "url": "https://example.com/a",
        "published": 1783330000.0, "guid": "g1", "n_articles": 2}


def test_project_entry_reads_sentiment_score_v2():
    entry = _project_entry({**BASE, "sentiment_score": 0.5},
                           reference_ts=1783333600.0)
    assert entry["score"] == 0.5


def test_project_entry_falls_back_to_v1_score():
    entry = _project_entry({**BASE, "score": -0.3},
                           reference_ts=1783333600.0)
    assert entry["score"] == -0.3


def test_project_entry_prefers_sentiment_score_when_both_present():
    entry = _project_entry({**BASE, "sentiment_score": 0.5, "score": -0.9},
                           reference_ts=1783333600.0)
    assert entry["score"] == 0.5
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest dashboard/backend/tests/test_sentiment_score_fallback.py -v --timeout=180 -p no:cacheprovider`
Expected: first and third tests FAIL with `KeyError: 'score'` / wrong value; second PASSES (current behavior).

- [ ] **Step 4: Implement the fallback**

In `news_sentiment.py` `_project_entry`, change the single line

```python
        "score": sig["score"],
```

to

```python
        # v2 sentiment_score with transitional v1 fallback — PR-2 of the
        # FinSearch score-field disambiguation deletes the fallback.
        "score": sig.get("sentiment_score", sig.get("score")),
```

- [ ] **Step 5: Relax the SIGNALS fixture schema_version pin (not the items one)**

`test_news_sentiment_fixture.py` now has TWO `schema_version == 1` asserts. In `test_fixture_matches_contract_essentials` (the signals fixture pin, line ~105), change

```python
    assert body["schema_version"] == 1
```

to

```python
    assert body["schema_version"] in (1, 2)  # transitional; PR-2 pins == 2
```

Leave `test_items_wire_fixture_matches_contract_essentials` (items pin, line ~55) at `== 1` — the items fixture stays v1 until PR-2 flips it after the FinSearch deploy. (Both asserts test static fixture files, so this relax is documentation of intent, not a green/red requirement.)

- [ ] **Step 6: Run the ATL suite**

Run: `pytest dashboard/backend/tests/ --timeout=180 -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard/backend/integrations/news_sentiment.py \
        dashboard/backend/tests/test_sentiment_score_fallback.py \
        dashboard/backend/tests/test_news_sentiment_fixture.py
git commit -m "feat(news): read sentiment_score with v1 fallback"
```

---

### Task 2: ATL PR-1 — sentiment contract doc, open PR

**Files:**
- Modify: `/mnt/d/Github/agent-trading-lab/docs/integrations/finsearch-news-sentiment.md` (lines 32, 85, 107, 194 on post-#110 origin/main)

**Interfaces:**
- Produces: contract doc naming `sentiment_score` canonical; PR-1 ready for user merge + deploy.

- [ ] **Step 1: Update the four `score` references**

Exact edits (line numbers per post-#110 origin/main):
1. Line 32 contract-table row: `| \`score\` | float | −1.0 … 1.0 |` → `| \`sentiment_score\` | float | −1.0 … 1.0 |`
2. Line 85 response example: `"score": 0.5,` → `"sentiment_score": 0.5,`
3. Line 107 projection-mapping row: `| \`score\` | \`score\` | passthrough (already −1…1) |` → `| \`score\` | \`sentiment_score\` | passthrough (already −1…1) |` (left column is the ATL-internal `NewsSentimentEntry` field, which keeps its name)
4. Line 194 reference sketch: `"score": s["score"],` → `"score": s["sentiment_score"],`

Leave line 76's top-level field list saying `schema_version` (=1) — factually correct until the FinSearch deploy; PR-2 flips it to (=2).

Then add, directly under the contract table, one transitional note:

```markdown
> **Transitional:** until FinSearch's schema-v2 deploy lands, the producer
> still sends `score` (v1); the adapter reads `sentiment_score` with a `score`
> fallback. PR-2 removes the fallback and pins `schema_version == 2`.
```

- [ ] **Step 2: Re-run the ATL suite (docs-only change — sanity)**

Run: `pytest dashboard/backend/tests/ --timeout=180 -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 3: Commit and open PR-1**

```bash
git add docs/integrations/finsearch-news-sentiment.md
git commit -m "docs(news): sentiment_score is the canonical wire name"
git push -u origin feat/sentiment-score-fallback
gh pr create --title "feat(news): read sentiment_score with v1 fallback" \
  --body "Consumer-first half of FinSearch's score-field disambiguation (editorial_score / sentiment_score, schema v2). Adapter prefers sentiment_score, falls back to score; fixture pin relaxed to {1,2}. PR-2 (after the FinSearch v2 deploy) deletes the fallback. Contract doc updated."
```

**CHECKPOINT (user-owned):** PR-1 must be merged and its deploy live before Task 8 (FinSearch merge). Do not self-merge.

---

### Task 3: FinSearch — items producer rename (`news_heartbeat.py`)

**Files:**
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/news_heartbeat.py` (lines 29, 255, 319-320, 343)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/tests/test_news_heartbeat.py` (all `score=` fixture kwargs and score assertions)

**Interfaces:**
- Produces: `items-*.jsonl` stories carrying `editorial_score` (float) and **no** `score` key. `score_story()` keeps its name and internal `score` local variable — it computes *the* editorial score; only the persisted dict key changes.

- [ ] **Step 1: Update the tests (failing first)**

In `test_news_heartbeat.py`, change every fixture `score=` kwarg to `editorial_score=` and the ranking assertion. Exact occurrences (current lines): `bulk_digest` line 50 (`score=5.0` → `editorial_score=5.0`); `test_collapses_near_identical_titles` lines 159, 161, 162 (three `score=` → `editorial_score=`); `test_listicle_penalized_below_market_news` line 193:

```python
        self.assertGreater(ranked[0]["editorial_score"],
                           ranked[1]["editorial_score"] + 2)
```

`test_digest_structure_without_llm` lines 210, 215; `test_summary_is_quoted_first_sentence_of_description` line 228; `_digest_and_index` line 361; `test_masked_link_injection_is_neutralized` line 407 — all `score=` → `editorial_score=`. Then sweep for stragglers:

```bash
grep -n '\bscore\b' Heartbeat/tests/test_news_heartbeat.py
```

Expected leftovers: none (the `story()` builder takes `**kw`, so the kwarg change is sufficient — no builder edit needed).

- [ ] **Step 2: Run to verify failure**

Run: `cd Heartbeat && python3 -m unittest tests.test_news_heartbeat -v`
Expected: FAIL — `rank_stories` still writes `s["score"]`, so `KeyError: 'editorial_score'` / ranking assertions fail.

- [ ] **Step 3: Implement the producer rename**

In `news_heartbeat.py`:

Line 29: `VERSION = "2026-07-11.1"` → `VERSION = "2026-07-14.1"`

Line 255 (`collapse_near_dups`):
```python
    for s in sorted(stories, key=lambda x: x.get("editorial_score", 0.0), reverse=True):
```

Lines 319-320 (`rank_stories`):
```python
        s["editorial_score"] = score_story(s, watchlist, now)
    return sorted(stories, key=lambda s: (s["editorial_score"], s["published"]),
                  reverse=True)
```

Line 343 (`extractive_digest`):
```python
    ranked = sorted(stories, key=lambda s: s.get("editorial_score", 0.0), reverse=True)
```

- [ ] **Step 4: Run the heartbeat producer tests**

Run: `cd Heartbeat && python3 -m unittest tests.test_news_heartbeat -v`
Expected: PASS. (Do NOT run the full Heartbeat suite yet — `test_news_signals.py`/parity still exercise the old reader and stay green because `news_signals.py` is untouched; the full suite runs in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add Heartbeat/news_heartbeat.py Heartbeat/tests/test_news_heartbeat.py
git commit -m "feat(heartbeat): items carry editorial_score, not score"
```

---

### Task 4: FinSearch — items reader/server rename across the three-copy boundary

This is the trust-boundary task: `news_signals.py` reader, `signals_views.py` vendored gate + projection, the parity corpus, and every test fixture that writes an items story — **one commit**, both suites green.

**Files:**
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/news_signals.py` (lines 27, 45, 188, 345, 368)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Main/backend/api/signals_views.py` (lines 201, 252, 284-287)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/tests/test_port_parity.py` (corpus + new strictness cases)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/tests/test_news_signals.py` (`make_story` fixture ~line 191 + `score=` kwargs)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/tests/fixtures/make_signals_fixture.py` (ITEMS lines 22-42)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Main/backend/tests/test_news_items_endpoint.py` (`CONTRACT_KEYS` line 24, `make_item` lines 27-39, shape assertions lines 74-100)

**Interfaces:**
- Consumes: Task 3's `editorial_score` disk key.
- Produces: both gate copies require `editorial_score` in `REQUIRED_FIELDS` and float-coerce `story["editorial_score"]`; wire item key `editorial_score`; `_ITEMS_SCHEMA_VERSION = 2`. Legacy `score`-only stories poison the batch (missing required field → ValueError → 404).

- [ ] **Step 1: Update endpoint tests (failing first)**

`test_news_items_endpoint.py` line 24:
```python
CONTRACT_KEYS = {"guid", "headline", "url", "source", "published",
                 "description", "tickers", "editorial_score"}
```

`make_item` (lines 27-39): rename the parameter and dict key:
```python
def make_item(guid, title="Example headline", link="https://example.com/a",
              source="Reuters", published=None, description="A description.",
              tickers=None, editorial_score=0.7, **extra):
    if published is None:
        published = time.time() - 3600  # 1h ago by default
    item = {
        "guid": guid, "title": title, "link": link, "source": source,
        "published": published, "description": description,
        "tickers": tickers if tickers is not None else ["AAPL"],
        "editorial_score": editorial_score,
    }
    item.update(extra)
    return item
```

In `test_serves_newest_batch_default_limit_shape` (lines 74-100): `body["schema_version"] == 1` → `== 2`; the float check becomes `body["items"][0]["editorial_score"]`; add one strictness assertion in the same test:
```python
        self.assertNotIn("score", body["items"][0])
```
Then `grep -n '\bscore\b' Main/backend/tests/test_news_items_endpoint.py` and convert any remaining `score=` kwargs / `["score"]` lookups the grep surfaces (e.g. the malformed-numeric test passes `score="NaN-ish"` style values — same rename, keep the malformed values).

Add one new test pinning the poison-pill on legacy batches (alongside `test_missing_required_field_poisons_batch`, lines 214-223, reusing its batch-writing pattern):
```python
    def test_legacy_score_only_batch_poisons(self):
        # Pre-rename batch: has score, lacks editorial_score -> missing
        # required field -> whole batch rejected (strict cutover, no compat).
        item = make_item("g1")
        item["score"] = item.pop("editorial_score")
        self._write_batch("items-2026-07-06.jsonl", [item])
        resp = self.client.get(PATH, **AUTH)
        self.assertEqual(resp.status_code, 404)
```
(Use the same batch-write helper + auth constants the sibling tests at lines 214-232 use — copy their exact call pattern.)

- [ ] **Step 2: Update Heartbeat reader tests (failing first)**

`test_news_signals.py` `make_story` (~line 191): `"score": 5.0` → `"editorial_score": 5.0` in the base dict. Then `grep -n 'score=' Heartbeat/tests/test_news_signals.py` and rename every `score=` kwarg to `editorial_score=` (known: `test_score_null_drops_story_not_batch` line 262, `test_editorial_gate_and_cap_order` lines 427-428, `test_near_dup_collapse_and_damping_compose_end_to_end` lines 649-653). Rename the test method `test_score_null_drops_story_not_batch` → `test_editorial_score_null_drops_story_not_batch`. Do NOT touch writer-side assertions (`e["score"]`, LLM reply dicts `{"score": 0.95, ...}`) — those are Task 5.

`make_signals_fixture.py` ITEMS (lines 22-42): each `"score": <val>` → `"editorial_score": <val>` (6 entries). Leave `fake_llm`'s `{"score": 0.5/0.9}` untouched (LLM reply key is out of scope). Do not regenerate the fixture JSON yet — that's Task 5.

- [ ] **Step 3: Update the parity corpus + add strictness cases**

In `test_port_parity.py` `TestValidateItemsParity` (lines 256-358): rename every `"score"` key in the corpus stories to `"editorial_score"` (keep the malformed-numeric values as-is — the case the corpus exists for). Add two cases using the class's existing batch-write helper and `_assert_gate_parity` pattern (copy the exact helper invocation from the neighboring tests):

```python
    def test_legacy_score_only_story_poisons_batch_in_both_copies(self):
        # Strict rename: a pre-rename story (score, no editorial_score) is a
        # MISSING REQUIRED FIELD -> batch-level ValueError in BOTH copies.
        story = {"guid": "g1", "title": "t", "link": "https://e/x",
                 "source": "s", "published": time.time(), "score": 5.0}
        path = self._write(json.dumps(story))
        with self.assertRaises(ValueError):
            _PORTED_NS["_validate_items"](path, 10)
        with self.assertRaises(ValueError):
            ns.validation_gate(path, 10)

    def test_extra_legacy_score_key_passes_gate_identically(self):
        # A story carrying BOTH keys validates; the stray legacy key is the
        # projection layer's problem (dropped at the wire), not the gate's.
        story = {"guid": "g1", "title": "t", "link": "https://e/x",
                 "source": "s", "published": time.time(),
                 "editorial_score": 5.0, "score": 4.0}
        path = self._write(json.dumps(story))
        self._assert_gate_parity(path, 10)
```
(`self._write` here stands for the class's actual single-batch write helper — match the existing methods' name and signature exactly; do not invent a second helper.)

- [ ] **Step 4: Run both suites to verify failure**

Run: `cd Heartbeat && python3 -m unittest discover -s tests -v` and `cd Main/backend && uv run pytest tests/test_news_items_endpoint.py -q`
Expected: FAIL — gates still require/parse `score`.

- [ ] **Step 5: Implement — both gate copies, same commit**

`Heartbeat/news_signals.py`:
- Line 27: `VERSION = "2026-07-14.1"` → `VERSION = "2026-07-14.2"` (covers Tasks 4+5; single bump for the PR)
- Line 45: `REQUIRED_FIELDS = ("guid", "title", "link", "source", "published", "editorial_score")`
- Line 188 (in `validation_gate`): `story["editorial_score"] = float(story["editorial_score"])`
- Line 345: `if float(story["editorial_score"]) < cfg["min_editorial"]:`
- Line 368: `lst.sort(key=lambda s: (-float(s["editorial_score"]), -float(s["published"])))`

`Main/backend/api/signals_views.py`:
- Line 201: `REQUIRED_FIELDS = ("guid", "title", "link", "source", "published", "editorial_score")`
- Line 252: `story["editorial_score"] = float(story["editorial_score"])`
- Lines 284-287:
```python
_ITEMS_CONTRACT_FIELDS = ("guid", "title", "link", "source", "published",
                          "description", "tickers", "editorial_score")
_ITEMS_WIRE_RENAMES = {"title": "headline", "link": "url"}
_ITEMS_SCHEMA_VERSION = 2  # news-story v2: score -> editorial_score (2026-07-14 spec)
```

- [ ] **Step 6: Run both suites to verify green**

Run: `cd Heartbeat && python3 -m unittest discover -s tests -v`
Expected: PASS, including all of `test_port_parity`.
Run: `cd Main/backend && uv run pytest tests -q`
Expected: PASS (signals endpoint tests untouched and still green — the signals artifact shape is unchanged so far).

- [ ] **Step 7: Mutation-test the new parity cases** (deploy-contract rule: a parity test must be shown to catch the drift it claims to catch)

Temporarily revert ONLY `signals_views.py` line 201 back to `"score"`, rerun `python3 -m unittest tests.test_port_parity -v` — expected: FAIL (constant parity + the new legacy case diverge). Restore the edit, rerun — PASS. Do the same one-sided flip for `news_signals.py` line 45 — expected FAIL, then restore, PASS.

- [ ] **Step 8: Commit (single commit — the boundary edit must not split)**

```bash
git add Heartbeat/news_signals.py Main/backend/api/signals_views.py \
        Heartbeat/tests/test_port_parity.py Heartbeat/tests/test_news_signals.py \
        Heartbeat/tests/fixtures/make_signals_fixture.py \
        Main/backend/tests/test_news_items_endpoint.py
git commit -m "feat(api): items pipeline requires and serves editorial_score (news-story v2)"
```

---

### Task 5: FinSearch — signals writer emits `sentiment_score` (signals v2)

**Files:**
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/news_signals.py` (lines 28, 52-53, 487)
- Create: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/schemas/signals-v2.schema.json`
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/tests/test_news_signals.py` (SIGNAL_KEYS lines 17-20, SCHEMA_PATH line 12, schema tests lines 28-38, writer assertions)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Heartbeat/tests/fixtures/make_signals_fixture.py` (regenerate output)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Main/backend/tests/test_signals_contract.py` (schema path + projection tuple)

**Interfaces:**
- Consumes: nothing new from Task 4 (writer side is independent of the reader key).
- Produces: artifact entries `{"sentiment", "sentiment_score", "rationale", "headline", "source", "url", "published", "guid", "n_articles"}`, `schema_version: 2`, pinned by `signals-v2.schema.json`. The backend fixture JSON regenerated in v2 shape.

- [ ] **Step 1: Update Heartbeat writer tests (failing first)**

`test_news_signals.py`:
- Lines 17-20: `SIGNAL_KEYS = ["sentiment", "sentiment_score", "rationale", "headline", "source", "url", "published", "guid", "n_articles"]`
- Line 12: `SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "signals-v2.schema.json"`
- Line 30 (in `test_schema_parses_and_pins_the_contract`): `self.assertEqual(schema["properties"]["schema_version"]["const"], 2)`
- Writer assertions: `test_clamp_damp_and_join` line 585 → `self.assertEqual(e["sentiment_score"], 0.7)`; `test_near_dup_collapse_and_damping_compose_end_to_end` line 662 → `self.assertEqual(entry["sentiment_score"], 0.7, ...)`. Sweep the rest: `grep -n '\["score"\]' Heartbeat/tests/test_news_signals.py` — convert every ARTIFACT-entry lookup to `["sentiment_score"]`; leave LLM-reply dict literals (`{"score": 0.95, ...}` passed INTO `validate_response`/`fake_llm_factory`) as `"score"` — that's the unchanged LLM contract.
- `test_ok_artifact_shape_and_diagnostics` (line 610 area): also assert `artifact["schema_version"] == 2` and `self.assertNotIn("score", entry)` for one entry.

- [ ] **Step 2: Run to verify failure**

Run: `cd Heartbeat && python3 -m unittest tests.test_news_signals -v`
Expected: FAIL — writer still emits `score`, schema v2 file missing.

- [ ] **Step 3: Implement writer + schema**

`news_signals.py` line 28: `SCHEMA_VERSION = 2`. Lines 52-53 comment: `signals-v1` → `signals-v2`. Line 487 (entry build):
```python
            "sentiment_score": round(score, 2),
```
(the local `score` variable and the `entry.get("score")` LLM-reply parse at line 477 stay).

Create `Heartbeat/schemas/signals-v2.schema.json` as an exact copy of `signals-v1.schema.json` with exactly three diffs:
- `"$id": "signals-v2.schema.json"`
- `"title": "FinSearch news signals artifact v2 (2026-07-14 score-field disambiguation)"`
- `"schema_version": { "const": 2 }`
- in `signals.additionalProperties`: `required` lists `"sentiment_score"` instead of `"score"`, and the property renames to `"sentiment_score": { "type": "number", "minimum": -1, "maximum": 1 }`

Keep `signals-v1.schema.json` in-tree (it describes historical artifacts).

- [ ] **Step 4: Regenerate the backend fixture + update contract test**

Run `make_signals_fixture.py` the way its header docstring says (it writes the fixture JSON consumed by `Main/backend/tests/test_signals_contract.py`). Then in `test_signals_contract.py`: point `SCHEMA` at `signals-v2.schema.json`; in `test_fixture_supports_atl_projection` (lines 22-33) change the tuple's `"score"` → `"sentiment_score"` and the range assert to `-1.0 <= entry["sentiment_score"] <= 1.0`, and add `assert "score" not in entry`.

- [ ] **Step 5: Run both suites**

Run: `cd Heartbeat && python3 -m unittest discover -s tests -v` — Expected: PASS.
Run: `cd Main/backend && uv run pytest tests/test_signals_contract.py -q` — Expected: PASS. (`test_signals_endpoint.py` still green: it builds its own v1-style artifacts and the view is still pass-through — Task 6 flips both together.)

- [ ] **Step 6: Commit**

```bash
git add Heartbeat/news_signals.py Heartbeat/schemas/signals-v2.schema.json \
        Heartbeat/tests/test_news_signals.py \
        Heartbeat/tests/fixtures/ Main/backend/tests/test_signals_contract.py
git commit -m "feat(signals): artifact v2 — sentiment_score replaces score"
```

---

### Task 6: FinSearch — signals wire is uniformly v2 (legacy normalizer in the view)

**Files:**
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Main/backend/api/signals_views.py` (view body, lines 162-181)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Main/backend/tests/test_signals_endpoint.py` (DEFAULT_SIGNAL lines 23-26, make_artifact lines 29-47, + new tests)

**Interfaces:**
- Consumes: v2 artifacts (Task 5) AND historical v1 artifacts on disk.
- Produces: every response — including `?as_of` reads of pre-rename artifacts — serves `schema_version: 2` and `sentiment_score`; `score` never reaches the wire.

- [ ] **Step 1: Update endpoint tests (failing first)**

`test_signals_endpoint.py`:
```python
DEFAULT_SIGNAL = {"sentiment": "bullish", "sentiment_score": 0.5, "rationale": "r",
                  "headline": "h", "source": "Reuters",
                  "url": "https://example.com/a", "published": 1783330000.0,
                  "guid": "g1", "n_articles": 2}
```
In `make_artifact`: `"schema_version": 2,`. In the main shape test add:
```python
        self.assertEqual(body["schema_version"], 2)
        self.assertNotIn("score", body["signals"]["MSFT"])
```
Add the normalizer test (same file, reusing the suite's artifact-writing pattern):
```python
    def test_legacy_v1_artifact_normalized_to_wire_v2(self):
        # Historical artifacts (?as_of reads) predate the rename; the wire
        # must still be uniformly v2 — score never escapes the boundary.
        legacy_entry = dict(DEFAULT_SIGNAL)
        legacy_entry["score"] = legacy_entry.pop("sentiment_score")
        art = make_artifact(generated_at="2026-07-10T00:00:00+00:00",
                            signals={"MSFT": legacy_entry})
        art["schema_version"] = 1
        self._write_artifact("signals-2026-07-10.json", art)
        resp = self.client.get(PATH, **AUTH)
        body = resp.json()
        self.assertEqual(body["schema_version"], 2)
        entry = body["signals"]["MSFT"]
        self.assertEqual(entry["sentiment_score"], 0.5)
        self.assertNotIn("score", entry)
```
(Match the suite's actual artifact-write helper and auth/PATH constants — copy the invocation from `test_serves_newest_by_stem_strips_private_adds_staleness`.)

- [ ] **Step 2: Run to verify failure**

Run: `cd Main/backend && uv run pytest tests/test_signals_endpoint.py -q`
Expected: FAIL — view is pass-through, no normalization.

- [ ] **Step 3: Implement the normalizer**

In `signals_views.py`, add beside `_PUBLIC_STRIP` (line 34):
```python
_SIGNALS_WIRE_SCHEMA_VERSION = 2  # wire is always v2; v1 disk artifacts are normalized below
```
In the `news_signals` view body, after `body = {k: v for k, v in artifact.items() if k not in _PUBLIC_STRIP}` (line 170), insert:
```python
    if body.get("schema_version") != _SIGNALS_WIRE_SCHEMA_VERSION:
        # Historical v1 artifacts (?as_of reads) predate the score-field
        # rename; normalize at the boundary so `score` never reaches the
        # wire. Defensive per-entry: odd shapes pass through untouched.
        body["schema_version"] = _SIGNALS_WIRE_SCHEMA_VERSION
        body["signals"] = {
            t: ({**e, "sentiment_score": {**e}.pop("score")}
                if isinstance(e, dict) and "score" in e
                and "sentiment_score" not in e else e)
            for t, e in body["signals"].items()
        }
```
Then simplify that dict-surgery into a small helper if the inline form reads poorly — behavior pinned by the test either way. The `{**e}` copy matters: `_get_artifact` memoizes per-request, but never mutate the loaded artifact in place.

- [ ] **Step 4: Run the backend suite**

Run: `cd Main/backend && uv run pytest tests -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Main/backend/api/signals_views.py Main/backend/tests/test_signals_endpoint.py
git commit -m "feat(api): signals wire uniformly v2 — normalize legacy artifacts"
```

---

### Task 7: FinSearch — user-facing docs + spec cross-reference

**Files:**
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Docs/source/api_reference.rst` (lines 236-243 area of the signals block ~703-732, items block ~733-769 incl. the note at 755-761)
- Modify: `/mnt/d/fingpt/github/fingpt_rcos/Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md` (top-of-file amendment pointer)

- [ ] **Step 1: Rewrite the two RST response shapes**

Signals block — change the schema pointer and field list (current lines 236-243 of the excerpt; verbatim replacement):
```rst
**Response (200):** the artifact JSON (schema:
``Heartbeat/schemas/signals-v2.schema.json``) minus internal provenance
fields, plus a computed ``staleness_hours``. Key fields: ``schema_version``
(always ``2`` — artifacts predating the 2026-07-14 field rename are
normalized at the boundary), ``profile``, ``generated_at``, ``window_hours``,
``watchlist``, ``status`` (``ok`` | ``degraded``), ``status_reason``,
``news_overview``, ``diagnostics``, and ``signals`` — a map of ticker →
``{sentiment, sentiment_score, rationale, headline, source, url, published,
guid, n_articles}`` with ``sentiment_score`` in ``[-1, 1]``.
```

Items block — field list line becomes:
```rst
``{guid, headline, url, source, published, description, tickers,
editorial_score}``. ``published`` is epoch seconds.
```

Replace the collision `.. note::` (lines 755-761) with:
```rst
.. note::

   ``editorial_score`` is the pipeline's newsworthiness score — the gate that
   decides which stories become sentiment candidates
   (``SIGNALS_MIN_EDITORIAL_SCORE``). It is unrelated to the ``[-1, 1]``
   ``sentiment_score`` served by ``/api/signals/news/``.
```

- [ ] **Step 2: Add the amendment pointer**

At the top of `2026-07-06-news-to-signals-pipeline-design.md`, under the title:
```markdown
> **Amended 2026-07-14:** §4.2/§4.4 field names superseded by
> `2026-07-14-score-field-disambiguation-design.md` — the artifact/wire field
> is now `sentiment_score` (schema v2) and the items input field is
> `editorial_score`.
```

- [ ] **Step 3: Sweep for leftover doc references**

```bash
grep -rn '``score``' Docs/source/ | grep -v editorial_score | grep -v sentiment_score
```
Expected: no hits about these two endpoints (xbrl/search relevance scores are unrelated — leave them).

- [ ] **Step 4: Commit**

```bash
git add Docs/source/api_reference.rst \
        Docs/superpowers/specs/2026-07-06-news-to-signals-pipeline-design.md
git commit -m "docs(api): editorial_score / sentiment_score, schema v2"
```

---

### Task 8: FinSearch — full verification, PR, merge gate

- [ ] **Step 1: Full local verification**

```bash
cd /mnt/d/fingpt/github/fingpt_rcos/Heartbeat && python3 -m unittest discover -s tests -v
cd /mnt/d/fingpt/github/fingpt_rcos/Main/backend && uv run pytest tests -q
grep -rn '"score"' /mnt/d/fingpt/github/fingpt_rcos/Heartbeat/news_heartbeat.py \
  /mnt/d/fingpt/github/fingpt_rcos/Heartbeat/news_signals.py \
  /mnt/d/fingpt/github/fingpt_rcos/Main/backend/api/signals_views.py
```
Expected: both suites PASS; the grep's only hits are `news_signals.py`'s LLM prompt/reply lines (~399, ~477) — the sanctioned internal contract.

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/score-field-rename
gh pr create --title "feat(api): editorial_score + sentiment_score (schema v2)" \
  --body "$(cat <<'EOF'
Disambiguates the two unrelated `score` fields (spec: Docs/superpowers/specs/2026-07-14-score-field-disambiguation-design.md).

- items: disk+wire `editorial_score`, news-story v2 — strict, legacy batches poison-pill
- signals: artifact v2 `sentiment_score`; view normalizes historical v1 so the wire is uniformly v2
- three-copy gate edited same-commit, parity corpus widened + mutation-tested
- LLM prompt/reply key and diagnostics.scores_damped unchanged

Merge gate: ATL PR-1 (sentiment_score fallback) must be deployed first. Merge auto-deploys Heartbeat + Django; run the runbook kick right after (plan Task 9).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: CHECKPOINT (user-owned)** — confirm ATL PR-1 is merged AND its deploy is live, PR review passes, then the user merges this PR. Do not self-merge.

---

### Task 9: Deploy runbook (run immediately after the FinSearch merge)

CI deploys both artifacts concurrently on the merge push: `heartbeat-tests.yml` (curl+sha256+py_compile atomic replace of both scripts into `/home/deploy/fingpt/heartbeat/`) and `backend-deploy.yml` (podman image, pre-cutover boot gate, `systemctl restart fingpt-api`). Heartbeat's timer is **daily 11:00 UTC**, so without a kick the newest batch stays old-format for up to ~24h — during which the new Django 404s `/api/news/items/` and every-20-min signals runs poison-pill (existing artifact keeps serving; `staleness_hours` grows).

- [ ] **Step 1:** Watch both workflow runs to green: `gh run list --limit 5` in the fingpt repo.
- [ ] **Step 2:** Kick a heartbeat run immediately:
```bash
ssh finsearch-deploy 'systemctl --user start finsearch-heartbeat.service'
```
- [ ] **Step 3:** Verify the new batch speaks v2 (expect `editorial_score` present, bare `"score"` absent):
```bash
ssh finsearch-deploy 'tail -1 "$(ls -t ~/fingpt/heartbeat/digests/items-*.jsonl | head -1)" | grep -c editorial_score'
ssh finsearch-deploy 'tail -1 "$(ls -t ~/fingpt/heartbeat/digests/items-*.jsonl | head -1)" | grep -c "\"score\"" || true'
```
Expected: `1` then `0`.
- [ ] **Step 4:** Verify the wire (same prod base URL + bearer used for the #359 live verification): `/api/news/items/?limit=1` → 200, `schema_version: 2`, item has `editorial_score`; `/api/signals/news/` → 200, `schema_version: 2`, entries have `sentiment_score` (normalizer, while the newest artifact is still v1); `/api/signals/news/?as_of=2026-07-10` → also v2-normalized.
- [ ] **Step 5:** After the next 20-min signals run: newest `signals-*.json` on disk has `"schema_version": 2` and `sentiment_score` natively. Check ATL's Home panel: sentiment chips render (fallback path now reading `sentiment_score`), real items in the "Latest news" column, and **no `degraded` drift badge** — PR #110's `_alarm_if_all_dropped` must not fire, since `_feed_from_items` never reads the items `score`. (During the pre-kick items-404 window the panel silently shows the Phase-A representative feed with no badge — expected, not drift.)
- [ ] **Rollback note:** revert Heartbeat and Django **together** and kick another manual heartbeat run (strictness is symmetric — a v2 batch fails the old gate exactly as a v1 batch fails the new one). ATL's fallback keeps the panel alive in both directions until PR-2.

---

### Task 10: ATL PR-2 — strict cleanup (after Task 9 verified)

**Files:**
- Modify: `/mnt/d/Github/agent-trading-lab/dashboard/backend/integrations/news_sentiment.py` (drop fallback)
- Modify: `/mnt/d/Github/agent-trading-lab/dashboard/backend/tests/test_news_sentiment_fixture.py` (both schema_version pins, signals field tuple, `ITEMS_STORY_KEYS`)
- Modify: `/mnt/d/Github/agent-trading-lab/dashboard/backend/tests/fixtures/signals-fixture.json` AND `signals-wire-fixture.json` (the pair is coupled by `test_wire_fixture_is_base_minus_strip_plus_staleness` — flip both or it goes red)
- Modify: `/mnt/d/Github/agent-trading-lab/dashboard/backend/tests/fixtures/items-wire-fixture.json` (added by #110; pins the live items key set)
- Modify: `/mnt/d/Github/agent-trading-lab/dashboard/backend/tests/test_sentiment_score_fallback.py` (fallback cases die with the fallback)
- Modify: `/mnt/d/Github/agent-trading-lab/docs/integrations/finsearch-news-sentiment.md` (drop transitional note; line 76 `schema_version` `(=1)` → `(=2)`)
- Modify: `/mnt/d/Github/agent-trading-lab/docs/integrations/finsearch-news-items.md` (line 26 extras list `score` → `editorial_score`; line 28 `currently \`1\`` → `currently \`2\``; example lines 59/62 → `"schema_version": 2` / `"editorial_score": 0.7`; rewrite the lines-33-41 blockquote — its *hypothetical* "a v2 that renamed a field" is now actual history: v2 shipped, and the drift alarm did NOT fire because the projection never read `score`)
- Modify: `/mnt/d/Github/agent-trading-lab/docs/superpowers/specs/2026-07-14-finsearch-news-story-contract-design.md` (grep `score\|schema_version` — hits at lines 62, 88, 89-90 need the v2 renames; add a superseded-by note pointing at FinSearch's 2026-07-14 disambiguation spec)
- Modify (opportunistic staleness): `docs/superpowers/specs/2026-07-13-finsearch-news-signals-panel-design.md:77,91` and `docs/superpowers/plans/2026-07-13-finsearch-news-signals-panel.md` score mentions (incl. ~857)

Steps:
- [ ] **Step 1:** Branch `feat/sentiment-score-strict` off origin/main. In `_project_entry`: `"score": sig["sentiment_score"],` (delete the fallback + its comment).
- [ ] **Step 2:** Flip the signals fixture PAIR to v2 — in BOTH `signals-fixture.json` and `signals-wire-fixture.json`: `"schema_version": 2`, each signal entry's `"score"` → `"sentiment_score"`. In `test_news_sentiment_fixture.py`: `test_fixture_matches_contract_essentials` → `assert body["schema_version"] == 2` and `"score"` → `"sentiment_score"` in its required-fields tuple (line ~108).
- [ ] **Step 3:** Flip the items fixture to v2 — in `items-wire-fixture.json`: `"schema_version": 2`, each item's `"score"` → `"editorial_score"` (keep the values). In `test_news_sentiment_fixture.py`: `ITEMS_STORY_KEYS` (line ~46) `"score"` → `"editorial_score"`; `test_items_wire_fixture_matches_contract_essentials` → `assert body["schema_version"] == 2`. Update the fixture-loader docstring's "verified against prod" date to the Task 9 verification date. Optional hygiene, same commit: rename the inert `"score"` keys in the adapter tests' inline items dicts (e.g. `test_feed_from_items_maps_exact_five_keys`, ~line 583) to `"editorial_score"` so test dicts mirror the live wire — the projection ignores them either way.
- [ ] **Step 4:** In `test_sentiment_score_fallback.py`: delete `test_project_entry_falls_back_to_v1_score` and `test_project_entry_prefers_sentiment_score_when_both_present`; keep the v2 test; module docstring now says the fallback is gone and this pins strict v2 reads.
- [ ] **Step 5:** Apply the doc edits listed above. Run the ATL suite (`pytest dashboard/backend/tests/ --timeout=180 -p no:cacheprovider`) — PASS.
- [ ] **Step 6:** Commit `feat(news): require sentiment_score (drop v1 fallback)` + a second commit `docs(news): items contract speaks v2 (editorial_score)` if the diff reads better split; push, `gh pr create` with a two-line body linking PR-1 and the FinSearch PR. User merges.

---

### Task 11: Context lake + followups

- [ ] Update `finsearch-atl-news-sentiment-bridge.md` memory: score-collision resolved (editorial_score/sentiment_score, both v2), PR numbers, fallback lifecycle; drop the stale "wire mismatch" blocker line — #110 MERGED 2026-07-14 15:36Z (`802c0852`), which also redeployed ATL prod and flipped the Home panel to real items.
- [ ] Update `heartbeat-single-file-deploy-contract.md`: parity corpus now includes the legacy-score strictness cases; VERSIONs bumped.
- [ ] Central DB: decision entry (why full-depth, no-compat) in `/mnt/d/CENTRAL-DATABASE/decisions/2026-07.md` + finsearch/ATL project.md touch-ups.
- [ ] Surface "user-facing docs to update" list: `Docs/source/api_reference.rst` ships in the PR (done in Task 7); readthedocs rebuilds on merge — verify the hosted page rendered the new note.
