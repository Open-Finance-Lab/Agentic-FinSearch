"""Connection-model regression tests for the production read path.

The truth layer is served by gunicorn with multiple gthread threads AND multiple
forked worker processes. A single shared DuckDB connection is not thread-safe
(interleaved .execute() clobbers cursor state -> KeyError or, worse, another
query's row returned as "truth"), and a read-write handle locks every other
worker process out of the file. The fix: build once, then one read-only
connection per thread. These tests pin that contract so it can't regress.
"""
import threading
from datetime import date

import duckdb
import pytest

from truthlayer import retrieve
from truthlayer.contracts import Query, Period

_AAPL_REV_2023 = 383285e6  # vendored ground truth, also pinned in the realdata/resolver suites


def test_request_path_connection_is_read_only():
    con = retrieve._conn()
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE _should_not_write (x INTEGER)")


def test_each_thread_gets_a_distinct_connection():
    main_con = retrieve._conn()
    grabbed: dict[int, object] = {}

    def grab(i):
        grabbed[i] = retrieve._conn()  # refs held in the dict -> ids stay stable

    ts = [threading.Thread(target=grab, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    cons = [main_con, *grabbed.values()]
    assert len({id(c) for c in cons}) == len(cons)  # no connection shared across threads


def test_concurrent_reads_stay_correct_under_contention():
    # Without per-thread connections this raises KeyError('fact_id') or returns a
    # different query's row; with the fix every read is correct.
    warm = retrieve.retrieve_evidence(
        Query("AAPL", "revenue", Period(period_end=date(2023, 9, 30))))
    assert warm.found and warm.value == pytest.approx(_AAPL_REV_2023, rel=1e-6)

    errors: list[str] = []

    def hammer():
        try:
            for _ in range(50):
                ev = retrieve.retrieve_evidence(
                    Query("AAPL", "revenue", Period(period_end=date(2023, 9, 30))))
                assert ev.found and ev.value == pytest.approx(_AAPL_REV_2023, rel=1e-6)
        except Exception as exc:  # noqa: BLE001 — any error here is the regression
            errors.append(repr(exc))

    ts = [threading.Thread(target=hammer) for _ in range(16)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert not errors, errors[:3]
