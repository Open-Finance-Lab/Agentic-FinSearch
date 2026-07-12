"""Tests for model configuration."""
import re
from pathlib import Path


def test_default_model_is_configured():
    from datascraper.models_config import DEFAULT_MODEL, MODELS_CONFIG
    assert DEFAULT_MODEL in MODELS_CONFIG


def test_views_model_fallbacks_use_default_model():
    # A fallback outside MODELS_CONFIG sends clients that omit `models` down
    # the unknown-model path (the old 'gpt-4o-mini' default, F-4).
    src = (Path(__file__).resolve().parent.parent / "api" / "views.py").read_text()
    fallbacks = re.findall(r"params\.get\(\s*['\"]models['\"]\s*,\s*([^)]+)\)", src)
    assert fallbacks, "expected at least one models fallback in api/views.py"
    assert all(f.strip() == "DEFAULT_MODEL" for f in fallbacks), fallbacks
