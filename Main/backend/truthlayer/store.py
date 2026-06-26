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
