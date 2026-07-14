"""GET /api/news/items/ behavior (Phase B of the ATL news-signals integration
plan): raw, ungated items-*.jsonl batches served through the PORTED
Heartbeat/news_signals.py validation gate — newest-by-mtime, limit clamping,
conditional GET, fail-closed 404s. Mirrors test_signals_endpoint.py."""
import json
import os
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings

from api import signals_views
from tests.shared_settings import HERMETIC_REQUEST_SETTINGS as _HERMETIC

URL = "/api/news/items/"

CONTRACT_KEYS = {"guid", "title", "link", "source", "published",
                 "description", "tickers", "score"}


def make_item(guid, title="Example headline", link="https://example.com/a",
              source="Reuters", published=None, description="A description.",
              tickers=None, score=0.7, **extra):
    if published is None:
        published = time.time() - 3600  # 1h ago by default
    item = {
        "guid": guid, "title": title, "link": link, "source": source,
        "published": published, "description": description,
        "tickers": tickers if tickers is not None else ["AAPL"],
        "score": score,
    }
    item.update(extra)
    return item


class NewsItemsEndpointTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _write(self, stem, items):
        path = self.dir / f"items-{stem}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item) + "\n")
        return path

    def _recent_epoch(self, hours_ago=1.0):
        return time.time() - hours_ago * 3600

    # 1. unconfigured dir
    def test_unconfigured_dir_404s_fail_closed(self):
        with override_settings(RAW_ITEMS_DIR="", **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_items"})

    # 2. configured empty dir
    def test_empty_dir_404s(self):
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_items"})

    # 3. serves newest batch, default limit, shape, newest-first, 8 keys, headers
    def test_serves_newest_batch_default_limit_shape(self):
        items = [
            make_item("g0", published=self._recent_epoch(0.0), feeds=["yahoo"]),
            make_item("g1", published=self._recent_epoch(1.0)),
            make_item("g2", published=self._recent_epoch(2.0)),
        ]
        self._write("2026-07-06", items)
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body), {"items", "count", "batch"})
        self.assertEqual(body["batch"], "items-2026-07-06.jsonl")
        self.assertEqual(body["count"], 3)
        self.assertEqual([it["guid"] for it in body["items"]], ["g0", "g1", "g2"])
        for it in body["items"]:
            self.assertEqual(set(it), CONTRACT_KEYS)
        self.assertIsInstance(body["items"][0]["published"], float)
        self.assertIsInstance(body["items"][0]["score"], float)
        self.assertIsInstance(body["items"][0]["tickers"], list)
        self.assertEqual(resp["Cache-Control"], "public, max-age=300")
        self.assertTrue(resp.has_header("ETag"))
        self.assertTrue(resp.has_header("Last-Modified"))

    # 4. newest-by-mtime-not-stem regression guard
    def test_serves_newest_by_mtime_not_stem_for_same_day_supplemental(self):
        morning = [make_item("morning", published=self._recent_epoch(8.0))]
        supplemental = [make_item("supplemental", published=self._recent_epoch(0.1))]
        self._write("2026-07-06", morning)
        supp_path = self._write("2026-07-06-153042", supplemental)
        now = time.time()
        os.utime(self.dir / "items-2026-07-06.jsonl", (now - 100, now - 100))
        os.utime(supp_path, (now, now))
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"][0]["guid"], "supplemental")

    # 5. limit handling
    def test_limit_slices_item_count(self):
        items = [make_item(f"g{i}", published=self._recent_epoch(i)) for i in range(5)]
        self._write("2026-07-06", items)
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"limit": "2"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["items"]), 2)
        self.assertEqual(resp.json()["count"], 2)

    def test_limit_absent_defaults_to_50(self):
        items = [make_item(f"g{i}", published=self._recent_epoch(i)) for i in range(5)]
        self._write("2026-07-06", items)
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(len(resp.json()["items"]), 5)  # fewer than 50, all served

    def test_limit_zero_and_negative_clamp_to_one(self):
        items = [make_item(f"g{i}", published=self._recent_epoch(i)) for i in range(3)]
        self._write("2026-07-06", items)
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            zero = self.client.get(URL, {"limit": "0"})
            negative = self.client.get(URL, {"limit": "-5"})
        self.assertEqual(len(zero.json()["items"]), 1)
        self.assertEqual(len(negative.json()["items"]), 1)

    def test_limit_above_200_clamps_to_200(self):
        # Direct unit test of the clamp boundary: fabricating 200+ fixture
        # items to observe the upper clamp via item count would be wasteful;
        # the lower bound and slicing are exercised end-to-end above.
        rf = RequestFactory()
        self.assertEqual(signals_views._parse_limit(rf.get(URL, {"limit": "9999"})), 200)

    # 6. bad limit
    def test_bad_limit_400s(self):
        self._write("2026-07-06", [make_item("g1")])
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"limit": "abc"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "bad_limit"})

    # 7. conditional GET replay
    def test_conditional_get_304(self):
        self._write("2026-07-06", [make_item("g1")])
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            first = self.client.get(URL)
            etag = first["ETag"]
            second = self.client.get(URL, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(second.status_code, 304)

    # 8. ETag varies with limit
    def test_etag_varies_with_limit(self):
        items = [make_item(f"g{i}", published=self._recent_epoch(i)) for i in range(5)]
        self._write("2026-07-06", items)
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            unfiltered = self.client.get(URL)
            limited = self.client.get(URL, {"limit": "2"},
                                      HTTP_IF_NONE_MATCH=unfiltered["ETag"])
            self.assertEqual(limited.status_code, 200)
            limited_etag = limited["ETag"]
            self.assertNotEqual(limited_etag, unfiltered["ETag"])
            repeat = self.client.get(URL, {"limit": "2"},
                                     HTTP_IF_NONE_MATCH=limited_etag)
        self.assertEqual(repeat.status_code, 304)

    # 9. loader called once per request
    def test_items_loaded_from_disk_once_per_request(self):
        self._write("2026-07-06", [make_item("g1")])
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC), \
             mock.patch.object(signals_views, "_load_items",
                               wraps=signals_views._load_items) as loader:
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(loader.call_count, 1)

    # 10. vanishing batch race
    def test_batch_vanishing_between_glob_and_stat_404s_fail_closed(self):
        self._write("2026-07-06", [make_item("g1")])
        real_stat = Path.stat

        def racing_stat(self, **kwargs):
            if self.name.endswith(".jsonl"):
                raise FileNotFoundError(self.name)
            return real_stat(self, **kwargs)

        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC), \
             mock.patch.object(Path, "stat", autospec=True, side_effect=racing_stat):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_items"})

    # 11. method gate
    def test_post_is_rejected(self):
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 405)

    # 12. missing required field: poison pill
    def test_missing_required_field_poisons_batch(self):
        good = make_item("g1")
        bad = make_item("g2")
        del bad["source"]
        path = self.dir / "items-2026-07-06.jsonl"
        path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_items"})

    # 13. malformed JSON line: poison pill
    def test_malformed_json_line_poisons_batch(self):
        path = self.dir / "items-2026-07-06.jsonl"
        path.write_text(json.dumps(make_item("g1")) + "\n{broken\n", encoding="utf-8")
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_items"})

    # 14. bad/out-of-window published: drop story, keep batch
    def test_out_of_window_published_drops_story_keeps_batch(self):
        good = make_item("g1", published=self._recent_epoch(1.0))
        stale = make_item("g2", published=time.time() - 40 * 86400)  # >30d old
        path = self.dir / "items-2026-07-06.jsonl"
        path.write_text(json.dumps(good) + "\n" + json.dumps(stale) + "\n", encoding="utf-8")
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([it["guid"] for it in resp.json()["items"]], ["g1"])

    def test_malformed_numeric_published_drops_story_keeps_batch(self):
        good = make_item("g1", published=self._recent_epoch(1.0))
        bad = make_item("g2", published="not-a-number")
        path = self.dir / "items-2026-07-06.jsonl"
        path.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([it["guid"] for it in resp.json()["items"]], ["g1"])

    # 15. oversized file
    def test_oversized_file_404s(self):
        huge_description = "x" * (11 * 1024 * 1024)  # > _MAX_ITEMS_FILE_MB
        self._write("2026-07-06", [make_item("g1", description=huge_description)])
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_items"})

    # 16. control/bidi + marker + over-cap title: security parity
    def test_control_bidi_and_marker_stripped_title_truncated(self):
        dirty_title = "Evil\u202eTitle" + "NEWS_DATA" + "A" * 600
        self._write("2026-07-06", [make_item("g1", title=dirty_title)])
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        served_title = resp.json()["items"][0]["title"]
        self.assertNotIn("\u202e", served_title)
        self.assertNotIn("NEWS_DATA", served_title)
        self.assertLessEqual(len(served_title), 500)

    # 17. zero valid stories after the gate
    def test_zero_valid_stories_404s(self):
        stale = make_item("g1", published=time.time() - 40 * 86400)
        self._write("2026-07-06", [stale])
        with override_settings(RAW_ITEMS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_items"})
