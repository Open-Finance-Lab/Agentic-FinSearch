import pytest

from truthlayer.registry import (
    CONCEPT_REGISTRY, RATIO_CONCEPTS, REGISTRY_VERSION, get_concept, ConceptNotFound,
)


def test_revenue_is_a_top_level_concept_shared_not_trapped_in_a_ratio():
    spec = get_concept("revenue")
    assert spec.period_type == "duration"
    assert spec.tags[0] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_instant_vs_duration_classification():
    assert get_concept("assets").period_type == "instant"
    assert get_concept("cost_of_revenue").period_type == "duration"


def test_ratios_reference_concepts_by_name():
    assert RATIO_CONCEPTS["gross_margin"] == {"revenue": "revenue", "cogs": "cost_of_revenue"}
    # every referenced concept must exist in the registry
    for mapping in RATIO_CONCEPTS.values():
        for concept in mapping.values():
            assert concept in CONCEPT_REGISTRY


def test_unknown_concept_raises():
    with pytest.raises(ConceptNotFound):
        get_concept("ebitda_magic")


def test_registry_is_versioned():
    assert REGISTRY_VERSION  # non-empty pin string
