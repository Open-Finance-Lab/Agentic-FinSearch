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


import os
import unittest.mock


class TestFoundation(unittest.TestCase):
    def test_clean_text_strips_control_bidi_and_marker_token(self):
        import news_signals as ns
        s = "a\u202eb\x00c NEWS_DATA d"  # escapes only — never paste literal bidi chars
        out = ns.clean_text(s, 100)
        self.assertEqual(out, "abc  d")

    def test_clean_text_caps_and_handles_none(self):
        import news_signals as ns
        self.assertEqual(ns.clean_text(None, 5), "")
        self.assertEqual(ns.clean_text("x" * 10, 5), "xxxxx")

    def test_load_config_defaults_and_fallbacks(self):
        import news_signals as ns
        env = {"HEARTBEAT_HOME": "/tmp/hb-test", "HEARTBEAT_MODEL": "some-model"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            cfg = ns.load_config()
        self.assertEqual(str(cfg["digests"]), "/tmp/hb-test/digests")
        self.assertEqual(str(cfg["signals_dir"]), "/tmp/hb-test/signals")
        self.assertEqual(str(cfg["state_path"]), "/tmp/hb-test/signals_state.json")
        self.assertEqual(cfg["model"], "some-model")  # SIGNALS_MODEL falls back
        self.assertEqual(cfg["threshold"], 0.20)
        self.assertEqual(cfg["damp_cap"], 0.7)
        self.assertEqual(cfg["damp_min_articles"], 2)
        self.assertEqual(cfg["per_ticker_cap"], 3)
        self.assertEqual(cfg["watchlist"], sorted(set(ns.DEFAULT_WATCHLIST.split())))

    def test_signals_model_overrides_heartbeat_model(self):
        import news_signals as ns
        env = {"SIGNALS_MODEL": "better-model", "HEARTBEAT_MODEL": "worse-model"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(ns.load_config()["model"], "better-model")


import tempfile
import time

import news_signals as ns


def make_story(**over):
    s = {
        "guid": "g1", "title": "Microsoft raises Azure guidance",
        "link": "https://example.com/a", "source": "Reuters",
        "published": time.time() - 3600, "description": "desc",
        "tickers": ["MSFT"], "feeds": ["news"], "score": 5.0,
    }
    s.update(over)
    return s


def write_items(dirpath, stories, name="items-2026-07-06.jsonl"):
    p = Path(dirpath) / name
    p.write_text("\n".join(json.dumps(s) for s in stories) + "\n", encoding="utf-8")
    return p


class TestValidationGate(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)

    def test_happy_path_cleans_and_uppercases(self):
        p = write_items(self.td, [make_story(title="ok\u202etitle",
                                             tickers=["msft"])])
        stories = ns.validation_gate(p, 10)
        self.assertEqual(stories[0]["title"], "oktitle")
        self.assertEqual(stories[0]["tickers"], ["MSFT"])

    def test_missing_required_field_is_poison_pill(self):
        s = make_story()
        del s["source"]
        p = write_items(self.td, [s])
        with self.assertRaises(ValueError):
            ns.validation_gate(p, 10)

    def test_malformed_json_line_is_poison_pill(self):
        p = Path(self.td) / "items-x.jsonl"
        p.write_text('{"broken\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            ns.validation_gate(p, 10)

    def test_oversized_file_is_poison_pill(self):
        p = write_items(self.td, [make_story()])
        with self.assertRaises(ValueError):
            ns.validation_gate(p, 0)  # 0 MB cap

    def test_published_outside_sanity_window_drops_story_not_batch(self):
        future = make_story(guid="future", published=time.time() + 7200)
        ancient = make_story(guid="ancient", published=time.time() - 40 * 86400)
        ok = make_story(guid="ok")
        p = write_items(self.td, [future, ancient, ok])
        stories = ns.validation_gate(p, 10)
        self.assertEqual([s["guid"] for s in stories], ["ok"])
