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
