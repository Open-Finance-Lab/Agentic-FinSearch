"""Watchlist single-source-of-truth + cross-file parity (spec 2026-07-10).

DOW_30 is duplicated in news_signals.py and news_heartbeat.py by design — the
heartbeat is stdlib-only, single-file per script, so there is no shared module.
This test is the anti-drift guard, same discipline as the schema-parity test.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_signals as ns
import news_heartbeat as nh

# Current Dow-30, verified 2026-07-10 (S&P DJI, effective 2026-06-29).
EXPECTED_DOW_30 = {
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GOOGL", "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM",
    "MRK", "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "WMT",
}
# Stale / erroneous tickers that must never reappear (the pre-fix bug set,
# incl. DOW — the ticket wrongly wanted it, but it left the index in 2024).
FORBIDDEN = {"AMEX", "DOW", "INTC", "MA", "PFE", "WBA", "XOM", "VZ"}


class TestWatchlistParity(unittest.TestCase):
    def test_dow30_matches_across_both_scripts(self):
        self.assertEqual(ns.DOW_30, nh.DOW_30)

    def test_extras_match_across_both_scripts(self):
        self.assertEqual(ns.WATCHLIST_EXTRAS, nh.WATCHLIST_EXTRAS)

    def test_default_watchlist_matches_across_both_scripts(self):
        self.assertEqual(ns.DEFAULT_WATCHLIST, nh.DEFAULT_WATCHLIST)

    def test_dow30_is_exactly_the_current_index(self):
        self.assertEqual(set(ns.DOW_30), EXPECTED_DOW_30)
        self.assertEqual(len(ns.DOW_30), 30)
        self.assertEqual(len(set(ns.DOW_30)), 30)          # unique
        self.assertTrue(all(t == t.upper() for t in ns.DOW_30))

    def test_no_stale_or_erroneous_tickers(self):
        self.assertEqual(set(ns.DOW_30) & FORBIDDEN, set())
        self.assertEqual(set(ns.WATCHLIST_EXTRAS) & FORBIDDEN, set())

    def test_extras_are_disjoint_from_dow30(self):
        self.assertEqual(set(ns.WATCHLIST_EXTRAS) & set(ns.DOW_30), set())

    def test_default_watchlist_is_sorted_union_of_34(self):
        expected = " ".join(sorted(set(ns.DOW_30) | set(ns.WATCHLIST_EXTRAS)))
        self.assertEqual(ns.DEFAULT_WATCHLIST, expected)
        self.assertEqual(len(ns.DEFAULT_WATCHLIST.split()), 34)

    def test_reconcile_additions_present(self):
        for t in ("AMGN", "CRM", "HON", "SHW"):
            self.assertIn(t, ns.DOW_30, t)

    def test_ticker_aliases_cover_exactly_the_watchlist(self):
        # news_signals-only: the subject-relevance gate (spec D8) matches
        # name-based headlines through TICKER_ALIASES, so a ticker in the
        # watchlist but not the alias table is only half-added (and
        # warn_alias_gaps WARNs every sweep). Set EQUALITY also proves the
        # stale INTC/MA/PFE/WBA/XOM aliases are gone.
        union = set(ns.DOW_30) | set(ns.WATCHLIST_EXTRAS)
        self.assertEqual(set(ns.TICKER_ALIASES), union)


if __name__ == "__main__":
    unittest.main()
