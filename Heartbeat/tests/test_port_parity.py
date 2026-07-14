"""Cross-copy parity between news_signals.py's input trust boundary and its
port into Main/backend/api/signals_views.py (news-items endpoint, ATL
integration Phase B).

WHY THE DUPLICATION EXISTS: Heartbeat/news_signals.py is deliberately
stdlib-only and single-file (same deployability contract as
news_heartbeat.py — see that file's docstring), so it cannot be imported
from Django. Main/backend/api/signals_views.py therefore carries a
byte-faithful PORT of clean_text/validation_gate (as _clean_text/
_validate_items) rather than an import. That port is a security trust
boundary (spec §7.1, sanitizes/validates untrusted scraped input) — a
silent drift between the two copies is exactly the kind of bug that does
not show up in either file's own test suite, only in the gap between them.

HOW THIS TEST WORKS WITHOUT IMPORTING DJANGO: signals_views.py imports
django, django_ratelimit and api.auth, none of which are available to this
stdlib-only CI job (.github/workflows/heartbeat-tests.yml runs the runner's
system python3 with no deps — see that file's comment on why this suite,
not backend-deploy.yml, is where a parity test can actually fire before
merge). So this test never imports signals_views. Instead it ast.parse()s
the file's SOURCE, pulls out only the nodes that make up the ported region
(the names in _WANTED_NODE_NAMES below), and compiles+execs just that
subset into a bare namespace pre-seeded with the stdlib modules those nodes
need (json, re, unicodedata). The ported region is deliberately Django-free
except for one guarded reference to `settings` inside _validate_items,
behind `if max_file_mb is None:` — this test always calls the extracted
_validate_items with an explicit cap, so that branch, and the name
`settings`, is never resolved. If a future edit drags a new Django (or any
other) dependency into the ported region outside that guard, this test
starts failing with NameError — that is intended: the ported region must
stay portable.

The rename map between the two copies (the ONLY thing that may differ in
the ported region):
    Heartbeat/news_signals.py        ->  Main/backend/api/signals_views.py
    clean_text                       ->  _clean_text
    validation_gate                  ->  _validate_items
    CONTROL_RE                       ->  _CONTROL_RE
    _LINEBREAK_RE                    ->  _LINEBREAK_RE   (same)
    REQUIRED_FIELDS                  ->  REQUIRED_FIELDS (same)
    FIELD_CAPS                       ->  FIELD_CAPS      (same)
    TEXT_REQUIRED_FIELDS             ->  TEXT_REQUIRED_FIELDS (same)

The ONE sanctioned divergence: cap resolution. Heartbeat reads
SIGNALS_MAX_FILE_MB via load_config(); the Django copy resolves the same
env var through settings.RAW_ITEMS_MAX_FILE_MB (one operator knob, two
readers — see Main/backend/django_config/settings.py). This test pins the
two DEFAULTS together (test_default_max_file_mb_matches_heartbeat_default)
rather than the resolution mechanism, since the mechanism is allowed to
differ.

WHEN THIS TEST GOES RED: mirror the change into the OTHER copy (apply the
rename map above in reverse as needed) until it goes green again. Do NOT
delete or weaken this test to make it pass — that defeats its entire
purpose, which is to make drift between the two copies impossible to merge
silently.
"""
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_signals as ns  # noqa: E402  (sys.path must be set up first)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SIGNALS_VIEWS_PATH = _REPO_ROOT / "Main" / "backend" / "api" / "signals_views.py"

# The exact node names that make up the ported region. Assign targets and
# function defs only — see _extract_ported_region.
_WANTED_NODE_NAMES = (
    "TEXT_REQUIRED_FIELDS",
    "REQUIRED_FIELDS",
    "FIELD_CAPS",
    "_CONTROL_RE",
    "_LINEBREAK_RE",
    "_MAX_ITEMS_FILE_MB",
    "_clean_text",
    "_validate_items",
)


def _extract_ported_region(path):
    """ast.parse() `path`'s source (never import it — it pulls in Django)
    and select only the top-level Assign/FunctionDef nodes named in
    _WANTED_NODE_NAMES. Compile that subset alone into a fresh namespace
    pre-populated with the stdlib names the nodes need, and exec it.

    Returns (namespace, found_names). found_names lets
    TestPortedNodeSetPresent fail loudly if a rename silently drops a node,
    instead of this function silently building an incomplete (and
    therefore vacuously-passing) namespace.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    selected = []
    found = set()
    for node in tree.body:
        name = None
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            name = node.targets[0].id
        elif isinstance(node, ast.FunctionDef):
            name = node.name
        if name in _WANTED_NODE_NAMES:
            selected.append(node)
            found.add(name)
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, filename=str(path), mode="exec")
    namespace = {"json": json, "re": re, "unicodedata": unicodedata}
    exec(code, namespace)  # noqa: S102 — deliberate: see module docstring
    return namespace, found


_PORTED_NS, _PORTED_FOUND = _extract_ported_region(_SIGNALS_VIEWS_PATH)


def make_story(**over):
    """Same shape as Heartbeat/tests/test_news_signals.py's make_story —
    kept local (not imported) so this file stays independently readable,
    matching Heartbeat/tests/test_watchlist.py's self-contained style."""
    s = {
        "guid": "g1", "title": "Microsoft raises Azure guidance",
        "link": "https://example.com/a", "source": "Reuters",
        "published": time.time() - 3600, "description": "desc",
        "tickers": ["MSFT"], "editorial_score": 5.0,
    }
    s.update(over)
    return s


def _without(story, field):
    """A story with `field` absent entirely — distinct from the field being
    present but malformed, and the only way to reach a .get() default arm."""
    del story[field]
    return story


def write_lines(dirpath, lines, name="items-2026-07-14.jsonl"):
    """Write raw JSONL lines (already-encoded strings) verbatim — used where
    a case needs a blank line or deliberately malformed JSON, neither of
    which make_story()+json.dumps can express."""
    p = Path(dirpath) / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def write_stories(dirpath, stories, name="items-2026-07-14.jsonl"):
    return write_lines(dirpath, [json.dumps(s) for s in stories], name)


class TestPortedNodeSetPresent(unittest.TestCase):
    """F1 guard, part 0: if a rename in signals_views.py silently drops one
    of the ported nodes, every other test in this file would just run
    against a smaller (or missing) namespace and could vacuously pass. This
    test makes that failure loud instead."""

    def test_all_ported_nodes_present_in_signals_views(self):
        missing = set(_WANTED_NODE_NAMES) - _PORTED_FOUND
        self.assertEqual(
            missing, set(),
            f"signals_views.py is missing ported node(s) {sorted(missing)} "
            "— a rename in the ported region must be mirrored from "
            "news_signals.py, never silently dropped. See this test "
            "file's module docstring for the rename map.")


class TestConstantParity(unittest.TestCase):
    """F1 guard, part 1: exact equality of every constant in the ported
    region (modulo the rename map)."""

    def test_required_fields_match(self):
        self.assertEqual(_PORTED_NS["REQUIRED_FIELDS"], ns.REQUIRED_FIELDS)

    def test_field_caps_match(self):
        self.assertEqual(_PORTED_NS["FIELD_CAPS"], ns.FIELD_CAPS)

    def test_text_required_fields_match(self):
        self.assertEqual(_PORTED_NS["TEXT_REQUIRED_FIELDS"],
                         ns.TEXT_REQUIRED_FIELDS)

    def test_control_re_pattern_matches(self):
        self.assertEqual(_PORTED_NS["_CONTROL_RE"].pattern,
                         ns.CONTROL_RE.pattern)

    def test_linebreak_re_pattern_matches(self):
        self.assertEqual(_PORTED_NS["_LINEBREAK_RE"].pattern,
                         ns._LINEBREAK_RE.pattern)

    def test_default_max_file_mb_matches_heartbeat_default(self):
        # F3's guard: the two copies read the SAME env var name
        # (SIGNALS_MAX_FILE_MB) through two different settings readers
        # (design decision D3) — that resolution MECHANISM is the one
        # sanctioned divergence (see module docstring), but the DEFAULT
        # value both readers fall back to must stay pinned together.
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            heartbeat_default = ns.load_config()["max_file_mb"]
        self.assertEqual(_PORTED_NS["_MAX_ITEMS_FILE_MB"], heartbeat_default)


# Nasty corpus for clean_text/_clean_text behavioral parity. Every entry is
# (label, value, cap); label is used as the subTest discriminator.
_CLEAN_TEXT_CORPUS = [
    ("empty_string", "", 500),
    ("none", None, 500),
    ("zero", 0, 500),
    ("false", False, 500),
    ("true", True, 500),
    ("int", 123, 500),
    ("float", 12.5, 500),
    ("list", [], 500),
    ("dict", {}, 500),
    ("bidi_override", "abc‮def", 500),
    ("embedded_marker_token", "before NEWS_DATA after", 500),
    ("tab", "a\tb", 500),
    ("newline", "a\nb", 500),
    ("vertical_tab", "a\vb", 500),
    ("form_feed", "a\fb", 500),
    ("carriage_return", "a\rb", 500),
    ("file_separator", "a\x1cb", 500),
    ("group_separator", "a\x1db", 500),
    ("record_separator", "a\x1eb", 500),
    ("nel", "a\x85b", 500),
    ("line_separator", "a b", 500),
    ("paragraph_separator", "a b", 500),
    ("nfc_combining_form", "é", 500),
    ("nfc_precomposed_form", "é", 500),
    ("control_char", "a\x01b", 500),
    ("over_cap_string", "x" * 20, 5),
]


class TestCleanTextParity(unittest.TestCase):
    """F1 guard, part 2 (also D1's superset proof, behaviorally): the two
    clean_text/_clean_text copies must return byte-identical output for
    every input, not just agree on constants."""

    def test_clean_text_matches_across_corpus(self):
        for label, value, cap in _CLEAN_TEXT_CORPUS:
            with self.subTest(case=label):
                ported = _PORTED_NS["_clean_text"](value, cap)
                heartbeat = ns.clean_text(value, cap)
                self.assertEqual(ported, heartbeat)


class TestValidateItemsParity(unittest.TestCase):
    """F1 guard, part 3: the two validation_gate/_validate_items copies
    must agree on every case, including the F2a/F2b malformed-type
    fail-safes. Both sides mutate their story dicts in place and return a
    list of dicts, so the returned lists are compared directly."""

    def setUp(self):
        self._td = tempfile.mkdtemp(prefix="port_parity_")
        self.addCleanup(shutil.rmtree, self._td, ignore_errors=True)

    def _assert_gate_parity(self, path, max_file_mb=10):
        ported = _PORTED_NS["_validate_items"](path, max_file_mb)
        heartbeat = ns.validation_gate(path, max_file_mb)
        self.assertEqual(ported, heartbeat)

    def test_validate_items_matches_across_corpus(self):
        now = time.time()
        cases = {
            "happy_batch": [
                make_story(guid="g1", tickers=["msft", "MSFT"]),
                make_story(guid="g2", title="Other headline", tickers=["nvda"]),
            ],
            # F2a: non-str required TEXT field -> drop story, not the batch.
            "non_string_title": [make_story(title=True)],
            "non_string_source": [make_story(source=None)],
            "non_string_guid": [make_story(guid=123)],
            "non_string_link": [make_story(link=[1, 2])],
            # D2: optional description is NOT a drop condition -- it just
            # blanks via _clean_text's own isinstance guard.
            "non_string_description": [make_story(description=True)],
            # The numeric parse. These are not optional padding: the guard
            # shipped without them and went green over a real one-sided edit
            # that deleted _validate_items' try/except entirely (PR #359
            # review, P0). No other case reaches float(), because every other
            # story here carries a well-formed published/editorial_score -- the
            # window cases exercise the `lo <= published <= hi` compare, not the
            # parse. Each must drop the bad story and keep the good one on
            # BOTH copies; if either side raises instead, _assert_gate_parity
            # errors, which is the signal we want.
            "editorial_score_unparseable": [
                make_story(guid="good"),
                make_story(guid="bad", editorial_score="n/a")],
            "editorial_score_none": [
                make_story(guid="good"),
                make_story(guid="bad", editorial_score=None)],
            "editorial_score_non_scalar": [
                make_story(guid="good"),
                make_story(guid="bad", editorial_score={})],
            "published_unparseable": [make_story(guid="good"),
                                      make_story(guid="bad",
                                                 published="yesterday")],
            "published_none": [make_story(guid="good"),
                               make_story(guid="bad", published=None)],
            "published_non_scalar": [make_story(guid="good"),
                                     make_story(guid="bad", published=[1])],
            # description absent ENTIRELY: the only case that reaches
            # .get("description", "")'s default arm. Without it, a one-sided
            # edit to story["description"] passes the guard and only KeyErrors
            # in production.
            "description_missing": [_without(make_story(guid="nodesc"),
                                             "description")],
            # F2b: malformed tickers must never raise, on either copy.
            "tickers_mixed_types": [make_story(tickers=[123, "aapl"])],
            "tickers_bare_string": [make_story(tickers="AAPL")],
            "tickers_none": [make_story(tickers=None)],
            "past_out_of_window": [make_story(guid="ancient",
                                              published=now - 40 * 86400)],
            "future_beyond_window": [make_story(guid="future",
                                                published=now + 7200)],
            "blank_line_middle": None,  # built specially below
        }
        for label, stories in cases.items():
            with self.subTest(case=label):
                if label == "blank_line_middle":
                    path = write_lines(self._td, [
                        json.dumps(make_story(guid="g1")),
                        "",
                        json.dumps(make_story(guid="g2")),
                    ], name=f"items-{label}.jsonl")
                else:
                    path = write_stories(self._td, stories,
                                         name=f"items-{label}.jsonl")
                self._assert_gate_parity(path)

    def test_missing_required_field_raises_valueerror_on_both(self):
        story = make_story()
        del story["editorial_score"]
        path = write_stories(self._td, [story])
        with self.assertRaises(ValueError):
            _PORTED_NS["_validate_items"](path, 10)
        with self.assertRaises(ValueError):
            ns.validation_gate(path, 10)

    def test_legacy_score_only_story_poisons_batch_in_both_copies(self):
        # Strict rename: a pre-rename story (score, no editorial_score) is a
        # MISSING REQUIRED FIELD -> batch-level ValueError in BOTH copies.
        story = make_story()
        story["score"] = story.pop("editorial_score")
        path = write_stories(self._td, [story], name="items-legacy.jsonl")
        with self.assertRaises(ValueError):
            _PORTED_NS["_validate_items"](path, 10)
        with self.assertRaises(ValueError):
            ns.validation_gate(path, 10)

    def test_extra_legacy_score_key_passes_gate_identically(self):
        # A story carrying BOTH keys validates; the stray legacy key is the
        # projection layer's problem (dropped at the wire), not the gate's.
        story = make_story()
        story["score"] = 4.0
        path = write_stories(self._td, [story], name="items-bothkeys.jsonl")
        self._assert_gate_parity(path)

    def test_malformed_json_line_raises_valueerror_on_both(self):
        path = write_lines(self._td, ['{"broken\n'])
        with self.assertRaises(ValueError):
            _PORTED_NS["_validate_items"](path, 10)
        with self.assertRaises(ValueError):
            ns.validation_gate(path, 10)

    def test_over_cap_file_raises_valueerror_on_both(self):
        path = write_stories(self._td, [make_story()])
        with self.assertRaises(ValueError):
            _PORTED_NS["_validate_items"](path, 0)  # 0 MB cap: any file trips it
        with self.assertRaises(ValueError):
            ns.validation_gate(path, 0)


if __name__ == "__main__":
    unittest.main()
