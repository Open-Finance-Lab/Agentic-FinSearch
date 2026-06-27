from datetime import date
from decimal import Decimal

from truthlayer import store


def test_make_fact_id_is_stable_and_distinguishes_accession():
    # Signature: (cik, tag, period_start, period_end, unit, accession, dim_hash='')
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
