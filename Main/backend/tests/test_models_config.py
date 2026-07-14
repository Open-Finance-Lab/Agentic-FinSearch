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


def test_validate_model_support_fails_safe_on_ambiguous_model_name():
    """A raw model_name shared by a tool-enabled entry (FinGPT, supports_mcp=True)
    and a direct no-tools entry (FinSearch-Trader, supports_mcp=False) must resolve
    to the DIRECT entry, so an ambiguous identifier reports the restrictive
    capability set (fail safe) — the same policy _direct_dispatch_target applies at
    the routing seam (see test_trader_persona). Exact display-name keys and unique
    model_names are unaffected."""
    from datascraper.models_config import validate_model_support
    # Shared 'gemini-3-flash-preview' must fail safe onto the direct entry.
    assert validate_model_support("gemini-3-flash-preview", "mcp") is False
    assert validate_model_support("gemini-3-flash-preview", "advanced") is False
    # Exact keys resolve exactly — FinGPT keeps its capabilities, trader stays off.
    assert validate_model_support("FinGPT", "mcp") is True
    assert validate_model_support("FinSearch-Trader", "mcp") is False
    # A unique model_name (no direct sibling) still resolves via reverse lookup.
    assert validate_model_support("gpt-5.1-chat-latest", "mcp") is True
    # Unknown identifiers stay unsupported.
    assert validate_model_support("no-such-model", "mcp") is False
