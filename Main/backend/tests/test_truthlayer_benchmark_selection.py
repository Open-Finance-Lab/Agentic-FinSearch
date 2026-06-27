"""Concept->tag SELECTION reconciled against the benchmark generator (S2/S13, the
half that the fact_id hash reconciliation did NOT cover).

The generator stores, per concept, ONE us-gaap tag inside each gold_fact_path hash.
Track-R facts-reached only joins if our resolver SELECTS that same tag. The rule
was recovered empirically by reversing 858 gold hashes in cases_v1_final.json
against fetched SEC companyfacts (tooling: truthlayer/_tagrecover/): for every
concept the generator uses a single, CONFLICT-FREE global tag-priority order, and
`retrieve_evidence`'s first-present-tag-wins reproduces it because `_select` only
considers a tag that actually reports a fact for the queried period.

These tests pin (a) the recovered priority order and (b) end-to-end gold anchors:
registry-selected tag -> make_fact_id -> a REAL gold_fact_path hash, for each
benchmark concept our registry did not previously cover.
"""
from datetime import date

import pytest

from truthlayer import store
from truthlayer.registry import (
    CONCEPT_REGISTRY, BENCHMARK_CONCEPT_ALIAS, get_concept,
    resolve_benchmark_concept,
)


def _first_present_tag(concept: str, available: set[str]) -> str | None:
    """Offline mirror of retrieve_evidence: first registry tag for `concept` that is
    present (i.e. that _select would find a period fact for). The real path filters
    presence by period in SQL; here `available` is the set of tags reporting the
    queried period, so this exercises the registry ORDER identically."""
    for tag in get_concept(concept).tags:
        if tag in available:
            return tag
    return None


# --- recovered ground truth: (cik, registry-concept, tag, ps, pe, unit, accn, gold) ---
# Each was reversed from a real case's gold_fact_path; see _tagrecover/anchors.py.
GOLD_ANCHORS = [
    ("net_income", 97476, "NetIncomeLoss",
     date(2025, 1, 1), date(2025, 12, 31), "USD", "0000097476-26-000059",
     "f494a68abe558cc9db28a70b4feb6f6c955158b4"),
    ("operating_income", 18230, "OperatingIncomeLoss",
     date(2025, 1, 1), date(2025, 12, 31), "USD", "0000018230-26-000008",
     "3d9854ad48bfa32e78420b185d1070703dbead5e"),
    ("gross_profit", 1045810, "GrossProfit",
     date(2025, 1, 27), date(2026, 1, 25), "USD", "0001045810-26-000021",
     "e4fd53279204a524d56372e0bb045557abcced18"),
    ("research_and_development", 1045810, "ResearchAndDevelopmentExpense",
     date(2024, 1, 29), date(2025, 1, 26), "USD", "0001045810-26-000021",
     "62a99caa879716902576a53f0ffe313fb98f8448"),
    ("cash_and_equivalents", 1707925, "CashAndCashEquivalentsAtCarryingValue",
     None, date(2025, 12, 31), "USD", "0001628280-26-011430",
     "e4e4e70e394e119cc27ee54493b059aba335ee79"),
    # CrowdStrike reports revenue ONLY under the Including-assessed-tax variant — the
    # lone gold fact (of 230) using it. Caught by the full 742-fact validation sweep.
    ("revenue", 1535527, "RevenueFromContractWithCustomerIncludingAssessedTax",
     date(2025, 2, 1), date(2026, 1, 31), "USD", "0001535527-26-000010",
     "ee6fdb46fe1c98b76ddfcd12bdfe4cf6dc71cf5c"),
]


@pytest.mark.parametrize("concept,cik,tag,ps,pe,unit,accn,gold", GOLD_ANCHORS,
                         ids=[a[0] for a in GOLD_ANCHORS])
def test_registry_selected_tag_reproduces_real_gold_hash(concept, cik, tag, ps, pe, unit, accn, gold):
    # The registry must (1) know this benchmark concept and (2) select `tag` for it;
    # then the reconciled make_fact_id recipe must reproduce the real gold hash.
    selected = _first_present_tag(concept, {tag})
    assert selected == tag, f"{concept}: registry selected {selected!r}, gold used {tag!r}"
    assert store.make_fact_id(cik, selected, ps, pe, unit, accn) == gold


def test_new_concepts_have_correct_period_type():
    assert get_concept("net_income").period_type == "duration"
    assert get_concept("operating_income").period_type == "duration"
    assert get_concept("gross_profit").period_type == "duration"
    assert get_concept("research_and_development").period_type == "duration"
    assert get_concept("cash_and_equivalents").period_type == "instant"


def test_net_income_priority_netincomeloss_over_profitloss():
    # Recovered: NetIncomeLoss > ProfitLoss (gold chose NetIncomeLoss 15x head-to-head).
    assert _first_present_tag("net_income", {"NetIncomeLoss", "ProfitLoss"}) == "NetIncomeLoss"
    # Falls through to ProfitLoss only when NetIncomeLoss is absent (gold did this 6x).
    assert _first_present_tag("net_income", {"ProfitLoss"}) == "ProfitLoss"


def test_revenue_order_matches_generator_priority():
    # The crux of yesterday's false-alarm: order is RevenueFromContractExcluding >
    # Revenues > SalesRevenueNet > SalesRevenueGoodsNet (conflict-free over 230 facts).
    tags = get_concept("revenue").tags
    order = {t: i for i, t in enumerate(tags)}
    assert order["RevenueFromContractWithCustomerExcludingAssessedTax"] < order["Revenues"]
    assert order["Revenues"] < order["SalesRevenueNet"]
    assert order["SalesRevenueNet"] < order["SalesRevenueGoodsNet"]
    # Including-assessed-tax variant ranks below both Excluding and Revenues (recovered
    # constraints: Excluding > Including, Revenues > Including); only used when alone.
    assert order["RevenueFromContractWithCustomerExcludingAssessedTax"] < order["RevenueFromContractWithCustomerIncludingAssessedTax"]
    assert order["Revenues"] < order["RevenueFromContractWithCustomerIncludingAssessedTax"]
    assert _first_present_tag(
        "revenue", {"RevenueFromContractWithCustomerIncludingAssessedTax"}
    ) == "RevenueFromContractWithCustomerIncludingAssessedTax"
    # NVDA regime: only Revenues present for FY2026 -> first-tag-wins falls through.
    assert _first_present_tag("revenue", {"Revenues"}) == "Revenues"
    # Both present -> the generator (and we) pick RevenueFromContract.
    assert _first_present_tag(
        "revenue", {"RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"}
    ) == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_cogs_and_equity_orders_match_generator():
    cogs = {t: i for i, t in enumerate(get_concept("cost_of_revenue").tags)}
    assert cogs["CostOfGoodsAndServicesSold"] < cogs["CostOfRevenue"]  # 50 vs 21 in gold
    eq = {t: i for i, t in enumerate(get_concept("equity").tags)}
    assert (eq["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
            < eq["StockholdersEquity"])


def test_benchmark_concept_alias_resolves_every_single_fact_concept():
    # The benchmark keys required_facts by its OWN concept names (total_assets, cogs,
    # stockholders_equity, reported_gross_profit, ...). The grader maps them to our
    # registry via BENCHMARK_CONCEPT_ALIAS; every alias must land on a real concept.
    expected_primary = {
        "revenue": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "cogs": "CostOfGoodsAndServicesSold",
        "reported_gross_profit": "GrossProfit",
        "operating_income": "OperatingIncomeLoss",
        "net_income": "NetIncomeLoss",
        "research_and_development": "ResearchAndDevelopmentExpense",
        "total_assets": "Assets",
        "total_liabilities": "Liabilities",
        "stockholders_equity":
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "current_assets": "AssetsCurrent",
        "current_liabilities": "LiabilitiesCurrent",
        "cash_and_equivalents": "CashAndCashEquivalentsAtCarryingValue",
    }
    for bench_key, primary_tag in expected_primary.items():
        concept = resolve_benchmark_concept(bench_key)
        assert concept in CONCEPT_REGISTRY, f"{bench_key} -> {concept!r} not in registry"
        assert get_concept(concept).tags[0] == primary_tag, (
            f"{bench_key}: primary tag is {get_concept(concept).tags[0]!r}, "
            f"generator used {primary_tag!r}")
    # The alias map must not silently drop a benchmark concept.
    assert set(BENCHMARK_CONCEPT_ALIAS) == set(expected_primary)
