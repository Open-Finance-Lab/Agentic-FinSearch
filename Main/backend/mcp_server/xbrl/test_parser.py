"""Tests for iXBRL parser against real SEC filings."""

import pytest
from pathlib import Path
from mcp_server.xbrl.parser import parse_filing, find_filing, query_facts

FILINGS_DIR = Path(__file__).parent / "filings"


class TestParseFiling:
    """Test that parse_filing extracts correct values from real iXBRL files."""

    def test_aapl_effective_tax_rate(self):
        facts = parse_filing(FILINGS_DIR / "aapl-20230930.xml")
        matches = [
            f for f in facts
            if f["tag"] == "EffectiveIncomeTaxRateContinuingOperations"
            and not f["has_dimensions"]
        ]
        # Apple reports 3 years of tax rates in the 10-K
        assert len(matches) >= 3
        # FY2023 (ending 2023-09-30) should be 14.7% = 0.147
        fy2023 = [f for f in matches if f["period_end"] == "2023-09-30"]
        assert len(fy2023) == 1
        assert abs(fy2023[0]["value"] - 0.147) < 0.001
        assert fy2023[0]["unit"] == "pure"

    def test_aapl_revenue(self):
        facts = parse_filing(FILINGS_DIR / "aapl-20230930.xml")
        matches = [
            f for f in facts
            if f["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
            and not f["has_dimensions"]
            and f["period_end"] == "2023-09-30"
            and f["period_start"] is not None  # duration, not instant
        ]
        assert len(matches) == 1
        # Apple FY2023 total revenue = $383,285M
        assert abs(matches[0]["value"] - 383_285_000_000) < 1_000_000
        assert matches[0]["unit"] == "USD"

    def test_msft_revenue(self):
        facts = parse_filing(FILINGS_DIR / "msft-20230630.xml")
        matches = [
            f for f in facts
            if f["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
            and not f["has_dimensions"]
            and f["period_end"] == "2023-06-30"
            and f["period_start"] is not None
        ]
        assert len(matches) == 1
        # Microsoft FY2023 total revenue = $211,915M
        assert abs(matches[0]["value"] - 211_915_000_000) < 1_000_000

    def test_tsla_revenue(self):
        facts = parse_filing(FILINGS_DIR / "tsla-20231231.xml")
        matches = [
            f for f in facts
            if f["tag"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
            and not f["has_dimensions"]
            and f["period_end"] == "2023-12-31"
            and f["period_start"] is not None
        ]
        assert len(matches) == 1
        # Tesla FY2023 total revenue = $96,773M
        assert abs(matches[0]["value"] - 96_773_000_000) < 1_000_000

    def test_facts_have_required_fields(self):
        facts = parse_filing(FILINGS_DIR / "aapl-20230930.xml")
        assert len(facts) > 0
        required_keys = {"tag", "namespace", "value", "unit", "period_start", "period_end", "has_dimensions"}
        for fact in facts[:10]:
            assert required_keys.issubset(fact.keys()), f"Missing keys in {fact}"


class TestFindFiling:
    """Test company name to filing file resolution."""

    def test_find_by_ticker(self):
        path = find_filing("aapl", FILINGS_DIR)
        assert path is not None
        assert "aapl" in path.name

    def test_find_case_insensitive(self):
        path = find_filing("TSLA", FILINGS_DIR)
        assert path is not None
        assert "tsla" in path.name

    def test_find_by_company_name(self):
        # "apple" should match "aapl" via an alias map
        path = find_filing("apple", FILINGS_DIR)
        assert path is not None
        assert "aapl" in path.name

    def test_find_nonexistent_returns_none(self):
        path = find_filing("nonexistent", FILINGS_DIR)
        assert path is None


class TestQueryFacts:
    """Test the high-level query interface."""

    def test_query_aapl_tax_rate(self):
        results = query_facts("apple", "EffectiveIncomeTaxRateContinuingOperations", FILINGS_DIR)
        assert len(results) >= 1
        # Should include FY2023 value
        values = [r["value"] for r in results]
        assert any(abs(v - 0.147) < 0.001 for v in values)

    def test_query_unknown_tag_returns_empty(self):
        results = query_facts("apple", "NonExistentTag123", FILINGS_DIR)
        assert results == []

    def test_query_unknown_company_returns_empty(self):
        results = query_facts("nonexistent", "Revenue", FILINGS_DIR)
        assert results == []
