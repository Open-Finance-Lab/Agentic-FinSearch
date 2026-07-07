"""GET /api/signals/news/ behavior (spec §4.4): newest-by-mtime, public
serialization stripping, staleness_hours, tickers filter, conditional GET,
fail-closed 404s."""
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

URL = "/api/signals/news/"

# Bare pytest (no Django test runner, no pytest-django) never calls
# setup_test_environment(), so 'testserver' is not auto-added to ALLOWED_HOSTS
# and CommonMiddleware raises DisallowedHost before URL resolution. CI also
# runs with no .env (DJANGO_DEBUG unset -> False), which defaults
# SECURE_SSL_REDIRECT True and would 301 the plain-HTTP test-client request
# before it reaches the view (the 301-trap, PR #313). Same hermeticity knobs
# as tests/test_ratelimit_429.py and tests/test_debug_route_gating.py.
_HERMETIC = {"ALLOWED_HOSTS": ["testserver"], "SECURE_SSL_REDIRECT": False}


def make_artifact(generated_at, signals=None):
    return {
        "schema_version": 1, "profile": "default",
        "generated_at": generated_at,
        "generator": "news_signals.py/test", "model": "gpt-4o-mini",
        "prompt_version": 1, "source_items": "items-x.jsonl",
        "window_hours": 24, "watchlist": ["AAPL", "MSFT"],
        "status": "ok", "status_reason": None, "news_overview": "quiet",
        "diagnostics": {
            "stories_total": 1, "candidates_dropped_not_subject": 0,
            "near_dups_collapsed": 0, "candidates_selected": 1,
            "tickers_with_candidates": 1, "tickers_no_candidates": 1,
            "tickers_capped": 0, "tickers_omitted_by_llm": 0,
            "tickers_dropped_guid_mismatch": 0, "scores_damped": 0,
        },
        "signals": signals if signals is not None else {
            "MSFT": {"sentiment": "bullish", "score": 0.5, "rationale": "r",
                     "headline": "h", "source": "Reuters",
                     "url": "https://example.com/a", "published": 1783330000.0,
                     "guid": "g1", "n_articles": 2},
        },
    }


class SignalsEndpointTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _write(self, stem, artifact):
        (self.dir / f"signals-{stem}.json").write_text(
            json.dumps(artifact), encoding="utf-8")

    def _recent_iso(self, hours_ago=1.0):
        return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)
                ).isoformat(timespec="seconds")

    def test_unconfigured_dir_404s_fail_closed(self):
        with override_settings(SIGNALS_DIR="", **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_signals"})

    def test_empty_dir_404s(self):
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)

    def test_serves_newest_by_stem_strips_private_adds_staleness(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(30.0)))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for stripped in ("generator", "model", "prompt_version"):
            self.assertNotIn(stripped, body)
        self.assertAlmostEqual(body["staleness_hours"], 1.0, delta=0.2)
        self.assertIn("MSFT", body["signals"])
        self.assertEqual(resp["Cache-Control"], "public, max-age=300")
        self.assertTrue(resp.has_header("ETag"))
        self.assertTrue(resp.has_header("Last-Modified"))

    def test_serves_newest_by_mtime_not_stem_for_same_day_supplemental(self):
        # Same-day supplemental stems (items-<date>-<HHMMSS>.jsonl ->
        # signals-<date>-<HHMMSS>.json) sort lexicographically BEFORE the
        # date-only stem ("." > "-" in ASCII), so a same-day re-run must be
        # selected by mtime, not by stem order (regression guard, F2).
        morning = make_artifact(self._recent_iso(hours_ago=8.0), signals={
            "MSFT": dict(make_artifact("x")["signals"]["MSFT"], guid="morning"),
        })
        supplemental = make_artifact(self._recent_iso(hours_ago=0.1), signals={
            "MSFT": dict(make_artifact("x")["signals"]["MSFT"], guid="supplemental"),
        })
        self._write("2026-07-06", morning)
        supplemental_path = self.dir / "signals-2026-07-06-153042.json"
        supplemental_path.write_text(json.dumps(supplemental), encoding="utf-8")
        # Deterministic mtimes, independent of wall-clock write timing: the
        # date-only stem is strictly OLDER, the supplemental strictly NEWER.
        now = time.time()
        os.utime(self.dir / "signals-2026-07-06.json", (now - 100, now - 100))
        os.utime(supplemental_path, (now, now))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "supplemental")

    def test_tickers_filter_case_insensitive(self):
        art = make_artifact(self._recent_iso(), signals={
            "MSFT": make_artifact("x")["signals"]["MSFT"],
            "AAPL": dict(make_artifact("x")["signals"]["MSFT"], guid="g2"),
        })
        self._write("2026-07-06", art)
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"tickers": "msft,ZZZ"})
        self.assertEqual(list(resp.json()["signals"]), ["MSFT"])

    def test_conditional_get_304(self):
        self._write("2026-07-06", make_artifact(self._recent_iso()))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            first = self.client.get(URL)
            etag = first["ETag"]
            second = self.client.get(URL, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(second.status_code, 304)

    def test_etag_is_variant_specific_to_tickers_filter(self):
        self._write("2026-07-06", make_artifact(self._recent_iso()))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            unfiltered = self.client.get(URL)
            unfiltered_etag = unfiltered["ETag"]
            # Unfiltered ETag must not satisfy a differently-scoped request.
            filtered = self.client.get(URL, {"tickers": "msft"},
                                       HTTP_IF_NONE_MATCH=unfiltered_etag)
            self.assertEqual(filtered.status_code, 200)
            filtered_etag = filtered["ETag"]
            self.assertNotEqual(filtered_etag, unfiltered_etag)
            # The filtered variant's own ETag still revalidates correctly.
            repeat = self.client.get(URL, {"tickers": "msft"},
                                     HTTP_IF_NONE_MATCH=filtered_etag)
        self.assertEqual(repeat.status_code, 304)

    def test_malformed_newest_artifact_404s_fail_closed(self):
        (self.dir / "signals-2026-07-06.json").write_text("{broken",
                                                          encoding="utf-8")
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_signals"})

    def test_post_is_rejected(self):
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.post(URL)
        self.assertEqual(resp.status_code, 405)

    def test_non_dict_signals_404s_fail_closed_even_with_tickers_filter(self):
        art = make_artifact(self._recent_iso())
        art["signals"] = None  # malformed producer output: present but not an object
        self._write("2026-07-06", art)
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            plain = self.client.get(URL)
            filtered = self.client.get(URL, {"tickers": "msft"})
        self.assertEqual(plain.status_code, 404)
        self.assertEqual(filtered.status_code, 404)
        self.assertEqual(filtered.json(), {"error": "no_signals"})
