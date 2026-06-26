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
    # DuckDB PRAGMA table_info rows are (cid, name, type, ...) — name is index 1.
    cols = [r[1] for r in con.execute("PRAGMA table_info('facts')").fetchall()]
    assert {"fact_id", "value_exact", "filed", "period_start", "frame"} <= set(cols)
    ent_cols = [r[1] for r in con.execute("PRAGMA table_info('entities')").fetchall()]
    assert {"cik", "ticker", "name"} <= set(ent_cols)
