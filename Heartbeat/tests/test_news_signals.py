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


class TestSubjectGate(unittest.TestCase):
    def test_symbol_token_in_headline_is_subject(self):
        self.assertTrue(ns.is_subject("NVDA jumps 5% on record orders", "NVDA"))

    def test_company_alias_is_subject(self):
        self.assertTrue(ns.is_subject("Nvidia unveils next-gen GPU", "NVDA"))
        self.assertTrue(ns.is_subject("Alphabet beats on ad revenue", "GOOGL"))

    def test_mention_only_is_not_subject(self):
        self.assertFalse(ns.is_subject("Tech megacaps rally into the close", "NVDA"))

    def test_roundup_titles_are_blocked_even_with_symbol(self):
        self.assertFalse(ns.is_subject("Company News for July 6, 2026", "AAPL"))
        self.assertFalse(ns.is_subject("MSFT, AAPL are part of Zacks Earnings Preview", "MSFT"))
        self.assertFalse(ns.is_subject("5 stocks to watch this week: NVDA leads", "NVDA"))
        self.assertFalse(ns.is_subject("This AI stock joined the Dow", "NVDA"))

    def test_short_symbols_require_alias_not_token(self):
        self.assertFalse(ns.is_subject("GTA V breaks sales records", "V"))
        self.assertTrue(ns.is_subject("Visa expands stablecoin settlement", "V"))

    def test_hyphenated_symbols_match(self):
        self.assertTrue(ns.is_subject("BRK-B edges higher after 13F", "BRK-B"))
        self.assertTrue(ns.is_subject("Bitcoin tops $120k", "BTC-USD"))

    def test_alias_match_is_word_bounded_not_substring(self):
        # A naive `alias in lowered` substring check false-matches company
        # aliases embedded inside unrelated words. All three are real words
        # that show up routinely in financial headlines.
        self.assertFalse(ns.is_subject(
            "This Magnificent Artificial Intelligence Stock Rallies", "INTC"))
        self.assertFalse(ns.is_subject(
            "San Francisco Fed officials weigh in on rate path", "CSCO"))
        self.assertFalse(ns.is_subject(
            "Analysts say the merger looks advisable for shareholders", "V"))
        # genuine mentions must still pass once word-bounded
        self.assertTrue(ns.is_subject("Intel unveils new AI chip", "INTC"))
        self.assertTrue(ns.is_subject("Cisco beats on earnings", "CSCO"))

    def test_alias_3m_does_not_match_bare_dollar_or_share_figures(self):
        # "3m" is the only way to catch prose that names the company as "3M"
        # rather than the ticker "MMM" \u2014 but a plain \b3m\b still matches
        # "$3M" ($ and digit-then-nonword are boundaries too), so the numeric
        # alias needs an extra exclusion on top of word-boundaries.
        self.assertFalse(ns.is_subject("Company reports $3M in quarterly losses", "MMM"))
        self.assertFalse(ns.is_subject("Firm raised $13M in its latest round", "MMM"))
        self.assertFalse(ns.is_subject("Stock trades 133M shares in a single session", "MMM"))
        # genuine mentions must still pass
        self.assertTrue(ns.is_subject("3M raises full-year guidance", "MMM"))
        self.assertTrue(ns.is_subject("Shares of 3M rose after an analyst upgrade", "MMM"))


class TestSelection(unittest.TestCase):
    def _cfg(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            return ns.load_config()

    def test_near_dups_collapse_and_n_articles_counts_distinct(self):
        stories = [
            make_story(guid="a", title="Microsoft raises Azure guidance", score=6.0),
            make_story(guid="b", title="Microsoft raises Azure guidance!", score=3.0),
            make_story(guid="c", title="Microsoft cloud momentum lifts outlook", score=4.0),
        ]
        capped, n_articles, diag = ns.select_candidates(
            stories, ["MSFT"], self._cfg())
        self.assertEqual(n_articles["MSFT"], 2)  # dup collapsed BEFORE count
        self.assertEqual(diag["near_dups_collapsed"], 1)
        self.assertEqual([s["guid"] for s in capped["MSFT"]], ["a", "c"])

    def test_subject_gate_drops_feed_into_diagnostics(self):
        stories = [
            make_story(guid="r", title="Company News for July 6, 2026",
                       tickers=["AAPL", "GOOGL"]),
            make_story(guid="s", title="Apple raises iPhone guidance",
                       tickers=["AAPL"]),
        ]
        capped, n_articles, diag = ns.select_candidates(
            stories, ["AAPL", "GOOGL"], self._cfg())
        self.assertEqual(diag["candidates_dropped_not_subject"], 2)
        self.assertEqual(list(capped), ["AAPL"])
        self.assertEqual(n_articles["AAPL"], 1)

    def test_editorial_gate_and_cap_order(self):
        cfg = self._cfg()
        stories = [make_story(guid="low", score=1.0)] + [
            make_story(guid=f"g{i}", score=3.0 + i,
                       title=f"Microsoft ships product number {i} today",
                       published=time.time() - i * 60)
            for i in range(5)
        ]
        capped, n_articles, diag = ns.select_candidates(stories, ["MSFT"], cfg)
        self.assertEqual(n_articles["MSFT"], 5)          # low-editorial excluded
        self.assertEqual(len(capped["MSFT"]), cfg["per_ticker_cap"])
        self.assertEqual([s["guid"] for s in capped["MSFT"]], ["g4", "g3", "g2"])
        self.assertEqual(diag["tickers_capped"], 1)

    def test_non_watchlist_tickers_ignored(self):
        stories = [make_story(tickers=["ZZZZ"])]
        capped, n_articles, diag = ns.select_candidates(stories, ["MSFT"], self._cfg())
        self.assertEqual(capped, {})


class TestPromptAndLabel(unittest.TestCase):
    def test_every_candidate_text_is_datamarked(self):
        cands = {"MSFT": [make_story(description="Azure demand is strong")]}
        system, user = ns.build_prompt(cands, time.time(), 200)
        payload = json.loads(user)
        entry = payload["MSFT"][0]
        self.assertTrue(entry["title"].startswith(ns.MARK_OPEN))
        self.assertTrue(entry["title"].endswith(ns.MARK_CLOSE))
        self.assertTrue(entry["description"].startswith(ns.MARK_OPEN))
        self.assertIn("untrusted", system)
        self.assertIn(ns.MARK_OPEN, system)

    def test_description_capped_and_guid_passthrough(self):
        cands = {"MSFT": [make_story(description="x" * 500)]}
        _, user = ns.build_prompt(cands, time.time(), 200)
        entry = json.loads(user)["MSFT"][0]
        self.assertLessEqual(
            len(entry["description"]) - len(ns.MARK_OPEN) - len(ns.MARK_CLOSE) - 2,
            200)
        self.assertEqual(entry["guid"], "g1")

    def test_derive_label_thresholds(self):
        self.assertEqual(ns.derive_label(0.20, 0.20), "bullish")
        self.assertEqual(ns.derive_label(0.19, 0.20), "neutral")
        self.assertEqual(ns.derive_label(-0.20, 0.20), "bearish")
        self.assertEqual(ns.derive_label(0.0, 0.20), "neutral")


class TestCallLlm(unittest.TestCase):
    def _cfg(self):
        with unittest.mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}, clear=True):
            return ns.load_config()

    def test_returns_parsed_content_json(self):
        payload = {"choices": [{"message": {"content":
                   json.dumps({"overview": "o", "tickers": {}})}}]}
        fake_resp = unittest.mock.MagicMock()
        fake_resp.read.return_value = json.dumps(payload).encode()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda s, *a: False
        with unittest.mock.patch.object(ns.urllib.request, "urlopen",
                                        return_value=fake_resp):
            out = ns.call_llm(self._cfg(), "sys", "usr")
        self.assertEqual(out["overview"], "o")

    def test_raises_runtime_error_after_retries(self):
        with unittest.mock.patch.object(
                ns.urllib.request, "urlopen",
                side_effect=OSError("boom")), \
             unittest.mock.patch.object(ns.time, "sleep"):
            with self.assertRaises(RuntimeError):
                ns.call_llm(self._cfg(), "sys", "usr")
