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


# ── tags_tried is honest (provenance, not the whole registry tuple) ───

def test_tags_tried_reports_only_the_first_tag_on_a_first_hit(con):
    # 'revenue' has 4 candidate tags; the synthetic reports the FIRST, so tags_tried
    # must be just that one — not all four (they were never tried).
    ev = retrieve.retrieve_evidence(
        Query("INST", "revenue", Period(period_end=date(2023, 12, 31))), con=con)
    assert ev.found and ev.tags_tried == ("RevenueFromContractWithCustomerExcludingAssessedTax",)


def test_tags_tried_includes_skipped_tags_up_to_the_one_that_hit():
    # 'cost_of_revenue' = (CostOfGoodsAndServicesSold, CostOfRevenue, CostOfGoodsSold).
    # A filer reporting only the SECOND tag must surface both attempted tags.
    c = store.connect(":memory:")
    ingest.ingest_doc(c, {"cik": 333, "entityName": "Cogs Co", "facts": {"us-gaap": {
        "CostOfRevenue": {"units": {"USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 42, "accn": "a", "fy": 2023,
             "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}}}}})
    c.execute("UPDATE entities SET ticker = 'COGS' WHERE cik = 333")
    ev = retrieve.retrieve_evidence(
        Query("COGS", "cost_of_revenue", Period(period_end=date(2023, 12, 31))), con=c)
    assert ev.value == 42.0
    assert ev.tags_tried == ("CostOfGoodsAndServicesSold", "CostOfRevenue")  # not CostOfGoodsSold


# ── filing_reports_tag: latest filing, not "ever" ────────────────────

def _bs_doc(cik, ticker, entries):
    """entries: list of (tag, accn, filed) instant facts at 2023-12-31, all form 10-K."""
    facts = {}
    for tag, accn, filed in entries:
        facts.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(
            {"end": "2023-12-31", "val": 1, "accn": accn, "fy": 2023, "fp": "FY",
             "form": "10-K", "filed": filed})
    return {"cik": cik, "entityName": ticker, "facts": {"us-gaap": facts}}, ticker


def test_filing_reports_tag_true_when_latest_10k_classifies():
    c = store.connect(":memory:")
    doc, _ = _bs_doc(11, "CLS", [("Assets", "a", "2024-02-01"), ("AssetsCurrent", "a", "2024-02-01")])
    ingest.ingest_doc(c, doc)
    c.execute("UPDATE entities SET ticker = 'CLS' WHERE cik = 11")
    assert retrieve.filing_reports_tag("CLS", "AssetsCurrent", con=c) is True


def test_filing_reports_tag_false_when_latest_10k_is_unclassified():
    c = store.connect(":memory:")
    doc, _ = _bs_doc(12, "BANK", [("Assets", "a", "2024-02-01")])   # no AssetsCurrent ever
    ingest.ingest_doc(c, doc)
    c.execute("UPDATE entities SET ticker = 'BANK' WHERE cik = 12")
    assert retrieve.filing_reports_tag("BANK", "AssetsCurrent", con=c) is False


def test_filing_reports_tag_keys_on_latest_filing_not_any_filing_ever():
    # Older 10-K (2021) classified; newest 10-K (2024) dropped the current/non-current
    # split. The check must read the LATEST filing -> False, despite AssetsCurrent once
    # having existed. This is the semantic the "any filing ever" version got wrong.
    c = store.connect(":memory:")
    doc, _ = _bs_doc(13, "SWCH", [
        ("Assets", "old", "2021-02-01"), ("AssetsCurrent", "old", "2021-02-01"),
        ("Assets", "new", "2024-02-01"),                       # latest 10-K, no AssetsCurrent
    ])
    ingest.ingest_doc(c, doc)
    c.execute("UPDATE entities SET ticker = 'SWCH' WHERE cik = 13")
    assert retrieve.filing_reports_tag("SWCH", "AssetsCurrent", con=c) is False


def test_filing_reports_tag_none_for_unknown_entity(con):
    assert retrieve.filing_reports_tag("NOPE", "AssetsCurrent", con=con) is None


def test_filing_reports_tag_same_day_filings_resolve_deterministically():
    # Two 10-Ks filed the SAME day (original + same-day 10-K/A) with different
    # accessions; only the alphabetically-greater accession reports AssetsCurrent.
    # The accession-DESC tiebreak must pick it stably, not flip on insert order.
    c = store.connect(":memory:")
    doc, _ = _bs_doc(14, "SMDY", [
        ("Assets", "acc-A", "2024-02-01"),                     # original, no AssetsCurrent
        ("Assets", "acc-B", "2024-02-01"), ("AssetsCurrent", "acc-B", "2024-02-01"),
    ])
    ingest.ingest_doc(c, doc)
    c.execute("UPDATE entities SET ticker = 'SMDY' WHERE cik = 14")
    # acc-B sorts after acc-A -> chosen -> reports AssetsCurrent -> True, every run.
    assert retrieve.filing_reports_tag("SMDY", "AssetsCurrent", con=c) is True
