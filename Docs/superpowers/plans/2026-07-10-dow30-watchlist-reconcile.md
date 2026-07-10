# Dow-30 Watchlist Reconcile (cross-repo) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **This plan spans two git repos and a production droplet** — read Global Constraints and the per-part headers carefully; each Part is its own PR/op.

**Goal:** Reconcile the FinSearch news watchlist and ATL's `DJIA_30` universe against the *actual* current Dow-30, codified once per repo with an anti-drift test, so adding/removing a company is a one-line edit. Fixes the live 35-ticker watchlist (adds `{AMGN, CRM, HON, SHW}`, removes stale `{INTC, MA, PFE, WBA, XOM}`; the `TICKER_ALIASES` table is reconciled in lockstep so name-based headlines match the new tickers) and collapses ATL's duplicated `DJIA_30` (validator + backtest script — the spec's "third copy" in the docs example was rewritten away on `origin/main`, see B5) into one canonical source.

**Architecture:** Two repos, no shared import (a cross-repo package would be overkill). Each repo gets ONE canonical list derived from the same verified ground truth, guarded by a test. FinSearch: `DOW_30`/`WATCHLIST_EXTRAS` in both stdlib-only heartbeat scripts (duplicated by the single-file contract, parity-tested). ATL: `validator.DJIA_30` is canonical; the backtest script imports it. (The docs example on `origin/main` contains no Dow list at all — commit `13a2b64` rewrote it into a MAG7 demo — so there is nothing to mirror; B5 verifies that.) The droplet's manual `HEARTBEAT_WATCHLIST` override is then removed so the code default is the single source.

**Tech Stack:** FinSearch heartbeat — Python stdlib only, `unittest`. ATL — Python, `pytest`, `ast`-based source guards, Alpaca (`alpaca-py`) for price data.

## Global Constraints

- **Ground truth — current Dow-30 (verified 2026-07-10; S&P DJI, effective 2026-06-29):**
  ```
  AAPL AMGN AMZN AXP BA CAT CRM CSCO CVX DIS GOOGL GS HD HON IBM JNJ JPM KO
  MCD MMM MRK MSFT NKE NVDA PG SHW TRV UNH V WMT
  ```
- **FinSearch (Part A):** the heartbeat is **stdlib-only, single-file per script** — do NOT add a shared module or any cross-file import; the deploy workflow fetches only `news_heartbeat.py` and `news_signals.py`. `DEFAULT_WATCHLIST` must stay a **space-joined string** (consumers call `.split()`). Anti-drift = a parity test. Test: `cd Heartbeat && python3 -m unittest discover -s tests -v`.
- **ATL (Part B):** repo is `/mnt/d/Github/agent-trading-lab`; **branch off `origin/main`** (it currently sits on another branch). Domain code must not import `api/`/`app.py`. The docs example stays **standalone/importless** (external users copy-paste + `pip install` + run it outside the repo) — mirror the list, don't import it. Test: from repo root `pytest dashboard/backend/tests/ -v` (install `pytest` first; not in requirements.txt).
- **Droplet (Part C):** production; **every command is read-only until the explicit backup+edit steps, which require user go-ahead.** Gated on Part A being merged, auto-deployed, and verified first.
- **FinSearch watchlist target = `DOW_30` ∪ `WATCHLIST_EXTRAS` = 34:** the 30 above plus `META TSLA BRK-B BTC-USD`.

---

## Part A — FinSearch: codify the watchlist (PR B, repo `fingpt_rcos`)

### Task A1: Write the failing watchlist parity + invariants test

**Files:**
- Create: `Heartbeat/tests/test_watchlist.py`

**Interfaces:**
- Consumes (after A2): `news_signals.DOW_30`, `news_signals.WATCHLIST_EXTRAS`, `news_signals.DEFAULT_WATCHLIST`, and the same three on `news_heartbeat`; plus `news_signals.TICKER_ALIASES` (news_signals only — the heartbeat digest script has no alias table).

- [ ] **Step 1: Write the test file**

```python
"""Watchlist single-source-of-truth + cross-file parity (spec 2026-07-10).

DOW_30 is duplicated in news_signals.py and news_heartbeat.py by design — the
heartbeat is stdlib-only, single-file per script, so there is no shared module.
This test is the anti-drift guard, same discipline as the schema-parity test.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_signals as ns
import news_heartbeat as nh

# Current Dow-30, verified 2026-07-10 (S&P DJI, effective 2026-06-29).
EXPECTED_DOW_30 = {
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
}
# Stale / erroneous tickers that must never reappear (the pre-fix bug set,
# incl. DOW — the ticket wrongly wanted it, but it left the index in 2024).
FORBIDDEN = {"AMEX", "DOW", "INTC", "MA", "PFE", "WBA", "XOM", "VZ"}


class TestWatchlistParity(unittest.TestCase):
    def test_dow30_matches_across_both_scripts(self):
        self.assertEqual(ns.DOW_30, nh.DOW_30)

    def test_extras_match_across_both_scripts(self):
        self.assertEqual(ns.WATCHLIST_EXTRAS, nh.WATCHLIST_EXTRAS)

    def test_default_watchlist_matches_across_both_scripts(self):
        self.assertEqual(ns.DEFAULT_WATCHLIST, nh.DEFAULT_WATCHLIST)

    def test_dow30_is_exactly_the_current_index(self):
        self.assertEqual(set(ns.DOW_30), EXPECTED_DOW_30)
        self.assertEqual(len(ns.DOW_30), 30)
        self.assertEqual(len(set(ns.DOW_30)), 30)          # unique
        self.assertTrue(all(t == t.upper() for t in ns.DOW_30))

    def test_no_stale_or_erroneous_tickers(self):
        self.assertEqual(set(ns.DOW_30) & FORBIDDEN, set())
        self.assertEqual(set(ns.WATCHLIST_EXTRAS) & FORBIDDEN, set())

    def test_extras_are_disjoint_from_dow30(self):
        self.assertEqual(set(ns.WATCHLIST_EXTRAS) & set(ns.DOW_30), set())

    def test_default_watchlist_is_sorted_union_of_34(self):
        expected = " ".join(sorted(set(ns.DOW_30) | set(ns.WATCHLIST_EXTRAS)))
        self.assertEqual(ns.DEFAULT_WATCHLIST, expected)
        self.assertEqual(len(ns.DEFAULT_WATCHLIST.split()), 34)

    def test_reconcile_additions_present(self):
        for t in ("AMGN", "CRM", "HON", "SHW"):
            self.assertIn(t, ns.DOW_30, t)

    def test_ticker_aliases_cover_exactly_the_watchlist(self):
        # news_signals-only: the subject-relevance gate (spec D8) matches
        # name-based headlines through TICKER_ALIASES, so a ticker in the
        # watchlist but not the alias table is only half-added (and
        # warn_alias_gaps WARNs every sweep). Set EQUALITY also proves the
        # stale INTC/MA/PFE/WBA/XOM aliases are gone.
        union = set(ns.DOW_30) | set(ns.WATCHLIST_EXTRAS)
        self.assertEqual(set(ns.TICKER_ALIASES), union)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `Heartbeat/`): `python3 -m unittest tests.test_watchlist -v`
Expected: FAIL — `AttributeError: module 'news_signals' has no attribute 'DOW_30'`.

### Task A2: Implement the canonical lists in both heartbeat scripts

**Files:**
- Modify: `Heartbeat/news_signals.py:31` (the `DEFAULT_WATCHLIST = "..."` line) and `:198-214` (the `TICKER_ALIASES` dict)
- Modify: `Heartbeat/news_heartbeat.py:44` (the `DEFAULT_WATCHLIST = "..."` line)

**Interfaces:**
- Produces: `DOW_30: list[str]` (30), `WATCHLIST_EXTRAS: list[str]` (4), `DEFAULT_WATCHLIST: str` (space-joined sorted union of 34) — identical in both modules; `news_signals.TICKER_ALIASES` keyed by exactly `DOW_30 ∪ WATCHLIST_EXTRAS` (34 entries).

- [ ] **Step 1: Replace the one-line default in `news_signals.py`**

Replace line 31 (`DEFAULT_WATCHLIST = "AAPL MSFT NVDA GOOGL AMZN META TSLA BRK-B JPM BTC-USD"`) with:
```python
# Dow Jones Industrial Average constituents.
# Source: S&P Dow Jones Indices; effective 2026-06-29 (GOOGL replaced VZ).
# Reconcile against the official index — never a hand-maintained copy — when
# the composition changes. Parity-tested against news_heartbeat.py
# (Heartbeat/tests/test_watchlist.py). To add/remove a ticker, edit one list.
DOW_30 = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
]
# Non-Dow tickers FinSearch also tracks for its own community digests.
WATCHLIST_EXTRAS = ["META", "TSLA", "BRK-B", "BTC-USD"]
DEFAULT_WATCHLIST = " ".join(sorted(set(DOW_30) | set(WATCHLIST_EXTRAS)))
```

- [ ] **Step 2: Replace the identical one-line default in `news_heartbeat.py`**

Replace line 44 (same old string) with the **exact same block** as Step 1 (the parity test enforces they are identical).

- [ ] **Step 3: Reconcile `TICKER_ALIASES` in `news_signals.py` (adds 4, removes 5 stale)**

The subject-relevance gate (spec D8, `is_subject`/`_ticker_matches`) matches a story to a watchlist ticker by literal symbol OR by a lowercase company-name alias from `TICKER_ALIASES` (`news_signals.py:198-214`). Without aliases, name-based headlines ("Salesforce beats Q2 estimates") never tag the new tickers, and `warn_alias_gaps()` WARNs on every non-empty sweep.

In the `TICKER_ALIASES` dict (keep alphabetical order, match the existing tuple style):
- **Add** the four new Dow members:
```python
    "AMGN": ("amgen",),
    "CRM": ("salesforce",),
    "HON": ("honeywell",),
    "SHW": ("sherwin-williams", "sherwin williams"),
```
  (`AMGN` slots between `AAPL` and `AMZN`; `CRM` after `CAT`; `HON` after `HD`; `SHW` after `PG`. Two SHW spellings mirror the existing `"jpmorgan", "jp morgan"` idiom — headlines are inconsistent about the hyphen. `"honeywell"` still matches post-spin-off "Honeywell Technologies".)
- **Remove** the five stale entries: `"INTC": ("intel",)`, `"MA": ("mastercard",)`, `"PFE": ("pfizer",)`, `"WBA": ("walgreens",)`, `"XOM": ("exxon",)`.

Result: exactly 34 keys = `DOW_30 ∪ WATCHLIST_EXTRAS` (asserted by `test_ticker_aliases_cover_exactly_the_watchlist`). `news_heartbeat.py` has no alias table — nothing to mirror there.

- [ ] **Step 4: Run the watchlist test — must pass**

Run (from `Heartbeat/`): `python3 -m unittest tests.test_watchlist -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Run the FULL heartbeat suite — no regressions**

Run (from `Heartbeat/`): `python3 -m unittest discover -s tests -v`
Expected: all PASS. (`test_news_signals.py`'s `cfg["watchlist"] == sorted(set(ns.DEFAULT_WATCHLIST.split()))` derives from `DEFAULT_WATCHLIST`, so it tracks the new value automatically.)

- [ ] **Step 6: Commit**

```bash
git add Heartbeat/news_signals.py Heartbeat/news_heartbeat.py Heartbeat/tests/test_watchlist.py
git commit -m "feat(heartbeat): codify DOW_30 watchlist (default now Dow-30 ∪ extras, 34) + parity test; reconcile TICKER_ALIASES"
```

### Task A3: Update the docs (example env + README)

**Files:**
- Modify: `Heartbeat/.env.heartbeat.example:16`
- Modify: `Heartbeat/README.md` (the watchlist section, lines 108–112)

- [ ] **Step 1: Update `.env.heartbeat.example`**

Replace line 16 and give it an accurate comment. Find:
```
HEARTBEAT_WATCHLIST=AAPL MSFT NVDA GOOGL AMZN META TSLA BRK-B JPM BTC-USD
```
Replace with:
```
# Optional override. The code default is Dow-30 ∪ extras (34 tickers), defined
# as DOW_30/WATCHLIST_EXTRAS in news_signals.py + news_heartbeat.py. Leave unset
# to track the code default; set only to hot-patch the universe without a deploy.
# HEARTBEAT_WATCHLIST=AAPL AMGN AMZN AXP BA BRK-B BTC-USD CAT CRM CSCO CVX DIS GOOGL GS HD HON IBM JNJ JPM KO MCD META MMM MRK MSFT NKE NVDA PG SHW TRV TSLA UNH V WMT
```

- [ ] **Step 2: Update the README watchlist section**

Find the block (search for the anchor string `HEARTBEAT_WATCHLIST=AAPL AMZN AXP BA BRK-B BTC-USD CAT CSCO CVX DIS`). Replace the "(35 tickers: heartbeat default ∪ DJIA-30 …)" guidance and its stale env line with:
```markdown
Universe (spec 2026-07-10) — the watchlist is defined **in code** as
`DOW_30 ∪ WATCHLIST_EXTRAS` (34 tickers) in `news_signals.py` and
`news_heartbeat.py` (parity-tested in `tests/test_watchlist.py`). The droplet
does **not** set `HEARTBEAT_WATCHLIST`; the code default drives it, so changing
the universe is a one-line edit + a normal deploy. `HEARTBEAT_WATCHLIST` remains
an optional emergency override (space-separated) if you must hot-patch without a
deploy.
```

- [ ] **Step 3: Commit**

```bash
git add Heartbeat/.env.heartbeat.example Heartbeat/README.md
git commit -m "docs(heartbeat): watchlist is code-defined (Dow-30 ∪ extras); env override optional"
```

> **PR B** = Tasks A1–A3. Open against `fingpt_rcos` main. On merge, the Heartbeat CI auto-deploys `news_signals.py`+`news_heartbeat.py` to the droplet (the stale env override still masks the new default until Part C removes it — no regression window).

---

## Part B — ATL: collapse the 3 `DJIA_30` copies (PR C, repo `agent-trading-lab`)

### Task B1: Create the PR branch off `origin/main`

- [ ] **Step 1: Branch**

```bash
cd /mnt/d/Github/agent-trading-lab
git fetch origin
git checkout -b fix/djia30-current-index origin/main
```
Expected: on a fresh branch tracking the latest main.

### Task B2: Write the failing `DJIA_30` guard test

**Files:**
- Create: `dashboard/backend/tests/test_djia30_universe.py`

- [ ] **Step 1: Write the test**

```python
"""DJIA_30 single-source-of-truth guard (FinSearch↔ATL reconcile 2026-07-10).

validator.DJIA_30 is the one canonical Dow-30 for ATL: the backtest script and
the v2 API contract import it. (The docs example on origin/main is a MAG7 demo
with no Dow list — commit 13a2b64 — so there is nothing to guard there.)
Verified 2026-07-10 (S&P DJI, effective 2026-06-29).
"""
import ast
from pathlib import Path

from dashboard.backend.infrastructure.llm.validator import DJIA_30

_REPO = Path(__file__).resolve().parents[3]          # .../agent-trading-lab
_BHA = _REPO / "dashboard" / "scripts" / "backtest_hourly_agent.py"

EXPECTED = {
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
}
FORBIDDEN = {"AMEX", "DOW", "INTC", "MA", "PFE", "WBA", "XOM", "VZ", "NFLX", "TSLA"}


def _module_djia30_literal(path):
    """The set in a top-level `DJIA_30 = [...]` literal, or None if the module
    has no such assignment (i.e. it imports the constant instead)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "DJIA_30":
                    return {ast.literal_eval(e) for e in node.value.elts}
    return None


def test_validator_is_the_current_index():
    assert set(DJIA_30) == EXPECTED
    assert len(DJIA_30) == 30
    assert len(set(DJIA_30)) == 30
    assert set(DJIA_30) & FORBIDDEN == set()


def test_backtest_script_imports_not_hardcodes():
    # After the fix there is no local DJIA_30 = [...] literal — it imports it.
    assert _module_djia30_literal(_BHA) is None


def test_api_universe_tracks_validator():
    from dashboard.backend.api.v2.models import UNIVERSE
    assert set(UNIVERSE) == EXPECTED
```

- [ ] **Step 2: Run it to verify it fails**

Run (from repo root): `pytest dashboard/backend/tests/test_djia30_universe.py -v`
Expected: FAIL — `test_validator_is_the_current_index` (validator still has AMEX + stale set), `test_backtest_script_imports_not_hardcodes` (local literal still present). `test_api_universe_tracks_validator` fails with the validator (UNIVERSE derives from it).

### Task B3: Fix the canonical `DJIA_30` in `validator.py`

**Files:**
- Modify: `dashboard/backend/infrastructure/llm/validator.py:24-32`

- [ ] **Step 1: Replace the comment + `DJIA_30` list**

Replace the `# DJIA 30 stocks (must match backtest_hourly_agent.py)` comment and the `DJIA_30 = [ ... ]` block (lines ~24-32) with:
```python
# DJIA 30 constituents. Source: S&P Dow Jones Indices, effective 2026-06-29
# (GOOGL replaced VZ). Canonical for ATL — the backtest script and the v2 API
# contract import this (guarded by tests/test_djia30_universe.py). Reconcile
# against the official index on change.
DJIA_30 = [
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
]
```

- [ ] **Step 2: Run the guard test's validator check**

Run: `pytest dashboard/backend/tests/test_djia30_universe.py::test_validator_is_the_current_index -v`
Expected: PASS.

### Task B4: Import `DJIA_30` in `backtest_hourly_agent.py` (remove the duplicate)

**Files:**
- Modify: `dashboard/scripts/backtest_hourly_agent.py:50` (import) and `:87-98` (remove banner + local `DJIA_30`)

- [ ] **Step 1: Add `DJIA_30` to the existing validator import (line 50)**

Change:
```python
from dashboard.backend.infrastructure.llm.validator import create_safe_prompt, create_prompt, validate_llm_response, LLMTradingDecision, TOP_10_STOCKS
```
to:
```python
from dashboard.backend.infrastructure.llm.validator import create_safe_prompt, create_prompt, validate_llm_response, LLMTradingDecision, TOP_10_STOCKS, DJIA_30
```

- [ ] **Step 2: Delete the local `DJIA_30` banner + literal**

Remove these lines (the 3-line banner at 87-89 and the hardcoded list at 91-98), keeping the `TOP_10 = TOP_10_STOCKS` line below them:
```python
# ============================================================================
# DJIA 30 Stocks
# ============================================================================

DJIA_30 = [
    "AAPL", "MSFT", "JPM", "V", "JNJ",
    "WMT", "PG", "MA", "HD", "DIS",
    "MCD", "PFE", "CSCO", "IBM", "INTC",
    "XOM", "AXP", "KO", "CAT", "GS",
    "MRK", "NVDA", "BA", "UNH", "MMM",
    "CVX", "NKE", "AMEX", "TRV", "WBA"
]
```
Then replace the retained lines' TWO comments (a header line above plus an inline trailing comment) with one that reflects both constants are now imported. Current (lines 100-101):
```python
# Top 10 DJIA stocks (for buy-and-hold and baseline)
TOP_10 = TOP_10_STOCKS  # Import from llm_validator to keep them in sync
```
New:
```python
# DJIA_30 and TOP_10_STOCKS both imported from validator to keep them in sync
TOP_10 = TOP_10_STOCKS
```

- [ ] **Step 3: Byte-compile to confirm no syntax error / dangling reference**

Run (from repo root): `python -m py_compile dashboard/scripts/backtest_hourly_agent.py`
Expected: no output (success). Then: `pytest dashboard/backend/tests/test_djia30_universe.py::test_backtest_script_imports_not_hardcodes -v` → PASS.

### Task B5: Verify the docs example needs no change (origin/main rewrote it)

**Files:** none — validation only.

**Rationale (corrects the spec):** the spec described `docs/examples/simple_trading_agent_backtest.py` as a third, divergent `DJIA_30` copy ("uses AMGN where the others have AMEX"). That described the **stale working tree of another branch** (`docs/correct-engines-services-deleted-shims`, last touch `fd073ed` 2026-07-04). On `origin/main`, commit `13a2b64` (2026-07-06) rewrote the file into a standalone Magnificent-7 example (`MAG7 = [...]`, `DJIA_INDEX = "^DJI"` used only as a chart benchmark) with **zero** `DJIA_30` references. There is no third copy to fix — the collapse is validator + backtest script only (verified 2026-07-10 via `git show origin/main:docs/examples/simple_trading_agent_backtest.py`).

- [ ] **Step 1: Confirm no `DJIA_30` exists in the example on this branch**

Run (from repo root): `git grep -n "DJIA_30" -- docs/examples/`
Expected: no output. (If a `DJIA_30` literal DOES appear here, the branch was not cut from current `origin/main` — STOP and redo Task B1.)

- [ ] **Step 2: Run the full guard test**

Run: `pytest dashboard/backend/tests/test_djia30_universe.py -v`
Expected: all 3 tests PASS.

### Task B6: Run the full ATL suite; triage universe-driven changes

**Files:** none new — triage + update existing fixtures/snapshots only where the corrected universe demands it.

- [ ] **Step 1: Run the whole backend suite**

Run (from repo root): `pytest dashboard/backend/tests/ -v`

- [ ] **Step 2: Triage failures with a strict criterion**

The corrected `DJIA_30` changes leaderboard/baseline constituents, so some tests that encode the OLD universe or baseline numbers may fail. For each failure:
- **Expected (update it):** the assertion hard-codes a removed ticker (`AMEX`/`DOW`/`INTC`/`MA`/`PFE`/`WBA`/`XOM`) or a baseline metric (`equal_weight_index`/`equal_weight_buyhold`/`mean_variance`) computed from the old universe. Update the fixture/snapshot to the corrected universe.
- **STOP (do not update):** a failure NOT explained by the universe change is a real regression — investigate before proceeding, do not paper over it.

Document each updated fixture in the commit message.

- [ ] **Step 3: Re-run until green**

Run: `pytest dashboard/backend/tests/ -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add dashboard/backend/infrastructure/llm/validator.py \
        dashboard/scripts/backtest_hourly_agent.py \
        dashboard/backend/tests/test_djia30_universe.py
# plus any fixtures/snapshots updated in Step 2
git commit -m "fix(universe): DJIA_30 -> current Dow-30 (2026-06-29); collapse duplicate into canonical validator source"
```

### Task B7: Align the data layer (Alpaca coverage) + confirm the API layer

**Files:** none — validation only.

- [ ] **Step 1: Confirm price coverage for the 6 newly-added constituents**

The 6 additions vs the old ATL universe are `{AMGN, AMZN, CRM, GOOGL, HON, SHW}`. ATL loads bars via `AlpacaDataLoader` (`dashboard/backend/infrastructure/market_data/alpaca_bars.py`); Alpaca covers all major US equities, so no data-source change is expected. The real signature (verified 2026-07-10) is `AlpacaDataLoader(api_key=None, secret_key=None)` / `fetch_bars(symbols: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]`. If Alpaca credentials are configured (`credentials/alpaca.json` or `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`), verify empirically:
```bash
python - <<'PY'
from dashboard.backend.infrastructure.market_data.alpaca_bars import AlpacaDataLoader
SYMS = ["AMGN", "AMZN", "CRM", "GOOGL", "HON", "SHW"]
bars = AlpacaDataLoader().fetch_bars(SYMS, start="2026-06-01", end="2026-06-08")
for sym in SYMS:
    df = bars.get(sym)
    print(sym, "OK" if df is not None and len(df) else "MISSING")
PY
```
Expected: all six `OK`. If any is `MISSING`, STOP and resolve the data gap before this reaches production backtests.

- [ ] **Step 2: Confirm the API contract auto-tracked**

`dashboard/backend/api/v2/models.py` builds `UNIVERSE = list(DJIA_30)` from the validator import, so the v2 wire contract updated automatically — already asserted by `test_api_universe_tracks_validator`. No code change needed. Note in the PR description that `/api/v2` now advertises the corrected 30-symbol universe.

> **PR C** = Tasks B1–B7. Open against `agent-trading-lab` main; link to `Docs/superpowers/specs/2026-07-10-signals-asof-and-dow30-reconcile-design.md`. Call out in the PR body: leaderboard/baseline numbers change (corrected constituents) — this is intended.

---

## Part C — Droplet runbook: remove the stale watchlist override (production, MANUAL)

> ⚠️ **Production mutation. Do NOT run until: (1) PR B is merged and the Heartbeat CI deploy is green, (2) you have re-confirmed the current Dow-30 is still accurate, and (3) the user has explicitly approved this step.** Everything through Step 2 is read-only.

**Files:** `/home/deploy/fingpt/envs/.env.heartbeat` on the droplet (`ssh finsearch`).

- [ ] **Step 1: Verify the corrected code default is deployed (read-only)**

```bash
ssh finsearch "sudo -iu deploy python3 -c 'import sys; sys.path.insert(0,\"/home/deploy/fingpt/heartbeat\"); import news_signals as ns; print(len(ns.DEFAULT_WATCHLIST.split())); print(ns.DEFAULT_WATCHLIST)'"
```
Expected: `34` then the 34-ticker list including AMGN/CRM/HON/SHW and NOT INTC/MA/PFE/WBA/XOM. If not 34, the deploy hasn't landed — STOP.

- [ ] **Step 2: Show the current override (read-only)**

```bash
ssh finsearch "sudo -iu deploy grep -n '^HEARTBEAT_WATCHLIST=' /home/deploy/fingpt/envs/.env.heartbeat"
```
Expected: the stale 35-ticker line. (This is what we remove.)

- [ ] **Step 3: Back up the env file**

```bash
ssh finsearch "sudo -iu deploy cp -a /home/deploy/fingpt/envs/.env.heartbeat /home/deploy/fingpt/envs/.env.heartbeat.bak-20260710-preunion"
ssh finsearch "sudo -iu deploy ls -la /home/deploy/fingpt/envs/.env.heartbeat.bak-20260710-preunion"
```
Expected: the backup exists.

- [ ] **Step 4: Remove the override line**

```bash
ssh finsearch "sudo -iu deploy sed -i '/^HEARTBEAT_WATCHLIST=/d' /home/deploy/fingpt/envs/.env.heartbeat"
ssh finsearch "sudo -iu deploy grep -c '^HEARTBEAT_WATCHLIST=' /home/deploy/fingpt/envs/.env.heartbeat"
```
Expected: `0` (line removed). The code default (34) now governs.

- [ ] **Step 5: Verify after the next signals sweep (~20 min)**

After the next `finsearch-signals` timer run, confirm the produced artifact reflects the new universe (`sudo -u`, NOT `-iu`: verified live 2026-07-10 that `-i` drops shell state between `;`-statements, leaving `$f` empty → `IndexError`; the single-statement commands in Steps 1–4 are unaffected):
```bash
ssh finsearch "sudo -u deploy bash -lc 'f=\$(ls -t /home/deploy/fingpt/heartbeat/signals/signals-*.json | head -1); python3 -c \"import json,sys; w=json.load(open(sys.argv[1]))[\\\"watchlist\\\"]; print(len(w)); print(w)\" \$f'"
```
Expected: `34` and a watchlist containing AMGN/CRM/HON/SHW, without INTC/MA/PFE/WBA/XOM.

- [ ] **Step 6: Record the change**

Note the droplet edit (file, backup name, timestamp, sweep-verified) in the CENTRAL-DATABASE heartbeat topology record. Rollback if ever needed: restore `.env.heartbeat.bak-20260710-preunion`.

---

## Self-Review

- **Spec coverage:** target 34-ticker watchlist → A2; codify + parity test → A1/A2; `TICKER_ALIASES` reconcile → A2 Step 3 + A1 alias-coverage test; example env + README → A3; ATL duplicate copies → one canonical → B3 (validator) + B4 (import), with B5 verifying the docs example (rewritten on `origin/main`) carries no copy; guard test → B2; data-layer alignment → B7 Step 1; API-layer alignment → B7 Step 2 + B2 `test_api_universe_tracks_validator`; `DJIA_SYMBOLS` left out of scope (per spec D4) — intentionally untouched; droplet override removal → Part C. All spec §"Item 2" points covered.
- **Placeholder scan:** none — real code/commands throughout. B7 Step 1 uses the verified `fetch_bars` signature; Part C's ssh commands were live-verified read-only on 2026-07-10 (Step 5 uses `sudo -u`, not `-iu` — `-i` drops shell state between `;`-statements).
- **Type consistency:** `DOW_30`/`WATCHLIST_EXTRAS`/`DEFAULT_WATCHLIST` names identical across A1 (test), A2 (impl), C (verify); `DJIA_30`/`EXPECTED`/`_module_djia30_literal` consistent across B2–B4; `FORBIDDEN` sets align with the removed tickers in both repos.
- **Ground-truth consistency:** the 30-ticker `EXPECTED`/`DOW_30`/`DJIA_30` literal is byte-identical in `test_watchlist.py`, `news_signals.py`, `news_heartbeat.py`, `validator.py`, and `test_djia30_universe.py`.
- **Divergences from spec flagged (2026-07-10 review):** (1) the spec's "third divergent copy in the docs example" described the stale working tree of another branch — `origin/main` commit `13a2b64` rewrote the file with no Dow list, so B5 verifies absence instead of mirroring; (2) the spec's Item 2 missed the `TICKER_ALIASES` table — reconciled in A2 Step 3 (adds AMGN/CRM/HON/SHW, drops stale INTC/MA/PFE/WBA/XOM per user decision).
