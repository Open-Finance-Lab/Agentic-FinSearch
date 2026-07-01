# XBRL Truth Layer P0/P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a reusable, plain-Python canonical truth layer (`retrieve_evidence(entity, concept, period, as_of)` over SEC `companyfacts` in DuckDB) and migrate the existing 3-ratio Validate path onto it without regressing the 3/3 demo.

**Architecture:** A new `Main/backend/truthlayer/` package with no Django/MCP deps: `contracts` (frozen dataclasses) → `registry` (concept registry generalizing `RATIO_TAG_MAP`) → `store` (DuckDB schema + fact_id hashing) → `ingest` (companyfacts JSON → rows) → `retrieve` (the `as_of`-parameterized primitive). `axioms/resolver.py` is rewritten to call `retrieve`, keeping its three public functions' signatures so `engine`/`__init__`/`sources` stay untouched. Restatements coexist by `filed` date; a read picks `max(filed) ≤ as_of`.

**Tech Stack:** Python 3.12, `uv`, `duckdb` (new dep), `requests` (existing), `pytest` (asyncio_mode=auto). Source: SEC `companyfacts` API, vendored as pruned JSON snapshots for offline reproducible tests.

**Spec:** `Docs/superpowers/specs/2026-06-26-xbrl-truth-layer-p0p1-design.md`

**Working directory for all commands:** `Main/backend/` (the Django backend root). Run tests with `uv run pytest`.

---

## File Structure

```
Main/backend/truthlayer/
  __init__.py        re-exports retrieve_evidence, retrieve_evidence_batch, Query, Evidence
  contracts.py       Evidence, Provenance, ConceptSpec*, Period, Query  (*ConceptSpec lives in registry)
  registry.py        CONCEPT_REGISTRY, RATIO_CONCEPTS, REGISTRY_VERSION, get_concept, ConceptNotFound
  store.py           DuckDB schema DDL, connect(), make_fact_id(), DB_PATH/DATA_DIR
  ingest.py          companyfacts_rows(), ingest_doc(), fetch_companyfacts(), save_snapshots(), build_from_vendored()
  retrieve.py        retrieve_evidence(), retrieve_evidence_batch(), entity_has_tag(), latest_filing()
  data/
    companyfacts/CIK0000320193.json  (AAPL)  CIK0000789019.json (MSFT)  CIK0001318605.json (TSLA)  [vendored, pruned]
    truthlayer.duckdb                [built artifact, gitignored — rebuilt from vendored JSON on first use]
Main/backend/axioms/resolver.py      REWRITTEN onto truthlayer (public surface preserved)
Main/backend/tests/
  test_truthlayer_contracts.py
  test_truthlayer_registry.py
  test_truthlayer_store.py
  test_truthlayer_ingest.py
  test_truthlayer_retrieve.py
  test_truthlayer_asof.py
  test_axiom_resolver.py             MODIFIED (xbrl_source_url expectation only)
```

---

## Task 1: Add `duckdb` dependency + scaffold the package

**Files:**
- Modify: `Main/backend/pyproject.toml` (dependencies list)
- Create: `Main/backend/truthlayer/__init__.py`
- Create: `Main/backend/.gitignore` entry (or repo root) for the built DB

- [ ] **Step 1: Add the dependency**

Run: `uv add duckdb`
Expected: `pyproject.toml` gains a `duckdb>=...` line under `[project].dependencies` and `uv.lock` updates.

- [ ] **Step 2: Create the package init (empty re-export stub for now)**

Create `Main/backend/truthlayer/__init__.py`:

```python
"""Canonical XBRL truth layer: companyfacts -> DuckDB -> as_of-parameterized reads.

Pure-Python, no Django/MCP deps, so the axiom resolver, the benchmark grader,
and the Agent Trading Lab can all import it and call it in-process.
"""
```

- [ ] **Step 3: Ignore the built DB artifact**

Append to `Main/backend/.gitignore` (create if absent):

```
truthlayer/data/truthlayer.duckdb
```

- [ ] **Step 4: Verify the import works**

Run: `uv run python -c "import truthlayer; import duckdb; print('ok', duckdb.__version__)"`
Expected: prints `ok <version>` with no ImportError.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock truthlayer/__init__.py .gitignore
git commit -m "feat(truthlayer): add duckdb dep + scaffold package"
```

---

## Task 2: `contracts.py` — frozen dataclasses

**Files:**
- Create: `Main/backend/truthlayer/contracts.py`
- Test: `Main/backend/tests/test_truthlayer_contracts.py`

- [ ] **Step 1: Write the failing test**

Create `Main/backend/tests/test_truthlayer_contracts.py`:

```python
from datetime import date
from decimal import Decimal

from truthlayer.contracts import Evidence, Provenance, Period, Query


def test_period_demo_path_and_benchmark_path():
    demo = Period(period_end=date(2023, 9, 30))
    assert demo.period_end == date(2023, 9, 30) and demo.fiscal_year is None
    bench = Period(fiscal_year=2026, fiscal_period="FY")
    assert bench.fiscal_year == 2026 and bench.period_end is None


def test_query_defaults_as_of_none():
    q = Query(entity="AAPL", concept="assets", period=Period(period_end=date(2023, 9, 30)))
    assert q.as_of is None


def test_evidence_is_frozen_and_carries_provenance():
    prov = Provenance(fact_id="abc", cik=320193, accession="x", filed=date(2023, 11, 3),
                      form="10-K", taxonomy="us-gaap", tag="Assets", fy=2023, fp="FY", frame="CY2023Q3I")
    ev = Evidence(concept="assets", value=1.0, value_exact=Decimal("1.0"), unit="USD",
                  period=Period(period_end=date(2023, 9, 30)), as_of=None, provenance=prov,
                  found=True, tags_tried=("Assets",), restated_later=False)
    assert ev.provenance.tag == "Assets"
    import dataclasses, pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.value = 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_truthlayer_contracts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'truthlayer.contracts'`

- [ ] **Step 3: Implement `contracts.py`**

Create `Main/backend/truthlayer/contracts.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Provenance:
    fact_id: str
    cik: int
    accession: str
    filed: date
    form: str
    taxonomy: str
    tag: str            # the ACTUAL us-gaap tag matched
    fy: int | None
    fp: str | None
    frame: str | None


@dataclass(frozen=True)
class Period:
    fiscal_year: int | None = None
    fiscal_period: str = "FY"          # 'FY' | 'Q1'..'Q4'
    period_end: date | None = None     # exact end-date (demo path)


@dataclass(frozen=True)
class Query:
    entity: str                        # ticker or CIK
    concept: str
    period: Period
    as_of: date | None = None


@dataclass(frozen=True)
class Evidence:
    concept: str
    value: float | None                # compute view (existing engine)
    value_exact: Decimal | None        # exact (grader / truth claim)
    unit: str | None
    period: Period
    as_of: date | None
    provenance: Provenance | None
    found: bool
    tags_tried: tuple[str, ...]
    restated_later: bool | None        # a filing after as_of changed this value; None if uncomputed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_truthlayer_contracts.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add truthlayer/contracts.py tests/test_truthlayer_contracts.py
git commit -m "feat(truthlayer): frozen Evidence/Provenance/Period/Query contracts"
```

---

## Task 3: `registry.py` — concept registry

**Files:**
- Create: `Main/backend/truthlayer/registry.py`
- Test: `Main/backend/tests/test_truthlayer_registry.py`

- [ ] **Step 1: Write the failing test**

Create `Main/backend/tests/test_truthlayer_registry.py`:

```python
import pytest

from truthlayer.registry import (
    CONCEPT_REGISTRY, RATIO_CONCEPTS, REGISTRY_VERSION, get_concept, ConceptNotFound,
)


def test_revenue_is_a_top_level_concept_shared_not_trapped_in_a_ratio():
    spec = get_concept("revenue")
    assert spec.period_type == "duration"
    assert spec.tags[0] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_instant_vs_duration_classification():
    assert get_concept("assets").period_type == "instant"
    assert get_concept("cost_of_revenue").period_type == "duration"


def test_ratios_reference_concepts_by_name():
    assert RATIO_CONCEPTS["gross_margin"] == {"revenue": "revenue", "cogs": "cost_of_revenue"}
    # every referenced concept must exist in the registry
    for mapping in RATIO_CONCEPTS.values():
        for concept in mapping.values():
            assert concept in CONCEPT_REGISTRY


def test_unknown_concept_raises():
    with pytest.raises(ConceptNotFound):
        get_concept("ebitda_magic")


def test_registry_is_versioned():
    assert REGISTRY_VERSION  # non-empty pin string
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_truthlayer_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'truthlayer.registry'`

- [ ] **Step 3: Implement `registry.py`**

Create `Main/backend/truthlayer/registry.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

REGISTRY_VERSION = "2026-06-26"


class ConceptNotFound(KeyError):
    """Raised when a concept name is not in CONCEPT_REGISTRY (a wiring bug)."""


@dataclass(frozen=True)
class ConceptSpec:
    period_type: str               # 'instant' (balance sheet) | 'duration' (income stmt)
    tags: tuple[str, ...]          # ordered us-gaap candidates; first match wins


CONCEPT_REGISTRY: dict[str, ConceptSpec] = {
    "assets":              ConceptSpec("instant",  ("Assets",)),
    "liabilities":         ConceptSpec("instant",  ("Liabilities",)),
    "equity":              ConceptSpec("instant",  (
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity")),
    "temporary_equity":    ConceptSpec("instant",  (
        "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
        "RedeemableNoncontrollingInterestEquityCarryingAmount")),
    "revenue":             ConceptSpec("duration", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet")),
    "cost_of_revenue":     ConceptSpec("duration", (
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")),
    "current_assets":      ConceptSpec("instant",  ("AssetsCurrent",)),
    "current_liabilities": ConceptSpec("instant",  ("LiabilitiesCurrent",)),
}

# Ratios reference CONCEPTS (rename map keeps the engine's input names stable):
RATIO_CONCEPTS: dict[str, dict[str, str]] = {
    "accounting_equation": {"assets": "assets", "liabilities": "liabilities",
                            "equity": "equity", "temporary_equity": "temporary_equity"},
    "gross_margin":        {"revenue": "revenue", "cogs": "cost_of_revenue"},
    "current_ratio":       {"current_assets": "current_assets",
                            "current_liabilities": "current_liabilities"},
}


def get_concept(concept: str) -> ConceptSpec:
    try:
        return CONCEPT_REGISTRY[concept]
    except KeyError as exc:
        raise ConceptNotFound(concept) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_truthlayer_registry.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add truthlayer/registry.py tests/test_truthlayer_registry.py
git commit -m "feat(truthlayer): concept registry generalizing RATIO_TAG_MAP"
```

---

## Task 4: `store.py` — DuckDB schema, connection, fact_id hashing

**Files:**
- Create: `Main/backend/truthlayer/store.py`
- Test: `Main/backend/tests/test_truthlayer_store.py`

- [ ] **Step 1: Write the failing test**

Create `Main/backend/tests/test_truthlayer_store.py`:

```python
from datetime import date

from truthlayer import store


def test_make_fact_id_is_stable_and_distinguishes_accession():
    a = store.make_fact_id(320193, "us-gaap", "Assets", "USD", None, date(2023, 9, 30), "acc-1")
    a_again = store.make_fact_id(320193, "us-gaap", "Assets", "USD", None, date(2023, 9, 30), "acc-1")
    b = store.make_fact_id(320193, "us-gaap", "Assets", "USD", None, date(2023, 9, 30), "acc-2")
    assert a == a_again            # deterministic
    assert a != b                  # a restatement (different accession) is a different fact
    assert len(a) == 40            # sha1 hex


def test_connect_creates_schema():
    con = store.connect(":memory:")
    cols = [r[0] for r in con.execute("PRAGMA table_info('facts')").fetchall()]
    assert {"fact_id", "value_exact", "filed", "period_start", "frame"} <= set(cols)
    ent_cols = [r[0] for r in con.execute("PRAGMA table_info('entities')").fetchall()]
    assert {"cik", "ticker", "name"} <= set(ent_cols)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_truthlayer_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'truthlayer.store'`

- [ ] **Step 3: Implement `store.py`**

Create `Main/backend/truthlayer/store.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "truthlayer.duckdb"

# Column order is the contract for INSERT ... VALUES in ingest.py — do not reorder.
FACT_COLUMNS = (
    "fact_id", "cik", "taxonomy", "tag", "unit", "value", "value_exact",
    "period_start", "period_end", "fy", "fp", "form", "accession", "filed", "frame",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  fact_id      TEXT PRIMARY KEY,
  cik          BIGINT,
  taxonomy     TEXT,
  tag          TEXT,
  unit         TEXT,
  value        DOUBLE,
  value_exact  DECIMAL(38,6),
  period_start DATE,
  period_end   DATE,
  fy           INTEGER,
  fp           TEXT,
  form         TEXT,
  accession    TEXT,
  filed        DATE,
  frame        TEXT
);
CREATE TABLE IF NOT EXISTS entities (
  cik    BIGINT PRIMARY KEY,
  ticker TEXT,
  name   TEXT
);
"""


def make_fact_id(cik, taxonomy, tag, unit, period_start, period_end, accession) -> str:
    """sha1 of the documented canonical tuple. See spec S2; reconcile vs the
    benchmark gold_fact_path generator BEFORE building the P4 grader."""
    raw = "|".join(str(x) for x in (cik, taxonomy, tag, unit, period_start, period_end, accession))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def connect(db_path=DB_PATH) -> "duckdb.DuckDBPyConnection":
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_SCHEMA)
    return con
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_truthlayer_store.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add truthlayer/store.py tests/test_truthlayer_store.py
git commit -m "feat(truthlayer): DuckDB schema, connection, sha1 fact_id"
```

---

## Task 5: `ingest.py` — companyfacts JSON → rows → store

**Files:**
- Create: `Main/backend/truthlayer/ingest.py`
- Test: `Main/backend/tests/test_truthlayer_ingest.py`

- [ ] **Step 1: Write the failing test (synthetic companyfacts doc — fast, offline, deterministic)**

Create `Main/backend/tests/test_truthlayer_ingest.py`:

```python
from truthlayer import store, ingest

# Minimal companyfacts shape: one instant tag with an ORIGINAL and a RESTATED entry
# for the same period (different accn/filed/val), proving restatements coexist.
SYNTHETIC = {
    "cik": 999999,
    "entityName": "Test Co",
    "facts": {
        "us-gaap": {
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        {"end": "2023-12-31", "val": 1000, "accn": "acc-1", "fy": 2023,
                         "fp": "FY", "form": "10-K", "filed": "2024-02-01", "frame": "CY2023Q4I"},
                        {"end": "2023-12-31", "val": 1100, "accn": "acc-2", "fy": 2023,
                         "fp": "FY", "form": "10-K", "filed": "2025-02-01"},
                    ]
                },
            },
            "Revenues": {
                "units": {
                    "USD": [
                        {"start": "2023-01-01", "end": "2023-12-31", "val": 500, "accn": "acc-1",
                         "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
                    ]
                }
            },
        }
    },
}


def test_companyfacts_rows_maps_instant_and_duration():
    rows = list(ingest.companyfacts_rows(SYNTHETIC))
    by_tag = {}
    for r in rows:
        by_tag.setdefault(r[3], []).append(r)   # r[3] == tag (FACT_COLUMNS order)
    assert len(by_tag["Assets"]) == 2           # original + restatement both present
    # instant fact has period_start None; duration has a start
    assets = by_tag["Assets"][0]
    rev = by_tag["Revenues"][0]
    ps_idx = store.FACT_COLUMNS.index("period_start")
    assert assets[ps_idx] is None
    assert rev[ps_idx] is not None


def test_ingest_is_idempotent():
    con = store.connect(":memory:")
    ingest.ingest_doc(con, SYNTHETIC)
    ingest.ingest_doc(con, SYNTHETIC)            # re-ingest must not duplicate
    n = con.execute("SELECT count(*) FROM facts").fetchone()[0]
    assert n == 3                                # 2 Assets + 1 Revenues
    assert con.execute("SELECT count(*) FROM entities").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_truthlayer_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'truthlayer.ingest'`

- [ ] **Step 3: Implement `ingest.py`**

Create `Main/backend/truthlayer/ingest.py`:

```python
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import requests

from truthlayer import store

CF_DIR = store.DATA_DIR / "companyfacts"
USER_AGENT = "Agentic FinSearch admin@agenticfinsearch.org"

# Demo trio. CIK is the key; the API path is zero-padded to 10 digits.
DEMO_CIKS = {"AAPL": 320193, "MSFT": 789019, "TSLA": 1318605}

# Keep vendored snapshots small: prune to the tags the registry actually uses.
from truthlayer.registry import CONCEPT_REGISTRY  # noqa: E402
_KEEP_TAGS = {t for spec in CONCEPT_REGISTRY.values() for t in spec.tags}


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def companyfacts_rows(doc: dict):
    """Yield row tuples in store.FACT_COLUMNS order for every fact in the doc."""
    cik = doc["cik"]
    for taxonomy, concepts in doc.get("facts", {}).items():
        for tag, body in concepts.items():
            for unit, entries in body.get("units", {}).items():
                for e in entries:
                    ps, pe = _d(e.get("start")), _d(e.get("end"))
                    val = e["val"]
                    yield (
                        store.make_fact_id(cik, taxonomy, tag, unit, ps, pe, e["accn"]),
                        cik, taxonomy, tag, unit, float(val), Decimal(str(val)),
                        ps, pe, e.get("fy"), e.get("fp"), e.get("form"),
                        e["accn"], _d(e.get("filed")), e.get("frame"),
                    )


def ingest_doc(con, doc: dict) -> None:
    rows = list(companyfacts_rows(doc))
    placeholders = ",".join(["?"] * len(store.FACT_COLUMNS))
    con.executemany(
        f"INSERT INTO facts VALUES ({placeholders}) ON CONFLICT (fact_id) DO NOTHING", rows
    )
    con.execute(
        "INSERT INTO entities VALUES (?, ?, ?) ON CONFLICT (cik) DO NOTHING",
        [doc["cik"], None, doc.get("entityName")],
    )


# --- snapshot building (manual, network) -----------------------------------

def fetch_companyfacts(cik: int) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _prune(doc: dict) -> dict:
    """Keep only the us-gaap tags the registry references; drop everything else
    so the committed snapshot is ~tens of KB instead of multiple MB."""
    gaap = doc.get("facts", {}).get("us-gaap", {})
    doc["facts"] = {"us-gaap": {t: gaap[t] for t in _KEEP_TAGS if t in gaap}}
    return doc


def save_snapshots() -> None:
    """One-time, requires network. Writes pruned companyfacts JSON for the demo trio."""
    CF_DIR.mkdir(parents=True, exist_ok=True)
    for ticker, cik in DEMO_CIKS.items():
        doc = _prune(fetch_companyfacts(cik))
        (CF_DIR / f"CIK{cik:010d}.json").write_text(json.dumps(doc))
        print(f"saved {ticker} CIK{cik:010d}.json")


def build_from_vendored(db_path=store.DB_PATH):
    """Build the DuckDB store from the committed snapshots (offline, deterministic)."""
    con = store.connect(db_path)
    ticker_by_cik = {cik: tk for tk, cik in DEMO_CIKS.items()}
    for path in sorted(CF_DIR.glob("CIK*.json")):
        doc = json.loads(path.read_text())
        ingest_doc(con, doc)
        con.execute("UPDATE entities SET ticker = ? WHERE cik = ?",
                    [ticker_by_cik.get(doc["cik"]), doc["cik"]])
    return con
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_truthlayer_ingest.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add truthlayer/ingest.py tests/test_truthlayer_ingest.py
git commit -m "feat(truthlayer): companyfacts ingest (idempotent, restatements coexist)"
```

---

## Task 6: `retrieve.py` — `retrieve_evidence` core (period_end path)

**Files:**
- Create: `Main/backend/truthlayer/retrieve.py`
- Test: `Main/backend/tests/test_truthlayer_retrieve.py`

- [ ] **Step 1: Write the failing test**

Create `Main/backend/tests/test_truthlayer_retrieve.py`:

```python
from datetime import date

import pytest

from truthlayer import store, ingest, retrieve
from truthlayer.contracts import Query, Period
from truthlayer.registry import ConceptNotFound

SYNTHETIC = {
    "cik": 111, "entityName": "Inst Co",
    "facts": {"us-gaap": {
        "Assets": {"units": {"USD": [
            {"end": "2023-12-31", "val": 1000, "accn": "a", "fy": 2023, "fp": "FY",
             "form": "10-K", "filed": "2024-02-01"}]}},
        # revenue: annual + quarterly ending same date — longest-duration must win
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 900, "accn": "a", "fy": 2023,
             "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
            {"start": "2023-10-01", "end": "2023-12-31", "val": 250, "accn": "a", "fy": 2023,
             "fp": "Q4", "form": "10-K", "filed": "2024-02-01"}]}},
    }},
}


@pytest.fixture()
def con():
    c = store.connect(":memory:")
    ingest.ingest_doc(c, SYNTHETIC)
    c.execute("UPDATE entities SET ticker = 'INST' WHERE cik = 111")
    return c


def test_instant_concept_resolves_with_provenance(con):
    ev = retrieve.retrieve_evidence(
        Query("INST", "assets", Period(period_end=date(2023, 12, 31))), con=con)
    assert ev.found and ev.value == 1000.0
    assert ev.provenance.tag == "Assets" and ev.provenance.accession == "a"
    assert ev.unit == "USD"


def test_duration_concept_prefers_longest_duration(con):
    ev = retrieve.retrieve_evidence(
        Query("INST", "revenue", Period(period_end=date(2023, 12, 31))), con=con)
    assert ev.value == 900.0          # annual, not the 250 quarter


def test_missing_metric_returns_found_false_with_tags_tried(con):
    ev = retrieve.retrieve_evidence(
        Query("INST", "current_assets", Period(period_end=date(2023, 12, 31))), con=con)
    assert ev.found is False and ev.value is None
    assert ev.tags_tried == ("AssetsCurrent",)


def test_unknown_concept_raises(con):
    with pytest.raises(ConceptNotFound):
        retrieve.retrieve_evidence(
            Query("INST", "ebitda_magic", Period(period_end=date(2023, 12, 31))), con=con)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_truthlayer_retrieve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'truthlayer.retrieve'`

- [ ] **Step 3: Implement `retrieve.py`** (as_of + restated_later wiring included; covered by Task 7 tests)

Create `Main/backend/truthlayer/retrieve.py`:

```python
from __future__ import annotations

from collections.abc import Sequence

from truthlayer import registry, store
from truthlayer.contracts import Evidence, Period, Provenance, Query

_con = None


def _conn():
    """Lazily open the persistent store, building it from vendored snapshots
    on first use so a fresh checkout works offline."""
    global _con
    if _con is None:
        if not store.DB_PATH.exists():
            from truthlayer import ingest
            ingest.build_from_vendored()
        _con = store.connect(store.DB_PATH)
    return _con


def _resolve_cik(con, entity: str) -> int | None:
    if str(entity).isdigit():
        return int(entity)
    row = con.execute(
        "SELECT cik FROM entities WHERE upper(ticker) = upper(?)", [entity]).fetchone()
    return row[0] if row else None


def _select(con, cik: int, tag: str, period_type: str, period: Period, as_of):
    where = ["cik = ?", "taxonomy = 'us-gaap'", "tag = ?"]
    params: list = [cik, tag]
    where.append("period_start IS NULL" if period_type == "instant" else "period_start IS NOT NULL")
    if period.period_end is not None:                      # demo path
        where.append("period_end = ?"); params.append(period.period_end)
    else:                                                  # benchmark path (later)
        where.append("fy = ? AND fp = ?"); params += [period.fiscal_year, period.fiscal_period]
    if as_of is not None:
        where.append("filed <= ?"); params.append(as_of)
    order = "(period_end - period_start) DESC, filed DESC" if period_type == "duration" else "filed DESC"
    sql = f"SELECT * FROM facts WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 1"
    res = con.execute(sql, params)
    row = res.fetchone()
    if row is None:
        return None
    return dict(zip([c[0] for c in res.description], row))


def _restated_later(con, r: dict, as_of) -> bool:
    if as_of is None:
        return False
    q = ("SELECT 1 FROM facts WHERE cik = ? AND taxonomy = 'us-gaap' AND tag = ? "
         "AND period_end IS NOT DISTINCT FROM ? AND period_start IS NOT DISTINCT FROM ? "
         "AND filed > ? AND value <> ? LIMIT 1")
    return con.execute(
        q, [r["cik"], r["tag"], r["period_end"], r["period_start"], r["filed"], r["value"]]
    ).fetchone() is not None


def _build(q: Query, spec, r: dict, con) -> Evidence:
    prov = Provenance(r["fact_id"], r["cik"], r["accession"], r["filed"], r["form"],
                      r["taxonomy"], r["tag"], r["fy"], r["fp"], r["frame"])
    return Evidence(
        concept=q.concept,
        value=float(r["value"]) if r["value"] is not None else None,
        value_exact=r["value_exact"], unit=r["unit"], period=q.period, as_of=q.as_of,
        provenance=prov, found=True, tags_tried=spec.tags,
        restated_later=_restated_later(con, r, q.as_of),
    )


def _miss(q: Query, spec) -> Evidence:
    return Evidence(q.concept, None, None, None, q.period, q.as_of, None, False, spec.tags, None)


def retrieve_evidence(q: Query, con=None) -> Evidence:
    con = con or _conn()
    spec = registry.get_concept(q.concept)        # raises ConceptNotFound
    cik = _resolve_cik(con, q.entity)
    if cik is None:
        return _miss(q, spec)
    for tag in spec.tags:                          # first match wins
        r = _select(con, cik, tag, spec.period_type, q.period, q.as_of)
        if r is not None:
            return _build(q, spec, r, con)
    return _miss(q, spec)


def retrieve_evidence_batch(qs: Sequence[Query], con=None) -> list[Evidence]:
    con = con or _conn()
    return [retrieve_evidence(q, con=con) for q in qs]


def entity_has_tag(entity: str, tag: str, con=None) -> bool:
    con = con or _conn()
    cik = _resolve_cik(con, entity)
    if cik is None:
        return False
    return con.execute(
        "SELECT 1 FROM facts WHERE cik = ? AND tag = ? LIMIT 1", [cik, tag]).fetchone() is not None


def latest_filing(entity: str, form: str = "10-K", con=None) -> str | None:
    """Representative source for the Validate card: the entity's most recently
    filed accession of `form`."""
    con = con or _conn()
    cik = _resolve_cik(con, entity)
    if cik is None:
        return None
    row = con.execute(
        "SELECT accession FROM facts WHERE cik = ? AND form = ? ORDER BY filed DESC LIMIT 1",
        [cik, form]).fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_truthlayer_retrieve.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add truthlayer/retrieve.py tests/test_truthlayer_retrieve.py
git commit -m "feat(truthlayer): retrieve_evidence core (period_end path, first-tag-wins, longest-duration)"
```

---

## Task 7: `as_of` restatement semantics (the invariant proof)

**Files:**
- Modify: (none — code already in `retrieve.py` from Task 6)
- Test: `Main/backend/tests/test_truthlayer_asof.py`

- [ ] **Step 1: Write the failing test (a 2-version restatement fixture)**

Create `Main/backend/tests/test_truthlayer_asof.py`:

```python
from datetime import date

import pytest

from truthlayer import store, ingest, retrieve
from truthlayer.contracts import Query, Period

# Same (cik, tag, period) reported twice: original val=1000 filed 2024-02-01,
# restated val=1100 filed 2025-02-01.
RESTATED = {
    "cik": 222, "entityName": "Restate Co",
    "facts": {"us-gaap": {"Assets": {"units": {"USD": [
        {"end": "2023-12-31", "val": 1000, "accn": "orig", "fy": 2023, "fp": "FY",
         "form": "10-K", "filed": "2024-02-01"},
        {"end": "2023-12-31", "val": 1100, "accn": "restate", "fy": 2023, "fp": "FY",
         "form": "10-K", "filed": "2025-02-01"},
    ]}}}},
}


@pytest.fixture()
def con():
    c = store.connect(":memory:")
    ingest.ingest_doc(c, RESTATED)
    c.execute("UPDATE entities SET ticker = 'RST' WHERE cik = 222")
    return c


def _q(as_of):
    return Query("RST", "assets", Period(period_end=date(2023, 12, 31)), as_of=as_of)


def test_as_of_before_restatement_sees_original(con):
    ev = retrieve.retrieve_evidence(_q(date(2024, 6, 1)), con=con)
    assert ev.value == 1000.0
    assert ev.restated_later is True            # a later filing changed it (look-ahead-sensitive)


def test_as_of_after_restatement_sees_restated(con):
    ev = retrieve.retrieve_evidence(_q(date(2025, 6, 1)), con=con)
    assert ev.value == 1100.0
    assert ev.restated_later is False


def test_as_of_none_returns_latest(con):
    ev = retrieve.retrieve_evidence(_q(None), con=con)
    assert ev.value == 1100.0                   # latest known
    assert ev.restated_later is False
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_truthlayer_asof.py -v`
Expected: PASS (3 passed) — the as_of logic shipped in Task 6. If any fail, fix `_select`/`_restated_later` in `retrieve.py` until green. (This task exists to *prove* the invariant, so the test is the deliverable.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_truthlayer_asof.py
git commit -m "test(truthlayer): prove as_of moves value across a restatement"
```

---

## Task 8: Vendor real snapshots + build DB + real-data retrieve test

**Files:**
- Create: `Main/backend/truthlayer/data/companyfacts/CIK0000320193.json`, `CIK0000789019.json`, `CIK0001318605.json` (generated)
- Test: `Main/backend/tests/test_truthlayer_realdata.py`

- [ ] **Step 1: Fetch the vendored snapshots (one-time, requires network)**

Run: `uv run python -c "from truthlayer import ingest; ingest.save_snapshots()"`
Expected: prints `saved AAPL ...`, `saved MSFT ...`, `saved TSLA ...`; three pruned JSON files appear under `truthlayer/data/companyfacts/` (each tens of KB).

If SEC returns 403, confirm the `User-Agent` header in `ingest.USER_AGENT` is a real contact string and retry (SEC blocks empty/default agents).

- [ ] **Step 2: Write the real-data test**

Create `Main/backend/tests/test_truthlayer_realdata.py`:

```python
from datetime import date

import pytest

from truthlayer import store, ingest, retrieve
from truthlayer.contracts import Query, Period


@pytest.fixture(scope="module")
def con():
    c = store.connect(":memory:")
    import json
    for path in sorted(ingest.CF_DIR.glob("CIK*.json")):
        doc = json.loads(path.read_text())
        ingest.ingest_doc(c, doc)
    ticker_by_cik = {cik: tk for tk, cik in ingest.DEMO_CIKS.items()}
    for cik, tk in ticker_by_cik.items():
        c.execute("UPDATE entities SET ticker = ? WHERE cik = ?", [tk, cik])
    return c


def _val(con, ticker, concept, period_end):
    ev = retrieve.retrieve_evidence(
        Query(ticker, concept, Period(period_end=date.fromisoformat(period_end))), con=con)
    return ev.value


def test_aapl_balance_sheet(con):
    assert _val(con, "AAPL", "assets", "2023-09-30") == pytest.approx(352583e6, rel=1e-6)
    assert _val(con, "AAPL", "liabilities", "2023-09-30") == pytest.approx(290437e6, rel=1e-6)
    assert _val(con, "AAPL", "equity", "2023-09-30") == pytest.approx(62146e6, rel=1e-6)


def test_aapl_gross_margin_inputs(con):
    assert _val(con, "AAPL", "revenue", "2023-09-30") == pytest.approx(383285e6, rel=1e-6)
    assert _val(con, "AAPL", "cost_of_revenue", "2023-09-30") == pytest.approx(214137e6, rel=1e-6)


def test_tsla_temporary_equity_resolves(con):
    assert _val(con, "TSLA", "temporary_equity", "2023-12-31") == pytest.approx(242e6, rel=1e-6)
```

> **Restatement note:** these assert `as_of=None` (latest known). Large-cap balance-sheet aggregates are rarely restated, so the latest companyfacts value should equal the as-originally-reported figure above. If a value differs, that means companyfacts carries a later restatement — update the expected number to the companyfacts figure (the more-recently-filed truth) and note it in the commit message; do not pin `as_of` to force the old value.

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_truthlayer_realdata.py -v`
Expected: PASS (3 passed)

- [ ] **Step 4: Commit (vendored snapshots + test)**

```bash
git add truthlayer/data/companyfacts/*.json tests/test_truthlayer_realdata.py
git commit -m "feat(truthlayer): vendor pruned companyfacts snapshots + real-data retrieve test"
```

---

## Task 9: Migrate `axioms/resolver.py` onto the truth layer

**Files:**
- Modify (rewrite): `Main/backend/axioms/resolver.py`
- Modify: `Main/backend/tests/test_axiom_resolver.py` (only the `xbrl_source_url` expectation)

- [ ] **Step 1: Rewrite `resolver.py` preserving the three public signatures**

Replace the entire contents of `Main/backend/axioms/resolver.py` with:

```python
"""Resolve (ratio, ticker, period) -> XBRL-grounded numerical inputs.

Thin adapter over the canonical truth layer (`truthlayer`). Domain knowledge
(which us-gaap tags back each logical input) now lives in
`truthlayer.registry.CONCEPT_REGISTRY`; ratios reference concepts via
`RATIO_CONCEPTS`. The three public functions keep their original signatures so
`axioms.engine`, `axioms.__init__`, and `axioms.sources` are unchanged.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, Optional

from truthlayer import retrieve as tl
from truthlayer.contracts import Period, Query
from truthlayer.registry import RATIO_CONCEPTS

logger = logging.getLogger(__name__)

# Ratios that require a classified balance sheet (current vs non-current split).
# Financial-sector filers (banks, insurance, REITs) use unclassified balance sheets.
_REQUIRES_CLASSIFIED_BS = {"current_ratio"}


def _period(period: str) -> Period:
    return Period(period_end=date.fromisoformat(period)) if period else Period()


def fetch_ground_truth(ratio: str, ticker: str, period: str) -> Dict[str, Optional[float]]:
    """Return the resolved {input_name: value} dict for a ratio at (ticker, period).

    Values may be None if no tag was found; the engine's check_* functions handle
    None as SKIPPED. Uses as_of=None (latest known) — the Validate path is not
    restatement-sensitive.
    """
    if ratio not in RATIO_CONCEPTS:
        logger.warning("Unknown ratio: %s", ratio)
        return {}
    p = _period(period)
    out: Dict[str, Optional[float]] = {}
    for input_name, concept in RATIO_CONCEPTS[ratio].items():
        ev = tl.retrieve_evidence(Query(ticker, concept, p))
        out[input_name] = ev.value
    return out


def check_applicability(ratio: str, ticker: str) -> Optional[Dict[str, str]]:
    """Return a NOT_APPLICABLE reason dict if `ratio` does not apply to this
    filer's reporting structure; else None. Structural (tag-presence), not SIC."""
    if ratio not in _REQUIRES_CLASSIFIED_BS:
        return None
    if not tl.entity_has_tag(ticker, "AssetsCurrent"):
        return {
            "ratio": ratio,
            "reason": (
                f"{ticker.upper()} uses an unclassified balance sheet (typical for "
                "banks, insurance, and REITs). The current ratio is not defined "
                "for this reporting structure."
            ),
        }
    return None


def xbrl_source_url(ticker: str) -> Optional[str]:
    """Representative provenance for the Validate source card: the ticker's most
    recently filed 10-K accession (replaces the old local-filing path)."""
    return tl.latest_filing(ticker, form="10-K")
```

- [ ] **Step 2: Update the `xbrl_source_url` expectation in the resolver test**

In `Main/backend/tests/test_axiom_resolver.py`, find the test(s) that assert on `xbrl_source_url` (search for `xbrl_source_url`). Replace any assertion that expects a local file path (e.g. `mcp_server/xbrl/filings/aapl-...`) with one that expects a non-empty SEC accession string:

```python
def test_xbrl_source_url_returns_accession():
    src = xbrl_source_url("AAPL")
    assert src and "-" in src        # e.g. '0000320193-23-000106'
```

Leave the `fetch_ground_truth` and `check_applicability` assertions unchanged — they are the acceptance oracle and must still pass against the new layer.

- [ ] **Step 3: Run the resolver test (the oracle)**

Run: `uv run pytest tests/test_axiom_resolver.py -v`
Expected: PASS — all `fetch_ground_truth` cases (AAPL/MSFT/TSLA accounting_equation, gross_margin, current_ratio) green, plus the updated `xbrl_source_url` test.

If a `fetch_ground_truth` value mismatches, it is a real restatement difference — apply the same rule as Task 8's note (update the expected to the companyfacts latest value, record it in the commit message). If `check_applicability` regresses, confirm `AssetsCurrent` is in the vendored snapshot for that ticker.

- [ ] **Step 4: Commit**

```bash
git add axioms/resolver.py tests/test_axiom_resolver.py
git commit -m "refactor(axioms): migrate resolver onto truthlayer (RATIO_TAG_MAP -> concept registry)"
```

---

## Task 10: Full axiom-suite regression (acceptance gate)

**Files:**
- Modify: (only if a test breaks)

- [ ] **Step 1: Run the full axiom + truthlayer suite**

Run: `uv run pytest tests/test_axioms.py tests/test_axiom_resolver.py tests/test_axiom_integration.py tests/test_axiom_views.py tests/test_axiom_wrapper.py tests/test_truthlayer_*.py -v`
Expected: PASS — the 30 existing axiom tests + all truthlayer tests green. This is the definition of done: **the 3/3 Validate demo is served by the new layer, the as_of invariant is proven, every found fact carries provenance, and ingest is idempotent.**

- [ ] **Step 2: Investigate any failure with systematic-debugging**

If anything fails, do NOT loosen assertions blindly. Most likely causes:
- A demo value differs → restatement (apply the Task 8 note rule).
- `check_applicability` import or DB-build path issue → confirm `truthlayer/data/companyfacts/*.json` are committed and `_conn()` builds the DB.
- `mcp_server` import shadowing in tests → see `tests/conftest.py`; the truthlayer package has no such dependency, so failures here are pre-existing, not introduced.

- [ ] **Step 3: Final commit (if any fixes were made)**

```bash
git add -A
git commit -m "test(truthlayer): full axiom-suite regression green via the new truth layer"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- §3 package layout → Tasks 1–6 create `contracts/registry/store/ingest/retrieve`.
- §4 DuckDB schema (DECIMAL value_exact, frame, restatement coexistence) → Task 4 + Task 5 idempotency test.
- §5 frozen contracts → Task 2.
- §6 concept registry + RATIO_CONCEPTS → Task 3.
- §7 as_of query semantics (first-tag-wins, longest-duration, instant/duration, filed≤as_of) → Tasks 6–7.
- §8 S1 exact value → Task 4 (DECIMAL) + Evidence.value_exact (Task 2). S2 fact_id recipe → Task 4 `make_fact_id` + docstring checkpoint. S3 batch seam → Task 6 `retrieve_evidence_batch`. S4 cik-keyed entities → Task 4 schema + Task 6 `_resolve_cik`.
- §9 resolver migration (public surface preserved) → Task 9.
- §10 error handling (ConceptNotFound; found=False/tags_tried; entity not ingested) → Tasks 6, 9.
- §11 testing/acceptance → Tasks 7, 8, 10.
- §12 deferred items → not built (correct). §13 checkpoint → recorded in `make_fact_id` docstring.

**Placeholder scan:** no TBD/TODO; every code step shows full code; every test step shows the command + expected result.

**Type consistency:** `Period(period_end=…)` used identically in Tasks 6–9; `Query(entity, concept, period, as_of)` consistent; `Evidence`/`Provenance` field names match Task 2 across `_build`; `store.FACT_COLUMNS` order matches the `companyfacts_rows` tuple order (Task 5) and the INSERT placeholder count (Task 5). `retrieve_evidence(q, con=…)` keyword consistent across tests and resolver.

**Note for the implementer:** `retrieve.py` carries `as_of`/`restated_later` from Task 6 even though Task 7 supplies its tests — this avoids splitting one cohesive function across two tasks. Tasks 6 and 7 must both be green before Task 9.
