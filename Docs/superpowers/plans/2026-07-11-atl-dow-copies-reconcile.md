# ATL Dow-ish Copies Reconcile (§PR-C.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse every remaining Dow-ish ticker-list copy in AgenticTrading onto the canonical `validator.DJIA_30` / `TOP_10_STOCKS`, and extend the single-source-of-truth guard so none can drift again.

**Architecture:** After ATL #91, `dashboard/backend/infrastructure/llm/validator.py` holds the one canonical `DJIA_30` (current index, eff. 2026-06-29) and `TOP_10_STOCKS`, guarded by `dashboard/backend/tests/test_djia30_universe.py`. Four pre-existing copies were scoped out of #91 and deferred as §PR-C.1: two `DJIA_SYMBOLS` literals (old list with never-members NFLX/TSLA), the committee bot's `SYMBOLS` literal, and the frontend `djia` preset (15 stale tickers incl. GE/INTC). Each becomes an import/derivation of the canonical constant (or, for JS, a mirrored literal enforced textually by the guard).

**Tech Stack:** Python 3.13, pytest (venv: `~/atl-venv/bin/python`), vanilla JS frontend. Repo: `/mnt/d/Github/agent-trading-lab` (remote `origin` = Open-Finance-Lab/AgenticTrading). CI (`.github/workflows/ci.yml`) runs `pytest dashboard/backend/tests/` on PRs to main.

## Global Constraints

- Branch: `fix/dow-copies-reconcile` off current `origin/main` (fe073b1). Never commit to main.
- Canonical 30 (alphabetical, from `validator.py:28`): `AAPL, AMGN, AMZN, AXP, BA, CAT, CRM, CSCO, CVX, DIS, GOOGL, GS, HD, HON, IBM, JNJ, JPM, KO, MCD, MMM, MRK, MSFT, NKE, NVDA, PG, SHW, TRV, UNH, V, WMT`.
- Canonical top-10 (from `validator.py`): `TOP_10_STOCKS = ["AAPL", "MSFT", "JPM", "V", "JNJ", "WMT", "PG", "AXP", "HD", "DIS"]`.
- Forbidden tickers anywhere in a Dow list: `AMEX, DOW, INTC, MA, PFE, WBA, XOM, VZ, NFLX, TSLA`.
- No new local Dow list literals in Python — copies must import from `dashboard.backend.infrastructure.llm.validator`. The frontend (JS) keeps a literal but the guard test pins it to the canonical set.
- Intentional behavior changes (state them in commit messages, do not "fix around" them): the 10-stock baskets in `paper.py`/`scripts/backtest.py` move from "first 10 of the old list" (`…, UNH, NVDA, HD`) to canonical `TOP_10_STOCKS` (`…, AXP, HD, DIS`); the committee bot default universe and the frontend preset move to the current 30.
- Test runner: `cd /mnt/d/Github/agent-trading-lab && ~/atl-venv/bin/python -m pytest <paths> -q -p no:cacheprovider`. Full-suite gate: `dashboard/backend/tests/` (678+ tests, all must pass; 3 route-contract tests were fixed upstream by #92 — a failure there means your rebase is stale, not "pre-existing").
- Repo quirk: `dashboard/scripts/*.py` bootstrap imports via `from _bootstrap import ensure_repo_root; ensure_repo_root()` — canonical imports in scripts must come AFTER the `ensure_repo_root()` call.

---

### Task 1: Python reconcile + guard extension

**Files:**
- Modify: `dashboard/backend/domain/backtesting/baselines/paper.py:28-35, 169-170`
- Modify: `dashboard/scripts/backtest.py:55-62, 303`
- Modify: `dashboard/scripts/alpaca_trader_with_committee.py:30-40`
- Modify: `dashboard/backend/tests/domain/backtesting/baselines/test_paper_baselines_move.py:65-68, 131`
- Test (extend): `dashboard/backend/tests/test_djia30_universe.py`

**Interfaces:**
- Consumes: `dashboard.backend.infrastructure.llm.validator.DJIA_30` (list of 30 str) and `TOP_10_STOCKS` (list of 10 str) — already exist, do not touch validator.py.
- Produces: `paper.DJIA_SYMBOLS` remains an importable module attribute (tests import it), now `== list(DJIA_30)`. Guard helpers `_module_dow_literal(path)` and `_imports_canonical(path)` in `test_djia30_universe.py` (Task 2's frontend test lives in the same file but is added in Task 2).

- [ ] **Step 1: Write the failing guard tests (RED)**

In `dashboard/backend/tests/test_djia30_universe.py`, replace the whole module docstring, the imports, module constants, the `_module_djia30_literal` helper, and the existing `test_backtest_script_imports_not_hardcodes` with the following (keep `test_validator_is_the_current_index`, `test_api_universe_tracks_validator`, `test_top10_is_subset_of_djia30` exactly as they are):

```python
"""Dow-30 single-source-of-truth guard (FinSearch↔ATL reconcile 2026-07-10;
copies collapsed 2026-07-11, §PR-C.1).

validator.DJIA_30 is the one canonical Dow-30 for ATL. Every other Dow-ish
list must import it (Python) or mirror it exactly (frontend, enforced
textually below): the backtest scripts, the v2 API contract, the
paper-trading baselines, and the app.js `djia` universe preset. The
committee trading script (alpaca_trader_with_committee.py) is deliberately
user-customizable (⭐ CUSTOMIZE) — the guard pins only its *default*, which
derives from DJIA_30.
Index verified 2026-07-10 (S&P DJI, effective 2026-06-29).
"""
import ast
from pathlib import Path

from dashboard.backend.infrastructure.llm.validator import DJIA_30

_REPO = Path(__file__).resolve().parents[3]          # .../agent-trading-lab
_SCRIPTS = _REPO / "dashboard" / "scripts"
_BHA = _SCRIPTS / "backtest_hourly_agent.py"
_BACKTEST = _SCRIPTS / "backtest.py"
_COMMITTEE = _SCRIPTS / "alpaca_trader_with_committee.py"
_PAPER = (_REPO / "dashboard" / "backend" / "domain" / "backtesting"
          / "baselines" / "paper.py")

EXPECTED = {
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
}
FORBIDDEN = {"AMEX", "DOW", "INTC", "MA", "PFE", "WBA", "XOM", "VZ", "NFLX", "TSLA"}

# Names under which Dow-ish copies have historically appeared.
_DOW_NAMES = {"DJIA_30", "DJIA_SYMBOLS", "SYMBOLS"}


def _module_dow_literal(path, names=frozenset(_DOW_NAMES)):
    """The set in a top-level `<name> = [...]` list literal for any Dow-ish
    name, or None if the module has no such literal (i.e. it imports or
    derives the constant instead — `SYMBOLS = list(DJIA_30)` is not a
    literal and passes)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        for tgt in targets:
            if (isinstance(tgt, ast.Name) and tgt.id in names
                    and isinstance(node.value, (ast.List, ast.Tuple, ast.Set))):
                return {ast.literal_eval(e) for e in node.value.elts}
    return None


def _imports_canonical(path):
    """True if the module has `from ...infrastructure.llm.validator import
    DJIA_30` (or TOP_10_STOCKS)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom) and node.module
                and node.module.endswith("infrastructure.llm.validator")):
            if any(a.name in {"DJIA_30", "TOP_10_STOCKS"} for a in node.names):
                return True
    return False


def test_backtest_script_imports_not_hardcodes():
    # No script carries its own Dow list literal — each imports the canonical.
    for path in (_BHA, _BACKTEST, _COMMITTEE):
        assert _module_dow_literal(path) is None, path.name
        assert _imports_canonical(path), path.name


def test_paper_baselines_track_canonical():
    from dashboard.backend.domain.backtesting.baselines.paper import DJIA_SYMBOLS
    assert list(DJIA_SYMBOLS) == list(DJIA_30)
    assert _module_dow_literal(_PAPER) is None
```

- [ ] **Step 2: Run the guard tests to verify they fail**

Run: `cd /mnt/d/Github/agent-trading-lab && ~/atl-venv/bin/python -m pytest dashboard/backend/tests/test_djia30_universe.py -q -p no:cacheprovider`
Expected: `test_backtest_script_imports_not_hardcodes` FAILS (backtest.py has a `DJIA_SYMBOLS` literal; committee script has a `SYMBOLS` literal) and `test_paper_baselines_track_canonical` FAILS (paper.py literal, old members). The three kept tests still pass.

- [ ] **Step 3: Reconcile `paper.py`**

Replace lines 28-35 (the `DJIA_SYMBOLS = [...]` literal) with:

```python
from dashboard.backend.infrastructure.llm.validator import DJIA_30, TOP_10_STOCKS

# Canonical Dow-30 (single source: validator.DJIA_30, guarded by
# tests/test_djia30_universe.py), kept under the historical local name.
DJIA_SYMBOLS = list(DJIA_30)
```

(Place the `from …validator import` line with the other `dashboard.backend.…` imports directly above, then the two comment lines + assignment where the literal was.)

In `fetch_buy_and_hold_djia` (currently lines 169-170), replace:

```python
            # Use first 10 DJIA symbols (faster)
            sample_symbols = DJIA_SYMBOLS[:10]
```

with:

```python
            # Use the canonical 10-stock basket (faster than all 30)
            sample_symbols = TOP_10_STOCKS
```

- [ ] **Step 4: Update `test_paper_baselines_move.py`**

Replace `test_djia_symbols_unchanged` (lines 65-68) with:

```python
def test_djia_symbols_track_canonical():
    from dashboard.backend.infrastructure.llm.validator import DJIA_30
    assert DJIA_SYMBOLS == list(DJIA_30)
    assert len(DJIA_SYMBOLS) == 30
```

Update the comment on line 131 from `# 10 symbols requested (first 10 of DJIA_SYMBOLS); each returns 2 bars.` to `# 10 symbols requested (the canonical TOP_10_STOCKS basket); each returns 2 bars.` — the assertions in that test stay as they are (`TOP_10_STOCKS[0]` is still `"AAPL"`, still 10 calls).

- [ ] **Step 5: Reconcile `scripts/backtest.py`**

Replace lines 55-62 (the `DJIA_SYMBOLS = [...]` literal) with:

```python
from dashboard.backend.infrastructure.llm.validator import DJIA_30, TOP_10_STOCKS

# Canonical Dow-30 (single source: validator.DJIA_30, guarded by
# tests/test_djia30_universe.py), kept under the historical local name.
DJIA_SYMBOLS = list(DJIA_30)
```

(The import must sit after the existing `ensure_repo_root()` call — put it right below the `from dashboard.backend.database import db` line at :49, and keep the assignment in the Configuration section where the literal was.)

At line 303, replace:

```python
    buy_hold_strategy = BuyHoldStrategy(engine, DJIA_SYMBOLS[:10])  # Top 10 for diversity
```

with:

```python
    buy_hold_strategy = BuyHoldStrategy(engine, TOP_10_STOCKS)  # canonical top-10 basket
```

- [ ] **Step 6: Reconcile `scripts/alpaca_trader_with_committee.py`**

Below the existing `from dashboard.backend.paths import DATA_DIR, DASHBOARD_DIR` (line 24), add:

```python
from dashboard.backend.infrastructure.llm.validator import DJIA_30
```

Replace lines 30-40 (the `# Trading config` comment block + `SYMBOLS = [...]` literal + trailing `# DJIA Full 30 Stocks` comment) with:

```python
# Trading config
# ⭐ CUSTOMIZE THIS LIST to change which stocks are scanned for trading.
# Defaults to the canonical Dow-30 (validator.DJIA_30) — replace with any
# ticker list to customize.
SYMBOLS = list(DJIA_30)
```

- [ ] **Step 7: Run the guard + baselines tests to verify they pass**

Run: `cd /mnt/d/Github/agent-trading-lab && ~/atl-venv/bin/python -m pytest dashboard/backend/tests/test_djia30_universe.py dashboard/backend/tests/domain/backtesting/baselines/test_paper_baselines_move.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 8: Sanity-import the two scripts**

Run: `cd /mnt/d/Github/agent-trading-lab && ~/atl-venv/bin/python -c "import ast; [ast.parse(open(f).read()) for f in ['dashboard/scripts/backtest.py','dashboard/scripts/alpaca_trader_with_committee.py']]; print('parse ok')"`
Expected: `parse ok`. (Do NOT actually import/run the scripts — the committee bot is a live-trading entrypoint.)

- [ ] **Step 9: Run the full backend suite**

Run: `cd /mnt/d/Github/agent-trading-lab && ~/atl-venv/bin/python -m pytest dashboard/backend/tests/ -q -p no:cacheprovider`
Expected: all pass, 0 failures.

- [ ] **Step 10: Commit**

```bash
cd /mnt/d/Github/agent-trading-lab
git add dashboard/backend/domain/backtesting/baselines/paper.py dashboard/scripts/backtest.py dashboard/scripts/alpaca_trader_with_committee.py dashboard/backend/tests/domain/backtesting/baselines/test_paper_baselines_move.py dashboard/backend/tests/test_djia30_universe.py
git commit -m "fix(universe): collapse remaining Python Dow-30 copies onto canonical DJIA_30

paper.py DJIA_SYMBOLS, scripts/backtest.py DJIA_SYMBOLS, and the committee
bot's SYMBOLS default now derive from validator.DJIA_30; the two 10-stock
baskets move from 'first 10 of the old list' to canonical TOP_10_STOCKS
(intentional behavior change: UNH/NVDA -> AXP/DIS). Guard extended to any
Dow-ish name (DJIA_30/DJIA_SYMBOLS/SYMBOLS) across all scripts + paper.py."
```

### Task 2: Frontend `djia` preset → canonical 30 + guard

**Files:**
- Modify: `dashboard/frontend/app.js:1959-1962` (the `djia` entry of `ASSET_UNIVERSES`)
- Test (extend): `dashboard/backend/tests/test_djia30_universe.py`

**Interfaces:**
- Consumes: `EXPECTED` set and `_REPO` path constant already present in `test_djia30_universe.py` (Task 1 kept/added them).
- Produces: nothing downstream.

- [ ] **Step 1: Write the failing guard test (RED)**

Append to `dashboard/backend/tests/test_djia30_universe.py` (add `import re` next to `import ast` at the top):

```python
_APP_JS = _REPO / "dashboard" / "frontend" / "app.js"


def test_frontend_djia_preset_matches_canonical():
    src = _APP_JS.read_text(encoding="utf-8")
    m = re.search(r"djia:\s*\{[^{}]*?assets:\s*\[([^\]]*)\]", src, re.S)
    assert m, "djia preset not found in ASSET_UNIVERSES in app.js"
    assets = re.findall(r"'([A-Z.]+)'", m.group(1))
    assert len(assets) == len(set(assets)), "duplicate tickers in djia preset"
    assert set(assets) == EXPECTED
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /mnt/d/Github/agent-trading-lab && ~/atl-venv/bin/python -m pytest dashboard/backend/tests/test_djia30_universe.py::test_frontend_djia_preset_matches_canonical -q -p no:cacheprovider`
Expected: FAIL — current preset has 15 tickers incl. GE/INTC.

- [ ] **Step 3: Update the preset in `app.js`**

Replace (currently at lines 1959-1962):

```js
    djia: {
        name: 'DJIA',
        assets: ['AAPL', 'MSFT', 'JPM', 'JNJ', 'V', 'PG', 'MRK', 'DIS', 'BA', 'HD', 'KO', 'AXP', 'GE', 'IBM', 'INTC']
    },
```

with:

```js
    djia: {
        name: 'DJIA',
        // Canonical Dow-30 — must mirror backend validator.DJIA_30
        // (pinned by dashboard/backend/tests/test_djia30_universe.py).
        assets: ['AAPL', 'AMGN', 'AMZN', 'AXP', 'BA', 'CAT', 'CRM', 'CSCO', 'CVX', 'DIS',
                 'GOOGL', 'GS', 'HD', 'HON', 'IBM', 'JNJ', 'JPM', 'KO', 'MCD', 'MMM',
                 'MRK', 'MSFT', 'NKE', 'NVDA', 'PG', 'SHW', 'TRV', 'UNH', 'V', 'WMT']
    },
```

(The UI card in `app.html:866` already says "30 blue-chip companies" — no HTML change needed. `selectPreset`/`getSelectedAssets` consume `assets` generically; nothing else hardcodes the count.)

- [ ] **Step 4: Run the guard test to verify it passes**

Run: `cd /mnt/d/Github/agent-trading-lab && ~/atl-venv/bin/python -m pytest dashboard/backend/tests/test_djia30_universe.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: JS syntax check**

Run: `cd /mnt/d/Github/agent-trading-lab && node --check dashboard/frontend/app.js`
Expected: no output (exit 0). If `node` is unavailable, state so in the report and rely on the regex test.

- [ ] **Step 6: Commit**

```bash
cd /mnt/d/Github/agent-trading-lab
git add dashboard/frontend/app.js dashboard/backend/tests/test_djia30_universe.py
git commit -m "fix(frontend): djia universe preset -> canonical current Dow-30

Was a stale 15-ticker subset (incl. ex-members GE/INTC) under a card that
already promised '30 blue-chip companies'. Now mirrors validator.DJIA_30,
pinned by a textual guard test in test_djia30_universe.py."
```

---

## Out of scope (tracked elsewhere)

- Prod leaderboard baselines refresh: blocked until Render redeploys post-#91 code (prod `/api/v2/schema` still serves the OLD universe as of 2026-07-11 09:45 UTC). After redeploy: `curl "https://agentictrading.onrender.com/api/v1/leaderboard?refresh=true"` (INSERT OR REPLACE on deterministic run_ids — idempotent).
- FinSearch artifact watchlist=34 verification: background job this session.
- `docs/examples`, `backtest_custom_algo.py` (imports `DJIA_30` from `backtest_hourly_agent` — already canonical transitively), `MarketEventFeed.js` (event feed copy, not a universe): reviewed, no Dow list literals to reconcile.
