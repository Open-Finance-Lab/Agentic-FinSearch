"""Cross-repo contract test: the committed signals fixture must validate
against the pinned signals-v1 schema and project onto ATL's 7-field
NewsSentimentEntry (spec §4.2/§4.5)."""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA = REPO_ROOT / "Heartbeat" / "schemas" / "signals-v1.schema.json"
FIXTURE = REPO_ROOT / "Heartbeat" / "tests" / "fixtures" / "signals-fixture.json"


def test_fixture_validates_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(
        json.loads(FIXTURE.read_text(encoding="utf-8")),
        json.loads(SCHEMA.read_text(encoding="utf-8")),
    )


def test_fixture_supports_atl_projection():
    artifact = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert artifact["signals"], "fixture must carry at least one signal"
    for entry in artifact["signals"].values():
        # sentiment/score/headline/source/url/n_articles cross directly;
        # age_hours is derived consumer-side from published (spec §4.5).
        for field in ("sentiment", "score", "headline", "source", "url",
                      "published", "n_articles"):
            assert field in entry
        assert entry["sentiment"] in ("bullish", "bearish", "neutral")
        assert -1.0 <= entry["score"] <= 1.0
