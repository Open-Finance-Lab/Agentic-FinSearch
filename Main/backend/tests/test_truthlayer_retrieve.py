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


DUAL_UNIT = {
    "cik": 555, "entityName": "Dual Co",
    "facts": {"us-gaap": {"Assets": {"units": {
        "USD": [{"end": "2023-12-31", "val": 5000, "accn": "u", "fy": 2023, "fp": "FY",
                 "form": "10-K", "filed": "2024-02-01"}],
        "EUR": [{"end": "2023-12-31", "val": 4600, "accn": "e", "fy": 2023, "fp": "FY",
                 "form": "10-K", "filed": "2024-03-01"}],   # later-filed, but wrong currency
    }}}},
}


def test_prefers_usd_when_multiple_units():
    c = store.connect(":memory:")
    ingest.ingest_doc(c, DUAL_UNIT)
    c.execute("UPDATE entities SET ticker = 'DUAL' WHERE cik = 555")
    ev = retrieve.retrieve_evidence(
        Query("DUAL", "assets", Period(period_end=date(2023, 12, 31))), con=c)
    assert ev.value == 5000.0 and ev.unit == "USD"   # not the later-filed EUR 4600


def test_whitespace_entity_still_resolves(con):
    clean = retrieve.retrieve_evidence(
        Query("INST", "assets", Period(period_end=date(2023, 12, 31))), con=con)
    padded = retrieve.retrieve_evidence(
        Query(" INST ", "assets", Period(period_end=date(2023, 12, 31))), con=con)
    assert clean.found and padded.found and padded.value == clean.value


def test_numeric_ticker_resolves_to_its_cik_not_the_literal_int():
    c = store.connect(":memory:")
    ingest.ingest_doc(c, {"cik": 999, "entityName": "N", "facts": {"us-gaap": {"Assets": {
        "units": {"USD": [{"end": "2023-12-31", "val": 7, "accn": "a", "fy": 2023, "fp": "FY",
                           "form": "10-K", "filed": "2024-02-01"}]}}}}})
    c.execute("UPDATE entities SET ticker = '8086' WHERE cik = 999")   # non-US numeric ticker
    ev = retrieve.retrieve_evidence(
        Query("8086", "assets", Period(period_end=date(2023, 12, 31))), con=c)
    assert ev.found and ev.value == 7.0    # via ticker 8086 -> cik 999, not int('8086')
