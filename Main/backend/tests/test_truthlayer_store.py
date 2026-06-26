from datetime import date
from decimal import Decimal

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


def test_make_fact_id_golden():
    # Pins the canonical sha1 recipe (spec S2) so it cannot drift silently and the
    # teammate's gold_fact_path generator can diff against a known value. If the S13
    # reconciliation deliberately changes the recipe, update this hash on purpose.
    fid = store.make_fact_id(
        320193, "us-gaap", "Assets", "USD", None, date(2023, 9, 30), "0000320193-23-000106")
    assert fid == "8f4f87c831b284fcb30381590a01bece0983ec4b"
