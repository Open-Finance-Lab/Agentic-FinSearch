from datetime import date
from decimal import Decimal

import pytest

from truthlayer import store


@pytest.fixture
def built_store_db(tmp_path):
    """A freshly built, checkpointed, closed store; yields its on-disk path."""
    from truthlayer import ingest
    db = tmp_path / "t.duckdb"
    con = ingest.build_from_vendored(db)
    con.execute("CHECKPOINT")
    con.close()
    return db


def test_make_fact_id_is_stable_and_distinguishes_accession():
    # Signature: (cik, concept, period_start, period_end, unit, accession, *, dim_hash='')
    a = store.make_fact_id(320193, "Assets", None, date(2023, 9, 30), "USD", "acc-1")
    a_again = store.make_fact_id(320193, "Assets", None, date(2023, 9, 30), "USD", "acc-1")
    b = store.make_fact_id(320193, "Assets", None, date(2023, 9, 30), "USD", "acc-2")
    assert a == a_again            # deterministic
    assert a != b                  # a restatement (different accession) is a different fact
    assert len(a) == 40            # sha1 hex


def test_connect_creates_schema():
    con = store.connect(":memory:")
    # DuckDB PRAGMA table_info rows are (cid, name, type, ...) — name is index 1.
    cols = [r[1] for r in con.execute("PRAGMA table_info('facts')").fetchall()]
    assert {"fact_id", "value_exact", "filed", "period_start", "frame"} <= set(cols)
    ent_cols = [r[1] for r in con.execute("PRAGMA table_info('entities')").fetchall()]
    assert {"cik", "ticker", "name"} <= set(ent_cols)
    meta_cols = [r[1] for r in con.execute("PRAGMA table_info('meta')").fetchall()]
    assert {"key", "value"} <= set(meta_cols)


def test_dim_hash_is_keyword_only():
    # Keyword-only so it can't be supplied by accident in the wrong positional slot of
    # a 7-field, all-stringified JOIN KEY. A 7th positional arg must raise, not hash.
    with pytest.raises(TypeError):
        store.make_fact_id(320193, "Assets", None, date(2023, 9, 30), "USD", "acc-1", "x")
    # Supplied by keyword it participates in the hash (a real param, not a constant).
    base = store.make_fact_id(320193, "Assets", None, date(2023, 9, 30), "USD", "acc-1")
    dimmed = store.make_fact_id(320193, "Assets", None, date(2023, 9, 30), "USD", "acc-1",
                                dim_hash="seg=US")
    assert base != dimmed


def test_value_exact_preserves_more_than_six_decimals():
    # value_exact is the exact truth claim; DuckDB DECIMAL silently rounds past its
    # scale, so scale 6 (spec S1's literal) would lose a >6-dp per-share fact. The
    # schema uses scale 18 — prove an 18-dp Decimal round-trips byte-exact.
    con = store.connect(":memory:")
    v = Decimal("0.123456789012345678")  # 18 fractional digits
    con.execute("INSERT INTO facts (fact_id, value_exact) VALUES (?, ?)", ["f1", v])
    got = con.execute("SELECT value_exact FROM facts WHERE fact_id = 'f1'").fetchone()[0]
    assert got == v


def test_make_fact_id_matches_benchmark_gold_path():
    # S2/S13 reconciliation: make_fact_id is a CROSS-SYSTEM JOIN KEY — it must
    # reproduce the benchmark's gold_fact_path sha1s byte-for-byte, or Track-R
    # facts-reached grading can never join our retrieved fact to the gold fact.
    #
    # These two anchors are REAL gold hashes lifted from cases_v1_final.json, with
    # the source fact fields recovered from SEC companyfacts. The recipe is
    #   sha1("|".join(str(x) for x in
    #        (cik, concept, period_start, period_end, unit, accession, dim_hash)))
    # concept = the raw us-gaap tag; period_start=None (instant facts) -> str() -> 'None';
    # dim_hash = '' for consolidated companyfacts facts (no segment/member dimensions).
    #
    # NVIDIA FY2026 Revenues (duration) — cases_v1_final.json[0].gold_fact_path:
    assert store.make_fact_id(
        1045810, "Revenues", date(2025, 1, 27), date(2026, 1, 25), "USD",
        "0001045810-26-000021",
    ) == "d7a34159f863a4bbbbe3b092a3f9611070cfe5ad"
    # Meta FY2025 Assets (instant; period_start is None) — same file:
    assert store.make_fact_id(
        1326801, "Assets", None, date(2025, 12, 31), "USD",
        "0001628280-26-003942",
    ) == "293b650557be02293fc40c70526e46d6b9096ee2"


def test_make_fact_id_golden():
    # Pins the canonical sha1 recipe (spec S2/S13, reconciled against the benchmark
    # generator) so it cannot drift silently. If the recipe deliberately changes,
    # update this hash on purpose — but it must stay consistent with the real
    # gold_fact_path anchors in test_make_fact_id_matches_benchmark_gold_path.
    fid = store.make_fact_id(
        1045810, "Revenues", date(2025, 1, 27), date(2026, 1, 25), "USD",
        "0001045810-26-000021")
    assert fid == "d7a34159f863a4bbbbe3b092a3f9611070cfe5ad"


def test_built_store_fact_ids_match_recipe(tmp_path):
    # End-to-end ingest->store pin: EVERY stored fact_id must equal make_fact_id
    # recomputed from that same row's fields. Without this, a store built under an old
    # recipe would pass every other test (none assert on a STORED fact_id) while
    # serving ids that never join to the benchmark gold. Recompute over the WHOLE table
    # (no LIMIT) — a few thousand rows hash in milliseconds — so a recipe desync scoped
    # to a single entity or tag can't slip through an unordered sample.
    from truthlayer import ingest
    con = ingest.build_from_vendored(tmp_path / "t.duckdb")
    try:
        rows = con.execute(
            "SELECT fact_id, cik, tag, period_start, period_end, unit, accession "
            "FROM facts"
        ).fetchall()
        assert rows, "vendored snapshots produced no facts"
        mismatched = [
            fid for fid, cik, tag, ps, pe, unit, accn in rows
            if fid != store.make_fact_id(cik, tag, ps, pe, unit, accn)
        ]
        assert not mismatched, f"{len(mismatched)}/{len(rows)} stored fact_ids != recipe"
    finally:
        con.close()


def test_built_store_stamps_versions(built_store_db):
    # The build records its recipe+registry versions into `meta` so staleness is
    # detectable; read_meta must round-trip exactly what build_versions() declared.
    assert store.read_meta(built_store_db) == store.build_versions()


def test_read_meta_treats_missing_or_legacy_store_as_stale(tmp_path):
    # Missing file -> {}. A store predating the meta table -> {} (so it reads as stale
    # and is rebuilt, never trusted) instead of raising.
    assert store.read_meta(tmp_path / "nope.duckdb") == {}
    legacy = tmp_path / "legacy.duckdb"
    con = store.connect(legacy)
    con.execute("DROP TABLE meta")          # simulate a store built before the meta table
    con.execute("CHECKPOINT")
    con.close()
    assert store.read_meta(legacy) == {}


def test_store_is_current_detects_version_drift(built_store_db, monkeypatch):
    # The payoff of the meta stamp: a recipe/registry bump must invalidate a store
    # built under the old version so _ensure_built rebuilds it.
    from truthlayer import retrieve
    monkeypatch.setattr(store, "DB_PATH", built_store_db)
    assert retrieve._store_is_current() is True
    monkeypatch.setattr(store, "FACT_ID_RECIPE_VERSION", "0000-00-00")  # simulate a bump
    assert retrieve._store_is_current() is False
