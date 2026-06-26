from __future__ import annotations

from dataclasses import dataclass

REGISTRY_VERSION = "2026-06-26"


class ConceptNotFound(KeyError):
    """Raised when a concept name is not in CONCEPT_REGISTRY (a wiring bug)."""


@dataclass(frozen=True)
class ConceptSpec:
    period_type: str               # 'instant' (balance sheet) | 'duration' (income stmt)
    tags: tuple[str, ...]          # ordered us-gaap candidates; first match wins


CONCEPT_REGISTRY: dict[str, ConceptSpec] = {
    "assets":              ConceptSpec("instant",  ("Assets",)),
    "liabilities":         ConceptSpec("instant",  ("Liabilities",)),
    "equity":              ConceptSpec("instant",  (
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity")),
    "temporary_equity":    ConceptSpec("instant",  (
        "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
        "RedeemableNoncontrollingInterestEquityCarryingAmount")),
    "revenue":             ConceptSpec("duration", (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet")),
    "cost_of_revenue":     ConceptSpec("duration", (
        "CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold")),
    "current_assets":      ConceptSpec("instant",  ("AssetsCurrent",)),
    "current_liabilities": ConceptSpec("instant",  ("LiabilitiesCurrent",)),
}

# Ratios reference CONCEPTS (rename map keeps the engine's input names stable):
RATIO_CONCEPTS: dict[str, dict[str, str]] = {
    "accounting_equation": {"assets": "assets", "liabilities": "liabilities",
                            "equity": "equity", "temporary_equity": "temporary_equity"},
    "gross_margin":        {"revenue": "revenue", "cogs": "cost_of_revenue"},
    "current_ratio":       {"current_assets": "current_assets",
                            "current_liabilities": "current_liabilities"},
}


def get_concept(concept: str) -> ConceptSpec:
    try:
        return CONCEPT_REGISTRY[concept]
    except KeyError as exc:
        raise ConceptNotFound(concept) from exc
