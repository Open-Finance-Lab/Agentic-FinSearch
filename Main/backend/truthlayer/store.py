from __future__ import annotations

import hashlib
import os
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).resolve().parent / "data"
# The built store is a writable, regenerable artifact; its path is env-overridable so
# production can place it on the mounted /app/runtime volume (persisted across restarts)
# while dev/tests/offline checkouts default to the in-package data/ dir. DATA_DIR itself
# stays in-package: ingest reads the committed companyfacts/ snapshots from it (the
# read-only build *source*), which must not move onto the initially-empty volume.
DB_PATH = Path(os.environ.get("TRUTHLAYER_DB_PATH", DATA_DIR / "truthlayer.duckdb"))

# Bump whenever make_fact_id's serialization changes. A built store bakes its
# fact_ids at ingest time, so retrieve._store_is_current() rebuilds any store stamped
# with a different recipe version rather than silently serving stale ids that no
# longer join to the benchmark gold. See build_versions() / write_meta() / read_meta().
FACT_ID_RECIPE_VERSION = "2026-06-26"

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
-- Build provenance: the code versions whose recipe/registry produced this store's
-- rows. read_meta() compares these against the running code so a store built before
-- a recipe or registry bump is detected as stale and rebuilt (not trusted).
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""


def make_fact_id(cik, concept, period_start, period_end, unit, accession, *, dim_hash="") -> str:
    """sha1 of the canonical fact tuple — the cross-system JOIN KEY (spec S2/S13).

    RECONCILED 2026-06-26 against the benchmark generator's gold_fact_path (tracked by
    FACT_ID_RECIPE_VERSION). The recipe was recovered empirically (the generator source
    is not checked in) by reproducing real gold hashes from cases_v1_final.json against
    their SEC companyfacts source fields, and is pinned by
    tests/test_truthlayer_store.py::test_make_fact_id_matches_benchmark_gold_path.

    Canonical tuple order (== the generator's, and the positional arg order here):
        (cik, concept, period_start, period_end, unit, accession, dim_hash)
    `concept` IS the raw us-gaap tag the fact is reported under (e.g. 'Revenues') —
    named to match the generator, not our `tag` column. The six canonical fields are
    POSITIONAL on purpose: the call order is the documented recipe and the golden tests
    pin it. `dim_hash` is KEYWORD-ONLY so it can never be supplied by accident in the
    wrong slot — it defaults to '' (the only value consolidated companyfacts needs).

    This is intentionally stringly-typed (each field goes through str()): callers may
    pass period dates as date objects OR ISO strings — the _tagrecover tooling does the
    latter — and None for an instant fact's period_start. So do NOT add isinstance
    guards here; the golden hash tests are the guard for the recipe.

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
    raw = "|".join(str(x) for x in (cik, concept, period_start, period_end, unit, accession, dim_hash))
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


def build_versions() -> dict[str, str]:
    """The code versions a freshly built store is stamped with (into the `meta` table).
    A persisted store is valid ONLY for these exact values: fact_id is recipe-baked and
    tag coverage is registry-baked, both at ingest time. retrieve._store_is_current()
    compares a store's stamp against this and rebuilds on any drift, so a recipe or
    registry bump can never silently serve a stale store."""
    from truthlayer.registry import REGISTRY_VERSION  # lazy: keep store import-cycle-free
    return {
        "fact_id_recipe_version": FACT_ID_RECIPE_VERSION,
        "registry_version": REGISTRY_VERSION,
    }


def write_meta(con, items: dict) -> None:
    """Upsert key/value strings into the store's `meta` table (idempotent)."""
    con.executemany(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        [(k, v) for k, v in items.items()],
    )


def read_meta(db_path=DB_PATH) -> dict:
    """Return {key: value} from a built store's `meta` table. Returns {} for ANY
    failure — store missing, unreadable, or predating the meta table — so a legacy or
    corrupt store reads as stale and gets rebuilt rather than trusted."""
    con = None
    try:
        con = connect(db_path, read_only=True)
        return dict(con.execute("SELECT key, value FROM meta").fetchall())
    except duckdb.Error:
        return {}
    finally:
        if con is not None:
            con.close()
