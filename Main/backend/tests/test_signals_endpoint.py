"""GET /api/signals/news/ behavior (spec §4.4): newest-by-mtime, public
serialization stripping, staleness_hours, tickers filter, conditional GET,
fail-closed 404s."""
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from api import signals_views

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

    def test_last_modified_only_on_unfiltered_variant(self):
        # Last-Modified (generated_at) is identical across every tickers
        # variant of one artifact, so it can only be emitted where it
        # uniquely identifies the variant: the unfiltered response.
        self._write("2026-07-06", make_artifact(self._recent_iso()))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            unfiltered = self.client.get(URL)
            filtered = self.client.get(URL, {"tickers": "msft"})
        self.assertTrue(unfiltered.has_header("Last-Modified"))
        self.assertFalse(filtered.has_header("Last-Modified"))

    def test_if_modified_since_revalidates_unfiltered_but_never_filtered(self):
        # RFC 9110: with no If-None-Match, the server answers from
        # If-Modified-Since alone and never consults the ETag — so a 304
        # here would tell the client to reuse a differently-filtered body.
        self._write("2026-07-06", make_artifact(self._recent_iso()))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            first = self.client.get(URL)
            ims = first["Last-Modified"]
            unfiltered = self.client.get(URL, HTTP_IF_MODIFIED_SINCE=ims)
            filtered = self.client.get(URL, {"tickers": "msft"},
                                       HTTP_IF_MODIFIED_SINCE=ims)
        self.assertEqual(unfiltered.status_code, 304)
        self.assertEqual(filtered.status_code, 200)

    def test_etag_is_stable_across_tickers_order_whitespace_and_dupes(self):
        # The view filters on a normalized set; the ETag must key on the
        # same normalization or equivalent requests never revalidate.
        art = make_artifact(self._recent_iso(), signals={
            "MSFT": make_artifact("x")["signals"]["MSFT"],
            "AAPL": dict(make_artifact("x")["signals"]["MSFT"], guid="g2"),
        })
        self._write("2026-07-06", art)
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            first = self.client.get(URL, {"tickers": "MSFT,AAPL"})
            second = self.client.get(URL, {"tickers": " aapl , msft ,msft"})
            self.assertEqual(first["ETag"], second["ETag"])
            revalidated = self.client.get(URL, {"tickers": "AAPL,MSFT"},
                                          HTTP_IF_NONE_MATCH=first["ETag"])
        self.assertEqual(revalidated.status_code, 304)

    def test_etag_distinguishes_literal_plus_token_from_plus_joined_list(self):
        # %2B decodes to a literal '+' inside one token; the ETag's '+' join
        # must not let {'AAPL','MSFT'} and the single token 'AAPL+MSFT'
        # collide, or a 304 would point a client (or the shared proxy cache
        # behind Cache-Control: public) at a differently-filtered body.
        art = make_artifact(self._recent_iso(), signals={
            "MSFT": make_artifact("x")["signals"]["MSFT"],
            "AAPL": dict(make_artifact("x")["signals"]["MSFT"], guid="g2"),
        })
        self._write("2026-07-06", art)
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            joined = self.client.get(URL, {"tickers": "AAPL,MSFT"})
            literal = self.client.get(URL, {"tickers": "AAPL+MSFT"})
        self.assertEqual(len(joined.json()["signals"]), 2)
        self.assertEqual(literal.json()["signals"], {})
        self.assertNotEqual(joined["ETag"], literal["ETag"])

    def test_artifact_loaded_from_disk_once_per_request(self):
        # @condition calls _etag and _last_modified before the view runs;
        # without per-request memoization one GET pays 3x glob+stat+read.
        self._write("2026-07-06", make_artifact(self._recent_iso()))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC), \
             mock.patch.object(signals_views, "_load_latest",
                               wraps=signals_views._load_latest) as loader:
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(loader.call_count, 1)

    def test_artifact_vanishing_between_glob_and_stat_404s_fail_closed(self):
        # A retention job (or manual cleanup) can unlink a candidate after
        # glob() lists it and before max() stat()s it — that race must fail
        # closed to 404, never surface as a 500.
        self._write("2026-07-06", make_artifact(self._recent_iso()))
        real_stat = Path.stat

        def racing_stat(self, **kwargs):
            if self.name.endswith(".json"):
                raise FileNotFoundError(self.name)
            return real_stat(self, **kwargs)

        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC), \
             mock.patch.object(Path, "stat", autospec=True,
                               side_effect=racing_stat):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_signals"})

    def test_naive_generated_at_404s_fail_closed(self):
        # fromisoformat accepts tz-naive strings; subtracting one from the
        # aware now() would raise TypeError in the view — the validator must
        # reject a naive generated_at up front.
        naive = datetime.now().isoformat(timespec="seconds")
        self._write("2026-07-06", make_artifact(naive))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_signals"})

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
