"""Tests for the Sources-popup XBRL cards (axioms/sources.py).

The card link must resolve through the xbrl_filing_download view. This chain
silently 404'd when the resolver migration changed xbrl_source_url to return a SEC
accession instead of a local filename; these tests pin the full chain so it cannot
regress again. conftest.py handles the mcp_server shadowing workaround.
"""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_config.settings')

import django  # noqa: E402
from django.apps import apps as _django_apps  # noqa: E402

if not _django_apps.ready:
    django.setup()

from django.test import RequestFactory  # noqa: E402

from api import views  # noqa: E402
from axioms.sources import build_xbrl_sources  # noqa: E402


class _DictCache:
    """Minimal Django-cache-compatible stub backed by a dict."""
    def __init__(self):
        self._store = {}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value, timeout=None):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)


@pytest.fixture
def factory():
    return RequestFactory()


def _claim(ticker):
    return {"ratio": "accounting_equation", "ticker": ticker, "period": "2023-09-30",
            "claimed_value": 1.0, "formula_inputs": {}}


def test_card_link_resolves_to_a_servable_filing(factory):
    with patch("axioms.registry.cache", _DictCache()):
        from axioms.registry import add_claim
        sid = "sources-card-resolves"
        add_claim(sid, _claim("AAPL"))
        cards = build_xbrl_sources(sid)

    assert len(cards) == 1
    card = cards[0]
    assert card["source_type"] == "xbrl"
    filename = card["display_url"]
    assert filename == "aapl-20230930.xml"        # local servable filename, not an accession
    # The emitted link must actually resolve (the chain that silently 404'd when the
    # migration made xbrl_source_url return an accession that the download regex rejects).
    resp = views.xbrl_filing_download(factory.get(card["url"]), filename)
    assert resp.status_code == 200
    b"".join(resp.streaming_content)              # drain to close the file handle


def test_no_card_for_ticker_without_local_filing():
    with patch("axioms.registry.cache", _DictCache()):
        from axioms.registry import add_claim
        sid = "sources-no-filing"
        add_claim(sid, _claim("ZZZZ"))
        cards = build_xbrl_sources(sid)
    assert cards == []                            # no local filing -> no dead link emitted
