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
from tests.shared_settings import HERMETIC_REQUEST_SETTINGS as _HERMETIC

URL = "/api/signals/news/"

# The single MSFT signal make_artifact() emits by default; tests composing
# custom `signals` dicts copy it instead of round-tripping a throwaway
# make_artifact() call just to reach one nested literal.
DEFAULT_SIGNAL = {"sentiment": "bullish", "sentiment_score": 0.5, "rationale": "r",
                  "headline": "h", "source": "Reuters",
                  "url": "https://example.com/a", "published": 1783330000.0,
                  "guid": "g1", "n_articles": 2}


def make_artifact(generated_at, signals=None):
    return {
        "schema_version": 2, "profile": "default",
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
            "MSFT": dict(DEFAULT_SIGNAL),
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
        self.assertEqual(body["schema_version"], 2)
        self.assertNotIn("score", body["signals"]["MSFT"])

    def test_serves_newest_by_mtime_not_stem_for_same_day_supplemental(self):
        # Same-day supplemental stems (items-<date>-<HHMMSS>.jsonl ->
        # signals-<date>-<HHMMSS>.json) sort lexicographically BEFORE the
        # date-only stem ("." > "-" in ASCII), so a same-day re-run must be
        # selected by mtime, not by stem order (regression guard, F2).
        morning = make_artifact(self._recent_iso(hours_ago=8.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="morning"),
        })
        supplemental = make_artifact(self._recent_iso(hours_ago=0.1), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="supplemental"),
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
            "MSFT": dict(DEFAULT_SIGNAL),
            "AAPL": dict(DEFAULT_SIGNAL, guid="g2"),
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
            "MSFT": dict(DEFAULT_SIGNAL),
            "AAPL": dict(DEFAULT_SIGNAL, guid="g2"),
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
            "MSFT": dict(DEFAULT_SIGNAL),
            "AAPL": dict(DEFAULT_SIGNAL, guid="g2"),
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
             mock.patch.object(signals_views, "_load_artifact",
                               wraps=signals_views._load_artifact) as loader:
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

    def test_as_of_serves_that_days_artifact(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d05")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d06")}))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d05")

    def test_as_of_falls_back_to_nearest_earlier_on_gap(self):
        # No 07-05 artifact; point-in-time on-or-before resolves to 07-03.
        self._write("2026-07-03", make_artifact(self._recent_iso(80.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d03")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d06")}))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d03")

    def test_as_of_before_all_history_404s(self):
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-01"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"error": "no_signals"})

    def test_malformed_as_of_400s(self):
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            for bad in ("2026-7-5", "07-05-2026", "yesterday",
                        "2026-13-40", "2026-07-06T00:00:00", "2026/07/06"):
                resp = self.client.get(URL, {"as_of": bad})
                self.assertEqual(resp.status_code, 400, bad)
                self.assertEqual(resp.json(), {"error": "bad_as_of"}, bad)

    def test_as_of_in_future_returns_latest(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d05")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d06")}))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2027-01-01"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d06")

    def test_as_of_picks_newest_same_day_supplemental_by_mtime(self):
        morning = make_artifact(self._recent_iso(8.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="morning")})
        supplemental = make_artifact(self._recent_iso(0.1), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="supplemental")})
        self._write("2026-07-06", morning)
        supp = self.dir / "signals-2026-07-06-153042.json"
        supp.write_text(json.dumps(supplemental), encoding="utf-8")
        now = time.time()
        os.utime(self.dir / "signals-2026-07-06.json", (now - 100, now - 100))
        os.utime(supp, (now, now))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-06"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "supplemental")

    def test_as_of_prefers_newer_stem_date_over_newer_mtime(self):
        # Backfill/reprocess skew: an older-day artifact rewritten in place
        # gets a fresh mtime; the correctly-dated 07-05 artifact must still
        # win under as_of=2026-07-05 (calendar order beats mtime).
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d05")}))
        self._write("2026-07-03", make_artifact(self._recent_iso(80.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL, guid="d03-backfilled")}))
        now = time.time()
        os.utime(self.dir / "signals-2026-07-05.json", (now - 100, now - 100))
        os.utime(self.dir / "signals-2026-07-03.json", (now, now))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["MSFT"]["guid"], "d05")

    def test_as_of_etag_tracks_resolved_artifact(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0)))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            e05 = self.client.get(URL, {"as_of": "2026-07-05"})["ETag"]
            e06 = self.client.get(URL, {"as_of": "2026-07-06"})["ETag"]
            # A future as_of resolves to the same artifact as 07-06 -> its ETag
            # revalidates with a 304.
            revalidated = self.client.get(URL, {"as_of": "2027-01-01"},
                                          HTTP_IF_NONE_MATCH=e06)
        self.assertNotEqual(e05, e06)
        self.assertEqual(revalidated.status_code, 304)

    def test_as_of_composes_with_tickers_filter(self):
        self._write("2026-07-05", make_artifact(self._recent_iso(50.0), signals={
            "MSFT": dict(DEFAULT_SIGNAL),
            "AAPL": dict(DEFAULT_SIGNAL, guid="g2")}))
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL, {"as_of": "2026-07-05", "tickers": "msft"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.json()["signals"]), ["MSFT"])

    def test_empty_as_of_400s(self):
        # ?as_of= (present but empty) must not silently serve the latest
        # artifact — that is the lookahead bias as_of exists to prevent.
        self._write("2026-07-06", make_artifact(self._recent_iso(1.0)))
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            resp = self.client.get(URL + "?as_of=")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "bad_as_of"})

    def test_legacy_v1_artifact_normalized_to_wire_v2(self):
        # Historical artifacts predate the rename; the wire must be uniformly
        # v2 whether reached as latest or via ?as_of — score never escapes.
        legacy_entry = dict(DEFAULT_SIGNAL)
        legacy_entry["score"] = legacy_entry.pop("sentiment_score")
        art = make_artifact(self._recent_iso(2.0), signals={"MSFT": legacy_entry})
        art["schema_version"] = 1
        self._write("2026-07-10", art)
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC):
            for query in ({}, {"as_of": "2026-07-10"}):
                with self.subTest(query=query):
                    resp = self.client.get(URL, query)
                    body = resp.json()
                    self.assertEqual(body["schema_version"], 2)
                    entry = body["signals"]["MSFT"]
                    self.assertEqual(entry["sentiment_score"], 0.5)
                    self.assertNotIn("score", entry)

    def test_legacy_normalization_does_not_mutate_shared_artifact(self):
        # _get_artifact's per-request memoization means the SAME artifact
        # dict is read by _etag, _last_modified, and the view body within
        # one request; _load_artifact itself re-reads from disk on every
        # call today, so this can't be reached through the real loader —
        # mock it to return one shared dict across two separate requests
        # (as a future response cache would) and confirm the normalizer's
        # entry-copy still holds: neither the original artifact nor the
        # second response is corrupted by the first request's rename.
        legacy_entry = dict(DEFAULT_SIGNAL)
        legacy_entry["score"] = legacy_entry.pop("sentiment_score")
        art = make_artifact(self._recent_iso(), signals={"MSFT": legacy_entry})
        art["schema_version"] = 1
        with override_settings(SIGNALS_DIR=str(self.dir), **_HERMETIC), \
             mock.patch.object(signals_views, "_load_artifact", return_value=art):
            first = self.client.get(URL).json()
            second = self.client.get(URL).json()
        self.assertIn("score", art["signals"]["MSFT"])
        self.assertNotIn("sentiment_score", art["signals"]["MSFT"])
        for body in (first, second):
            self.assertEqual(body["signals"]["MSFT"]["sentiment_score"], 0.5)
            self.assertNotIn("score", body["signals"]["MSFT"])
