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

    def test_watchlist_entries_are_uppercased(self):
        import news_signals as ns
        env = {"HEARTBEAT_WATCHLIST": "msft NVDA"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            cfg = ns.load_config()
        self.assertEqual(cfg["watchlist"], ["MSFT", "NVDA"])


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

    def test_published_null_drops_story_not_batch(self):
        bad = make_story(guid="bad", published=None)
        ok = make_story(guid="ok")
        p = write_items(self.td, [bad, ok])
        stories = ns.validation_gate(p, 10)
        self.assertEqual([s["guid"] for s in stories], ["ok"])

    def test_published_non_numeric_string_drops_story_not_batch(self):
        bad = make_story(guid="bad", published="not-a-number")
        ok = make_story(guid="ok")
        p = write_items(self.td, [bad, ok])
        stories = ns.validation_gate(p, 10)
        self.assertEqual([s["guid"] for s in stories], ["ok"])

    def test_score_null_drops_story_not_batch(self):
        bad = make_story(guid="bad", score=None)
        ok = make_story(guid="ok")
        p = write_items(self.td, [bad, ok])
        stories = ns.validation_gate(p, 10)
        self.assertEqual([s["guid"] for s in stories], ["ok"])

    def test_guid_hygiene_strips_marker_token_and_bidi(self):
        s = make_story(guid="g1-NEWS_DATA-\u202e-attack")
        p = write_items(self.td, [s])
        stories = ns.validation_gate(p, 10)
        self.assertNotIn("NEWS_DATA", stories[0]["guid"])
        self.assertNotIn("\u202e", stories[0]["guid"])

    def test_link_hygiene_strips_bidi(self):
        s = make_story(link="https://example.com/\u202eevil")
        p = write_items(self.td, [s])
        stories = ns.validation_gate(p, 10)
        self.assertNotIn("\u202e", stories[0]["link"])


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
        # rather than the ticker "MMM" — but a plain \b3m\b still matches
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
        self.assertTrue(entry["source"].startswith(ns.MARK_OPEN))
        self.assertTrue(entry["source"].endswith(ns.MARK_CLOSE))
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

    def test_non_object_json_content_raises_runtime_error(self):
        payload = {"choices": [{"message": {"content": "[1, 2]"}}]}
        fake_resp = unittest.mock.MagicMock()
        fake_resp.read.return_value = json.dumps(payload).encode()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda s, *a: False
        with unittest.mock.patch.object(ns.urllib.request, "urlopen",
                                        return_value=fake_resp), \
             unittest.mock.patch.object(ns.time, "sleep"):
            with self.assertRaises(RuntimeError):
                ns.call_llm(self._cfg(), "sys", "usr")


def fake_llm_factory(response):
    def fake_llm(cfg, system, user):
        return response
    return fake_llm


class TestValidateResponse(unittest.TestCase):
    def _setup(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            cfg = ns.load_config()
        cands = {"MSFT": [make_story(guid="m1"), make_story(
            guid="m2", title="Microsoft cloud momentum lifts outlook")]}
        n_articles = {"MSFT": 2}
        diag = {"tickers_omitted_by_llm": 0,
                "tickers_dropped_guid_mismatch": 0, "scores_damped": 0}
        return cfg, cands, n_articles, diag

    def test_guid_membership_violation_drops_ticker(self):
        cfg, cands, n, diag = self._setup()
        out = {"overview": "o", "tickers":
               {"MSFT": {"score": 0.5, "guid": "NOT-A-CANDIDATE", "rationale": "r"}}}
        overview, signals = ns.validate_response(out, cands, n, cfg, diag)
        self.assertEqual(signals, {})
        self.assertEqual(diag["tickers_dropped_guid_mismatch"], 1)

    def test_clamp_damp_and_join(self):
        cfg, cands, n, diag = self._setup()
        n["MSFT"] = 1  # under-corroborated
        out = {"overview": "o", "tickers":
               {"MSFT": {"score": 5.0, "guid": "m1", "rationale": "r"}}}
        _, signals = ns.validate_response(out, cands, n, cfg, diag)
        e = signals["MSFT"]
        self.assertEqual(e["score"], 0.7)          # clamped to 1.0 then damped
        self.assertEqual(e["sentiment"], "bullish")
        self.assertEqual(diag["scores_damped"], 1)
        self.assertEqual(e["headline"], "Microsoft raises Azure guidance")
        self.assertEqual(e["url"], "https://example.com/a")   # joined, not LLM text
        self.assertEqual(e["n_articles"], 1)

    def test_omitted_and_malformed_tickers_counted(self):
        cfg, cands, n, diag = self._setup()
        out = {"overview": "o", "tickers":
               {"MSFT": {"score": "not-a-number", "guid": "m1", "rationale": "r"}}}
        _, signals = ns.validate_response(out, cands, n, cfg, diag)
        self.assertEqual(signals, {})
        self.assertEqual(diag["tickers_omitted_by_llm"], 1)


class TestProcessBatch(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.cfg = ns.load_config()
        self.cfg["watchlist"] = ["AAPL", "GOOGL", "MSFT", "NVDA"]

    def test_ok_artifact_shape_and_diagnostics(self):
        p = write_items(self.td, [
            make_story(guid="m1"),
            make_story(guid="roundup", title="Company News for July 6, 2026",
                       tickers=["AAPL", "GOOGL"]),
        ])
        llm = fake_llm_factory({"overview": "calm day", "tickers":
            {"MSFT": {"score": 0.3, "guid": "m1", "rationale": "r"}}})
        artifact = ns.process_batch(p, self.cfg, time.time(), llm=llm)
        self.assertEqual(artifact["schema_version"], 1)
        self.assertEqual(artifact["status"], "ok")
        self.assertEqual(artifact["source_items"], p.name)
        self.assertEqual(list(artifact["signals"]), ["MSFT"])
        d = artifact["diagnostics"]
        self.assertEqual(d["stories_total"], 2)
        self.assertEqual(d["candidates_dropped_not_subject"], 2)
        self.assertEqual(d["candidates_selected"], 1)
        self.assertEqual(d["tickers_no_candidates"], 3)
        self.assertEqual(sorted(d), sorted(DIAG_KEYS))

    def test_llm_failure_yields_degraded_artifact(self):
        p = write_items(self.td, [make_story()])
        def broken_llm(cfg, system, user):
            raise RuntimeError("LLM call failed after 2 attempts: boom")
        artifact = ns.process_batch(p, self.cfg, time.time(), llm=broken_llm)
        self.assertEqual(artifact["status"], "degraded")
        self.assertEqual(artifact["signals"], {})
        self.assertIsNotNone(artifact["status_reason"])

    def test_no_candidates_still_writes_ok_artifact_without_llm(self):
        p = write_items(self.td, [make_story(tickers=["ZZZZ"])])
        def must_not_call(cfg, system, user):
            raise AssertionError("LLM must not be called with zero candidates")
        artifact = ns.process_batch(p, self.cfg, time.time(), llm=must_not_call)
        self.assertEqual(artifact["status"], "ok")
        self.assertEqual(artifact["signals"], {})

    def test_near_dup_collapse_and_damping_compose_end_to_end(self):
        # Regression guard for the composed defense (spec §7.3): near-dup
        # collapse (D9) must run BEFORE damping sees n_articles, so 3 raw
        # copies of one story can never satisfy the corroboration damper.
        p = write_items(self.td, [
            make_story(guid="d1", title="Nvidia unveils next-gen GPU lineup",
                       tickers=["NVDA"], score=6.0),
            make_story(guid="d2", title="Nvidia unveils next-gen GPU lineup!",
                       tickers=["NVDA"], score=5.0),
            make_story(guid="d3", title="Nvidia unveils next-gen GPU lineup.",
                       tickers=["NVDA"], score=4.0),
        ])
        llm = fake_llm_factory({"overview": "o", "tickers":
            {"NVDA": {"score": 0.95, "guid": "d1", "rationale": "r"}}})
        artifact = ns.process_batch(p, self.cfg, time.time(), llm=llm)
        entry = artifact["signals"]["NVDA"]
        self.assertEqual(entry["n_articles"], 1,
                          "3 near-dup copies must collapse to 1 distinct story")
        self.assertEqual(entry["score"], 0.7,
                          "under-corroborated despite 3 raw copies -> damped")
        self.assertEqual(artifact["diagnostics"]["scores_damped"], 1)
        self.assertEqual(artifact["diagnostics"]["near_dups_collapsed"], 2)

    def test_llm_non_dict_tickers_shape_omits_all_candidates(self):
        p = write_items(self.td, [
            make_story(guid="m1", tickers=["MSFT"]),
            make_story(guid="n1", title="Nvidia unveils next-gen GPU",
                       tickers=["NVDA"]),
        ])
        llm = fake_llm_factory({"overview": "o", "tickers": ["MSFT"]})
        artifact = ns.process_batch(p, self.cfg, time.time(), llm=llm)
        self.assertEqual(artifact["status"], "ok")
        self.assertEqual(artifact["signals"], {})
        self.assertEqual(artifact["diagnostics"]["tickers_omitted_by_llm"],
                         artifact["diagnostics"]["tickers_with_candidates"])

    def test_process_batch_joins_via_cleaned_guid(self):
        raw_guid = "g1-NEWS_DATA-\u202e-attack"
        p = write_items(self.td, [make_story(guid=raw_guid)])
        cleaned_guid = ns.clean_text(raw_guid, 200)
        llm = fake_llm_factory({"overview": "o", "tickers":
            {"MSFT": {"score": 0.3, "guid": cleaned_guid, "rationale": "r"}}})
        artifact = ns.process_batch(p, self.cfg, time.time(), llm=llm)
        self.assertEqual(artifact["status"], "ok")
        self.assertIn("MSFT", artifact["signals"])
        self.assertEqual(artifact["signals"]["MSFT"]["guid"], cleaned_guid)


def make_cfg(home: Path):
    with unittest.mock.patch.dict(os.environ, {}, clear=True):
        cfg = ns.load_config()
    cfg.update(home=home, digests=home / "digests", signals_dir=home / "signals",
               state_path=home / "signals_state.json", api_key="test-key",
               watchlist=["AAPL", "GOOGL", "MSFT", "NVDA"])
    cfg["digests"].mkdir(parents=True, exist_ok=True)
    return cfg


OK_LLM = fake_llm_factory({"overview": "o", "tickers":
    {"MSFT": {"score": 0.3, "guid": "g1", "rationale": "r"}}})


class TestAtomicWrite(unittest.TestCase):
    def test_write_json_atomic_replaces_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "signals-2026-07-06.json"
            path.write_text('{"old": true}', encoding="utf-8")
            ns.write_json_atomic({"new": True}, path)
            self.assertEqual(json.loads(path.read_text()), {"new": True})
            leftovers = [p.name for p in Path(td).iterdir() if p.name != path.name]
            self.assertEqual(leftovers, [], "temp file must not survive the write")


class TestSweep(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.cfg = make_cfg(self.home)

    def test_sweep_processes_each_batch_exactly_once(self):
        write_items(self.cfg["digests"], [make_story()], name="items-a.jsonl")
        write_items(self.cfg["digests"], [make_story()], name="items-b.jsonl")
        calls = []
        def counting_llm(cfg, system, user):
            calls.append(1)
            return {"overview": "o", "tickers":
                    {"MSFT": {"score": 0.3, "guid": "g1", "rationale": "r"}}}
        self.assertEqual(ns.run_sweep(self.cfg, llm=counting_llm), 0)
        self.assertEqual(len(calls), 2)
        self.assertTrue((self.cfg["signals_dir"] / "signals-a.json").exists())
        self.assertTrue((self.cfg["signals_dir"] / "signals-b.json").exists())
        self.assertEqual(ns.run_sweep(self.cfg, llm=counting_llm), 0)
        self.assertEqual(len(calls), 2, "second sweep must be a no-op")

    def test_poison_pill_records_error_continues_and_exits_zero(self):
        bad = self.cfg["digests"] / "items-2026-07-05.jsonl"
        bad.write_text('{"broken\n', encoding="utf-8")
        write_items(self.cfg["digests"], [make_story()],
                    name="items-2026-07-06.jsonl")
        self.assertEqual(ns.run_sweep(self.cfg, llm=OK_LLM), 0)
        state = json.loads(self.cfg["state_path"].read_text())
        self.assertEqual(state["items-2026-07-05.jsonl"]["status"],
                         "processed-with-error")
        self.assertEqual(state["items-2026-07-06.jsonl"]["status"], "ok")
        self.assertFalse((self.cfg["signals_dir"] / "signals-2026-07-05.json").exists())
        self.assertTrue((self.cfg["signals_dir"] / "signals-2026-07-06.json").exists())

    def test_artifact_written_before_state_crash_means_reprocess(self):
        write_items(self.cfg["digests"], [make_story()])
        calls = []
        def counting_llm(cfg, system, user):
            calls.append(1)
            return {"overview": "o", "tickers":
                    {"MSFT": {"score": 0.3, "guid": "g1", "rationale": "r"}}}
        with unittest.mock.patch.object(
                ns, "save_state_atomic",
                side_effect=OSError("simulated crash after artifact write")):
            with self.assertRaises(OSError):
                ns.run_sweep(self.cfg, llm=counting_llm)
        self.assertTrue(
            (self.cfg["signals_dir"] / "signals-2026-07-06.json").exists(),
            "artifact must land before the state write (spec §6.2)")
        self.assertEqual(ns.run_sweep(self.cfg, llm=counting_llm), 0)
        self.assertEqual(len(calls), 2,
                         "crashed batch is reprocessed — duplicate call, never a gap")

    def test_degraded_batch_is_marked_processed(self):
        write_items(self.cfg["digests"], [make_story()])
        def broken_llm(cfg, system, user):
            raise RuntimeError("boom")
        self.assertEqual(ns.run_sweep(self.cfg, llm=broken_llm), 0)
        state = json.loads(self.cfg["state_path"].read_text())
        self.assertEqual(state["items-2026-07-06.jsonl"]["status"], "degraded")
        artifact = json.loads(
            (self.cfg["signals_dir"] / "signals-2026-07-06.json").read_text())
        self.assertEqual(artifact["status"], "degraded")

    def test_sweep_survives_llm_returning_non_dict_tickers_shape(self):
        write_items(self.cfg["digests"], [make_story()])
        bad_shape_llm = fake_llm_factory({"overview": "o", "tickers": ["MSFT"]})
        self.assertEqual(ns.run_sweep(self.cfg, llm=bad_shape_llm), 0)
        state = json.loads(self.cfg["state_path"].read_text())
        self.assertEqual(state["items-2026-07-06.jsonl"]["status"], "ok")
        artifact = json.loads(
            (self.cfg["signals_dir"] / "signals-2026-07-06.json").read_text())
        self.assertEqual(artifact["status"], "ok")
        self.assertEqual(artifact["signals"], {})


import fcntl


class TestPostDiscord(unittest.TestCase):
    def test_post_discord_sends_bot_auth_and_truncated_body(self):
        fake_resp = unittest.mock.MagicMock()
        fake_resp.read.return_value = b"{}"
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda s, *a: False
        with unittest.mock.patch.object(ns.urllib.request, "urlopen",
                                        return_value=fake_resp) as mock_urlopen:
            ns.post_discord("tok", "chan123", "x" * 3000)
        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.full_url,
                         "https://discord.com/api/v10/channels/chan123/messages")
        self.assertEqual(req.get_header("Authorization"), "Bot tok")
        body = json.loads(req.data)
        self.assertEqual(len(body["content"]), 1900)


class TestCanary(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.cfg = make_cfg(self.home)
        self.cfg["signals_dir"].mkdir(parents=True, exist_ok=True)

    def test_fresh_artifact_passes(self):
        (self.cfg["signals_dir"] / "signals-x.json").write_text("{}")
        self.assertEqual(ns.run_canary(self.cfg), 0)

    def test_no_artifact_is_stale_and_pings_discord(self):
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_CHANNEL_ID": "c"}
        with unittest.mock.patch.dict(os.environ, env), \
             unittest.mock.patch.object(ns, "post_discord") as post:
            self.assertEqual(ns.run_canary(self.cfg), 1)
        post.assert_called_once()
        self.assertIn("stale", post.call_args.args[2])

    def test_old_artifact_is_stale_without_creds_no_ping(self):
        p = self.cfg["signals_dir"] / "signals-old.json"
        p.write_text("{}")
        old = time.time() - 31 * 3600
        os.utime(p, (old, old))
        with unittest.mock.patch.dict(os.environ, {}, clear=True), \
             unittest.mock.patch.object(ns, "post_discord") as post:
            self.assertEqual(ns.run_canary(self.cfg), 1)
        post.assert_not_called()


class TestMain(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _env(self, **extra):
        env = {"SIGNALS_HOME": str(self.home), "OPENAI_API_KEY": "k"}
        env.update(extra)
        return unittest.mock.patch.dict(os.environ, env, clear=True)

    def test_empty_sweep_exits_zero(self):
        (self.home / "digests").mkdir(parents=True)
        with self._env():
            self.assertEqual(ns.main([]), 0)

    def test_missing_api_key_exits_two(self):
        (self.home / "digests").mkdir(parents=True)
        with self._env(OPENAI_API_KEY=""):
            self.assertEqual(ns.main([]), 2)

    def test_held_lock_exits_three(self):
        (self.home / "signals").mkdir(parents=True)
        holder = (self.home / "signals" / ".lock").open("w")
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self._env():
            self.assertEqual(ns.main([]), 3)

    def test_canary_flag_dispatches(self):
        (self.home / "signals").mkdir(parents=True)
        (self.home / "signals" / "signals-x.json").write_text("{}")
        with self._env():
            self.assertEqual(ns.main(["--canary"]), 0)

    def test_missing_env_file_exits_two(self):
        missing = self.home / "does-not-exist.env"
        with self.assertRaises(SystemExit) as ctx:
            ns.main(["--env-file", str(missing)])
        self.assertEqual(ctx.exception.code, 2)


class TestFixture(unittest.TestCase):
    def test_committed_fixture_matches_regeneration(self):
        fixtures = Path(__file__).parent / "fixtures"
        sys.path.insert(0, str(fixtures))
        try:
            import make_signals_fixture
        finally:
            sys.path.pop(0)
        committed = json.loads(
            (fixtures / "signals-fixture.json").read_text(encoding="utf-8"))
        self.assertEqual(make_signals_fixture.build(), committed,
                         "pipeline semantics changed — regenerate the fixture "
                         "deliberately: python3 tests/fixtures/make_signals_fixture.py")
