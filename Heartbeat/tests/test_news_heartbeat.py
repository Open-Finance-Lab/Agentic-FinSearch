"""Tests for the news heartbeat pipeline.

Fixtures under tests/fixtures/ are real captures from the 2026-06-10 droplet
probes of live Yahoo Finance endpoints (single-line XML, both date schemas).
"""

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_heartbeat as nh

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = 1781100000.0  # 2026-06-10 ~04:40 UTC, shortly after fixtures were captured


def fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def story(**kw):
    base = {
        "guid": "g1",
        "title": "A title",
        "link": "https://finance.yahoo.com/news/a-title-123.html",
        "source": "Reuters",
        "published": NOW - 3600,
        "description": "First sentence. Second sentence.",
        "tickers": [],
        "feeds": ["topstories"],
    }
    base.update(kw)
    return base


def bulk_digest(n=40):
    """A digest bulky enough to force multi-message Discord packing."""
    stories = [story(guid=f"g{i}", title=("Long headline %d " % i) + "x" * 120,
                     link=f"https://finance.yahoo.com/news/long-{i}.html",
                     score=5.0) for i in range(n)]
    digest = {"overview": "O" * 500,
              "sections": [{"title": "Market Pulse",
                            "items": [{"guid": f"g{i}", "summary": "S" * 300}
                                      for i in range(n)]}]}
    return digest, {s["guid"]: s for s in stories}


def all_descs(messages):
    return "".join(e.get("description", "")
                   for m in messages for e in m["embeds"])


class TestParseRss(unittest.TestCase):
    def test_parses_market_feed_fixture(self):
        stories = nh.parse_rss(fx("topstories.xml"), feed="topstories")
        self.assertGreater(len(stories), 30)  # single-line XML must not break parsing
        for s in stories:
            self.assertTrue(s["guid"])
            self.assertTrue(s["title"])
            self.assertTrue(s["link"].startswith("http"))
            self.assertGreater(s["published"], 1700000000)  # ISO-8601 parsed
            self.assertEqual(s["feeds"], ["topstories"])

    def test_market_feed_has_source_attribution(self):
        stories = nh.parse_rss(fx("rssindex.xml"), feed="rssindex")
        sources = {s["source"] for s in stories}
        self.assertIn("TheStreet", sources)  # real <source> tag from fixture

    def test_parses_per_ticker_fixture_with_descriptions(self):
        stories = nh.parse_rss(fx("aapl2.xml"), feed="ticker:AAPL", ticker="AAPL")
        self.assertEqual(len(stories), 20)
        self.assertTrue(all(s["tickers"] == ["AAPL"] for s in stories))
        # RFC-822 pubDate parsed
        self.assertTrue(all(s["published"] > 1700000000 for s in stories))
        # the per-ticker feed is the only one with descriptions
        self.assertTrue(sum(1 for s in stories if s["description"]) >= 10)

    def test_per_ticker_sources_are_publisher_names_not_domains(self):
        stories = nh.parse_rss(fx("nvda2.xml"), feed="ticker:NVDA", ticker="NVDA")
        sources = {s["source"] for s in stories}
        self.assertTrue(all(s["source"] for s in stories))
        # the canonical map must rewrite known feed domains to publisher names
        self.assertNotIn("fool.com", sources)
        self.assertNotIn("finance.yahoo.com", sources)
        # Yahoo-hosted syndications (/m/ links) are credited honestly
        m_links = [s for s in stories if "/m/" in s["link"]]
        if m_links:
            self.assertTrue(all(s["source"] == "via Yahoo Finance" for s in m_links))

    def test_tsrc_suffix_kept_on_yahoo_stripped_elsewhere(self):
        xml = fx("aapl2.xml")
        stories = nh.parse_rss(xml, feed="ticker:AAPL", ticker="AAPL")
        for s in stories:
            if not nh._yahoo_host(nh._host(s["link"])):
                self.assertNotIn(".tsrc=rss", s["link"])

    def test_naive_dates_assumed_utc(self):
        # zoneless ISO timestamp must be read as UTC, not droplet-local time
        self.assertEqual(nh._parse_date("2026-06-09T00:55:00"),
                         nh._parse_date("2026-06-09T00:55:00Z"))

    def test_bad_xml_returns_empty(self):
        self.assertEqual(nh.parse_rss("<html>sad panda</html>", feed="x"), [])
        self.assertEqual(nh.parse_rss("", feed="x"), [])

    def test_roundup_description_blanked_at_parse(self):
        # live-blog descriptions drift to other stories; blanking must happen
        # once at parse so scoring, the extractive fallback, and the LLM all
        # see the same truth
        xml = ("<rss><channel><item>"
               "<title>Stock Market Today: Dow Slips As Tech Rallies</title>"
               "<link>https://finance.yahoo.com/news/stock-market-today-1.html</link>"
               "<description>Tesla shares jumped 5% after deliveries beat."
               "</description></item></channel></rss>")
        stories = nh.parse_rss(xml, feed="topstories")
        self.assertEqual(stories[0]["description"], "")

    def test_rejects_doctype_and_entities(self):
        # XXE / billion-laughs guard: real Yahoo feeds never carry a DTD
        evil = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "ha">]>'
                "<rss><channel><item><title>&lol;</title>"
                "<link>http://x</link></item></channel></rss>")
        self.assertEqual(nh.parse_rss(evil, feed="x"), [])


class TestMerge(unittest.TestCase):
    def test_dedupes_by_guid_and_unions_metadata(self):
        a = story(guid="dup", feeds=["topstories"], tickers=["AAPL"],
                  description="")  # market feeds carry no description
        b = story(guid="dup", feeds=["ticker:AAPL"], tickers=["NVDA"],
                  description="Only b has this.")
        merged = nh.merge_stories([[a], [b]])
        self.assertEqual(len(merged), 1)
        self.assertEqual(sorted(merged[0]["feeds"]), ["ticker:AAPL", "topstories"])
        self.assertEqual(sorted(merged[0]["tickers"]), ["AAPL", "NVDA"])
        self.assertEqual(merged[0]["description"], "Only b has this.")

    def test_real_market_feeds_overlap(self):
        a = nh.parse_rss(fx("topstories.xml"), feed="topstories")
        b = nh.parse_rss(fx("rssindex.xml"), feed="rssindex")
        merged = nh.merge_stories([a, b])
        self.assertLess(len(merged), len(a) + len(b))  # ~70% known overlap
        self.assertGreater(len(merged), max(len(a), len(b)))


class TestNearDups(unittest.TestCase):
    def test_collapses_near_identical_titles(self):
        a = story(guid="a", title="Apple unveils new AI chip at WWDC event",
                  score=5.0, tickers=["AAPL"])
        b = story(guid="b", title="Apple unveils its new AI chip at WWDC",
                  score=3.0, tickers=["NVDA"])
        c = story(guid="c", title="Fed holds rates steady in June meeting", score=4.0)
        out = nh.collapse_near_dups([a, b, c])
        self.assertEqual(len(out), 2)
        kept = next(s for s in out if "Apple" in s["title"])
        self.assertEqual(kept["guid"], "a")  # higher score survives
        self.assertEqual(sorted(kept["tickers"]), ["AAPL", "NVDA"])  # merged


class TestWindowAndState(unittest.TestCase):
    def test_drops_old_and_seen(self):
        fresh = story(guid="fresh", published=NOW - 3600)
        old = story(guid="old", published=NOW - 90000)  # >24h
        seen = story(guid="seen", published=NOW - 60)
        out = nh.filter_window([fresh, old, seen], now=NOW, hours=24,
                               seen={"seen": NOW - 99})
        self.assertEqual([s["guid"] for s in out], ["fresh"])

    def test_prune_state(self):
        state = {"keep": NOW - 86400, "drop": NOW - 86400 * 8}
        out = nh.prune_state(state, now=NOW, max_age_days=7)
        self.assertEqual(list(out), ["keep"])


class TestScoring(unittest.TestCase):
    def test_listicle_penalized_below_market_news(self):
        listicle = story(guid="l", title="What does renters insurance cover?",
                         source="Yahoo Personal Finance")
        macro = story(guid="m", title="Fed signals rate cut as inflation cools",
                      source="Reuters", feeds=["topstories", "rssindex"])
        ranked = nh.rank_stories([listicle, macro], watchlist=["AAPL"], now=NOW)
        self.assertEqual(ranked[0]["guid"], "m")
        self.assertGreater(ranked[0]["score"], ranked[1]["score"] + 2)

    def test_corroboration_and_watchlist_boost(self):
        plain = story(guid="p", title="Company expands office space", source="Zacks")
        boosted = story(guid="b", title="Company expands data centers",
                        source="Zacks", tickers=["NVDA"],
                        feeds=["topstories", "ticker:NVDA"])
        ranked = nh.rank_stories([plain, boosted], watchlist=["NVDA"], now=NOW)
        self.assertEqual(ranked[0]["guid"], "b")


class TestExtractiveFallback(unittest.TestCase):
    def test_digest_structure_without_llm(self):
        stories = []
        for i in range(12):
            stories.append(story(
                guid=f"m{i}", title=f"Market story {i} on earnings and rates",
                description=f"Market detail {i}. Extra.", score=10.0 - i))
        for i in range(4):
            stories.append(story(
                guid=f"c{i}", title=f"NVDA story {i} beats guidance",
                tickers=["NVDA"], description=f"Company detail {i}. More.",
                score=8.0 - i))
        digest = nh.extractive_digest(stories, now=NOW)
        self.assertTrue(digest["overview"])
        titles = [sec["title"] for sec in digest["sections"]]
        self.assertEqual(len(titles), 2)
        market_sec = digest["sections"][0]
        self.assertLessEqual(len(market_sec["items"]), 8)
        for item in market_sec["items"]:
            self.assertTrue(item["summary"])
            self.assertNotIn("\n", item["summary"])

    def test_summary_is_quoted_first_sentence_of_description(self):
        s = story(guid="x", description="Lead sentence here. Trailing detail.",
                  score=1.0)
        digest = nh.extractive_digest([s], now=NOW)
        item = digest["sections"][0]["items"][0]
        # excerpted publisher prose must be visibly marked as a quotation
        self.assertEqual(item["summary"], "“Lead sentence here.”")

    def test_first_sentence_handles_finance_abbreviations(self):
        self.assertEqual(
            nh._first_sentence("U.S. stocks rallied Friday. More detail."),
            "U.S. stocks rallied Friday.")


class TestLlmValidation(unittest.TestCase):
    GOOD = {
        "overview": "Markets were mixed.",
        "sections": [
            {"title": "Market Pulse",
             "items": [{"guid": "g1", "summary": "A fine summary."}]},
        ],
    }

    def test_accepts_valid_payload(self):
        out = nh.validate_llm_digest(self.GOOD, valid_guids={"g1"})
        self.assertIsNotNone(out)
        self.assertEqual(out["sections"][0]["items"][0]["guid"], "g1")

    def test_rejects_unknown_guid_items_but_keeps_rest(self):
        payload = json.loads(json.dumps(self.GOOD))
        payload["sections"][0]["items"].append(
            {"guid": "hallucinated", "summary": "Made up."})
        out = nh.validate_llm_digest(payload, valid_guids={"g1"})
        self.assertEqual(len(out["sections"][0]["items"]), 1)

    def test_rejects_garbage(self):
        self.assertIsNone(nh.validate_llm_digest({"nope": 1}, valid_guids={"g1"}))
        self.assertIsNone(nh.validate_llm_digest(None, valid_guids={"g1"}))
        empty = {"overview": "x", "sections": [{"title": "t", "items": [
            {"guid": "hallucinated", "summary": "y"}]}]}
        self.assertIsNone(nh.validate_llm_digest(empty, valid_guids={"g1"}))


class TestRender(unittest.TestCase):
    def _digest_and_index(self, n=3):
        stories = [story(guid=f"g{i}", title=f"Story {i} about earnings",
                         link=f"https://finance.yahoo.com/news/story-{i}.html",
                         score=5.0) for i in range(n)]
        digest = {
            "overview": "A calm day in the markets.",
            "sections": [{"title": "Market Pulse", "items": [
                {"guid": f"g{i}", "summary": f"Summary {i}."} for i in range(n)
            ]}],
        }
        return digest, {s["guid"]: s for s in stories}

    def test_markdown_has_links_sources_and_disclaimer(self):
        digest, idx = self._digest_and_index()
        md = nh.render_markdown(digest, idx, now=NOW)
        self.assertIn("https://finance.yahoo.com/news/story-0.html", md)
        self.assertIn("Reuters", md)
        self.assertIn(nh.DISCLAIMER, md)
        self.assertIn("A calm day in the markets.", md)

    def test_discord_messages_respect_limits(self):
        digest, idx = bulk_digest(40)
        messages = nh.discord_messages(digest, idx)
        self.assertTrue(messages)
        for msg in messages:
            embeds = msg["embeds"]
            self.assertLessEqual(len(embeds), 10)
            total = 0
            for e in embeds:
                desc = e.get("description", "")
                self.assertLessEqual(len(desc), 4096)
                total += len(e.get("title", "")) + len(desc)
                total += len(e.get("footer", {}).get("text", ""))
            self.assertLessEqual(total, 6000)
        # every item must appear exactly once across all messages
        joined = all_descs(messages)
        for i in range(40):
            self.assertIn(f"long-{i}.html", joined)

    def test_masked_links_in_embeds(self):
        digest, idx = self._digest_and_index(1)
        descs = all_descs(nh.discord_messages(digest, idx))
        self.assertIn("](https://finance.yahoo.com/news/story-0.html)", descs)


class TestSecurityHardening(unittest.TestCase):
    def test_masked_link_injection_is_neutralized(self):
        evil = story(guid="e",
                     title="Good News ](https://evil.example/phish) click",
                     link="https://finance.yahoo.com/news/real.html", score=5.0)
        digest = {"overview": "Day ](https://evil.example/o) end.",
                  "sections": [{"title": "Market Pulse", "items": [
                      {"guid": "e", "summary": "Sum ](https://evil.example/s) x."}]}]}
        idx = {"e": evil}
        descs = all_descs(nh.discord_messages(digest, idx))
        md = nh.render_markdown(digest, idx, now=NOW)
        for rendered in (descs, md):
            self.assertNotIn("](https://evil.example", rendered)
        self.assertIn("](https://finance.yahoo.com/news/real.html)", descs)

    def test_messages_suppress_mentions(self):
        digest = {"overview": "o", "sections": [{"title": "t", "items": [
            {"guid": "g", "summary": "s"}]}]}
        msgs = nh.discord_messages(digest, {"g": story(guid="g")})
        for m in msgs:
            self.assertEqual(m["allowed_mentions"], {"parse": []})

    def test_og_host_allowlist(self):
        ok = nh._og_host_ok
        self.assertTrue(ok("https://finance.yahoo.com/news/x.html"))
        self.assertFalse(ok("http://169.254.169.254/meta?finance.yahoo.com"))
        self.assertFalse(ok("http://localhost:8000/finance.yahoo.com"))
        self.assertFalse(ok("https://evil.example/finance.yahoo.com/x"))
        self.assertFalse(ok("ftp://finance.yahoo.com/x"))
        self.assertFalse(ok("https://notyahoo.com/x"))


class TestRobustness(unittest.TestCase):
    def test_llm_content_extraction_rejects_non_string(self):
        wrap = lambda c: {"choices": [{"message": {"content": c}}]}
        self.assertIsNone(nh._extract_llm_content(wrap(None)))
        self.assertIsNone(nh._extract_llm_content(wrap(["part"])))
        self.assertIsNone(nh._extract_llm_content({}))
        self.assertEqual(nh._extract_llm_content(wrap(" x ")), " x ")

    def test_validator_dedupes_guid_across_sections(self):
        payload = {"overview": "o", "sections": [
            {"title": "A", "items": [{"guid": "g1", "summary": "a"}]},
            {"title": "B", "items": [{"guid": "g1", "summary": "b"}]}]}
        out = nh.validate_llm_digest(payload, valid_guids={"g1"})
        total = sum(len(s["items"]) for s in out["sections"])
        self.assertEqual(total, 1)

    def test_validator_caps_summary_length(self):
        payload = {"overview": "o", "sections": [
            {"title": "A", "items": [{"guid": "g1", "summary": "word " * 200}]}]}
        out = nh.validate_llm_digest(payload, valid_guids={"g1"})
        self.assertLessEqual(len(out["sections"][0]["items"][0]["summary"]), 500)

    def test_seen_updates_exclude_future_and_undated(self):
        fresh = story(guid="fresh", published=NOW - 3600)
        old = story(guid="old", published=NOW - 90000)
        future = story(guid="future", published=NOW + 7200)
        undated = story(guid="undated", published=0.0)
        seen = nh.compute_seen_updates(
            merged=[fresh, old, future, undated], fresh=[fresh],
            now=NOW, hours=24)
        self.assertIn("fresh", seen)
        self.assertIn("old", seen)       # safely tombstoned, outside window
        self.assertNotIn("future", seen)  # must get a chance next beat
        self.assertNotIn("undated", seen)

    def test_footer_disclaimer_on_every_message(self):
        digest, idx = bulk_digest(40)
        msgs = nh.discord_messages(digest, idx)
        self.assertGreater(len(msgs), 1)
        for m in msgs:
            for e in m["embeds"]:
                self.assertEqual(e["footer"]["text"], nh.DISCLAIMER)


class TestEnrichment(unittest.TestCase):
    def test_enrich_skips_offsite_and_roundups_without_burning_cap(self):
        calls = []

        def fake_fetch(url, **kw):
            calls.append(url)
            return '<meta property="og:description" content="OG summary">'

        class BoomOpener:  # any direct (non-fetch_url) network path must fail
            def open(self, *a, **kw):
                raise AssertionError("unexpected direct network call")

        offsite = story(guid="o", description="", link="https://example.com/x")
        roundup = story(guid="r", description="",
                        title="Stock Market Today: Live Updates",
                        link="https://finance.yahoo.com/news/r.html")
        normal = story(guid="n", description="",
                       link="https://finance.yahoo.com/news/n.html")
        real_fetch, real_opener = nh.fetch_url, nh._NO_REDIRECT_OPENER
        nh.fetch_url, nh._NO_REDIRECT_OPENER = fake_fetch, BoomOpener()
        try:
            nh.enrich_descriptions([offsite, roundup, normal], cap=1, pause=0)
        finally:
            nh.fetch_url, nh._NO_REDIRECT_OPENER = real_fetch, real_opener
        # neither the off-allowlist link nor the drifting roundup may consume
        # the enrichment budget; the one slot goes to the eligible story
        self.assertEqual(calls, ["https://finance.yahoo.com/news/n.html"])
        self.assertEqual(normal["description"], "OG summary")
        self.assertEqual(offsite["description"], "")
        self.assertEqual(roundup["description"], "")


class TestFakeAliveDetection(unittest.TestCase):
    def test_clone_of_market_feed_is_flagged(self):
        market = [story(guid=f"g{i}") for i in range(20)]
        clone = [story(guid=f"g{i}", tickers=["AAPL"]) for i in range(19)]
        legit = [story(guid=f"t{i}", tickers=["AAPL"]) for i in range(20)]
        market_guids = {s["guid"] for s in market}
        self.assertTrue(nh.looks_like_market_clone(clone, market_guids))
        self.assertFalse(nh.looks_like_market_clone(legit, market_guids))


if __name__ == "__main__":
    unittest.main()
