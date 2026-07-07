"""Unit tests for news_signals.py (signals spec 2026-07-06, amended)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "signals-v1.schema.json"

DIAG_KEYS = [
    "stories_total", "candidates_dropped_not_subject", "near_dups_collapsed",
    "candidates_selected", "tickers_with_candidates", "tickers_no_candidates",
    "tickers_capped", "tickers_omitted_by_llm", "tickers_dropped_guid_mismatch",
    "scores_damped",
]
SIGNAL_KEYS = [
    "sentiment", "score", "rationale", "headline", "source", "url",
    "published", "guid", "n_articles",
]


class TestSchemaFile(unittest.TestCase):
    """Stdlib sanity checks; full jsonschema validation lives in
    Main/backend/tests/test_signals_contract.py (jsonschema ships in the
    backend uv env; the heartbeat CI stays dependency-free)."""

    def test_schema_parses_and_pins_the_contract(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        diag = schema["properties"]["diagnostics"]
        self.assertEqual(sorted(diag["required"]), sorted(DIAG_KEYS))
        self.assertFalse(diag.get("additionalProperties", True))
        entry = schema["properties"]["signals"]["additionalProperties"]
        self.assertEqual(sorted(entry["required"]), sorted(SIGNAL_KEYS))
        self.assertEqual(entry["properties"]["sentiment"]["enum"],
                         ["bullish", "bearish", "neutral"])
        self.assertEqual(schema["properties"]["status"]["enum"], ["ok", "degraded"])
