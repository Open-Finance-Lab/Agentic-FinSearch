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

# value_exact scale: spec S1 wrote DECIMAL(38,6), but its stated rationale is
# "a layer named *truth* must not lose precision" — and DuckDB DECIMAL silently
# *rounds* anything past its scale (verified: Decimal('1.2345678') -> 1.234568 at
# scale 6). Whole-dollar USD aggregates (every current concept) are exact at any
# scale, but the unit column already anticipates 'USD/shares'/EPS, where >6 dp
# can occur. Widened scale 6 -> 18 to keep per-share facts byte-exact; 38-18 = 20
# integer digits still dwarf any USD aggregate. The store is a gitignored artifact
# rebuilt from vendored JSON, so this scale can change anytime via re-ingest.
# NOTE: deliberate deviation from spec S1's literal (38,6) — serves S1's intent.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  fact_id      TEXT PRIMARY KEY,
  cik          BIGINT,
  taxonomy     TEXT,
  tag          TEXT,
  unit         TEXT,
  value        DOUBLE,
  value_exact  DECIMAL(38,18),
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


def make_fact_id(cik, tag, period_start, period_end, unit, accession, dim_hash="") -> str:
    """sha1 of the canonical fact tuple — the cross-system JOIN KEY (spec S2/S13).

    RECONCILED 2026-06-26 against the benchmark generator's gold_fact_path. The
    recipe was recovered empirically (the generator source is not checked in) by
    reproducing two real gold hashes from cases_v1_final.json against their SEC
    companyfacts source fields, and is pinned by
    tests/test_truthlayer_store.py::test_make_fact_id_matches_benchmark_gold_path.

    Canonical tuple order (== the generator's):
        (cik, concept, period_start, period_end, unit, accession, dim_hash)
    where `tag` here IS the generator's `concept` (the raw us-gaap tag, e.g.
    'Revenues'). Earlier prose was wrong twice — the contributor guide omitted
    period_start and the cases guide added value; the empirical match is of record.

    Encoding couplings (verified byte-for-byte against the gold hashes):
      - Delimiter is a literal '|' with NO escaping; unreachable with SEC data
        (PascalCase tags, USD/shares units, digit-dash accessions never contain '|').
      - None serializes via str() to the literal 'None' (period_start is None for
        instant/balance-sheet facts; real dates stringify to ISO).
      - dim_hash is '' for consolidated companyfacts facts (no segment/member
        dimensions), producing a trailing '|'. It is a real parameter, not a
        constant, so the deferred raw-XBRL dimensional ingest path can populate it
        without changing the recipe (and without re-hashing the consolidated facts).
    """
    raw = "|".join(str(x) for x in (cik, tag, period_start, period_end, unit, accession, dim_hash))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def connect(db_path=DB_PATH, read_only=False) -> "duckdb.DuckDBPyConnection":
    """Open the store. ``read_only=True`` is the request-path mode: DuckDB's
    read-write lock is exclusive across processes (a second gunicorn worker can't
    open an RW-locked file even read-only), whereas any number of processes — and
    threads, each with its own connection — may open the same file read-only. The
    file must already exist in that mode and ``_SCHEMA`` (a write/DDL) is skipped;
    callers go through ingest/build for the one-time write."""
    if db_path != ":memory:" and not read_only:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=read_only)
    if not read_only:
        con.execute(_SCHEMA)
    return con
