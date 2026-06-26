from datetime import date
from decimal import Decimal

from truthlayer.contracts import Evidence, Provenance, Period, Query


def test_period_demo_path_and_benchmark_path():
    demo = Period(period_end=date(2023, 9, 30))
    assert demo.period_end == date(2023, 9, 30) and demo.fiscal_year is None
    bench = Period(fiscal_year=2026, fiscal_period="FY")
    assert bench.fiscal_year == 2026 and bench.period_end is None


def test_query_defaults_as_of_none():
    q = Query(entity="AAPL", concept="assets", period=Period(period_end=date(2023, 9, 30)))
    assert q.as_of is None


def test_evidence_is_frozen_and_carries_provenance():
    prov = Provenance(fact_id="abc", cik=320193, accession="x", filed=date(2023, 11, 3),
                      form="10-K", taxonomy="us-gaap", tag="Assets", fy=2023, fp="FY", frame="CY2023Q3I")
    ev = Evidence(concept="assets", value=1.0, value_exact=Decimal("1.0"), unit="USD",
                  period=Period(period_end=date(2023, 9, 30)), as_of=None, provenance=prov,
                  found=True, tags_tried=("Assets",), restated_later=False)
    assert ev.provenance.tag == "Assets"
    import dataclasses, pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.value = 2.0
