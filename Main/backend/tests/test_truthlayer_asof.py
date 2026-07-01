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


def test_restated_later_detects_change_above_float_precision():
    # 2^53 -> 2^53+1 is invisible to float (DOUBLE) equality; the exact (DECIMAL)
    # value_exact probe must still flag the restatement.
    c = store.connect(":memory:")
    ingest.ingest_doc(c, {"cik": 444, "entityName": "Big", "facts": {"us-gaap": {"Assets": {
        "units": {"USD": [
            {"end": "2023-12-31", "val": 9007199254740992, "accn": "o", "fy": 2023, "fp": "FY",
             "form": "10-K", "filed": "2024-02-01"},
            {"end": "2023-12-31", "val": 9007199254740993, "accn": "r", "fy": 2023, "fp": "FY",
             "form": "10-K", "filed": "2025-02-01"},
        ]}}}}})
    c.execute("UPDATE entities SET ticker = 'BIG' WHERE cik = 444")
    ev = retrieve.retrieve_evidence(
        Query("BIG", "assets", Period(period_end=date(2023, 12, 31)), as_of=date(2024, 6, 1)), con=c)
    assert ev.restated_later is True            # exact probe catches the +1 float would miss


def test_restated_later_ignores_other_currency():
    # A later filing in a DIFFERENT unit is not a restatement of the USD fact.
    c = store.connect(":memory:")
    ingest.ingest_doc(c, {"cik": 666, "entityName": "FX", "facts": {"us-gaap": {"Assets": {
        "units": {
            "USD": [{"end": "2023-12-31", "val": 1000, "accn": "u", "fy": 2023, "fp": "FY",
                     "form": "10-K", "filed": "2024-02-01"}],
            "EUR": [{"end": "2023-12-31", "val": 920, "accn": "e", "fy": 2023, "fp": "FY",
                     "form": "10-K", "filed": "2024-12-01"}],
        }}}}})
    c.execute("UPDATE entities SET ticker = 'FX' WHERE cik = 666")
    ev = retrieve.retrieve_evidence(
        Query("FX", "assets", Period(period_end=date(2023, 12, 31)), as_of=date(2024, 6, 1)), con=c)
    assert ev.value == 1000.0 and ev.unit == "USD"
    assert ev.restated_later is False           # the later EUR fact is not a USD restatement
