"""Trader persona + config-driven direct-path routing (FinSearch-Trader).

Design: agent-trading-lab docs/superpowers/specs/2026-07-13-finsearch-leaderboard-agent-design.md §4.
The core safety invariant: a `direct: True` model must NEVER reach the
agent/tool machinery (look-ahead in a backtest) NOR Buffet's dedicated HF
endpoint (a different model entirely) — it goes to its own provider client
via the generic dispatch in create_response().
"""
from types import SimpleNamespace
from unittest.mock import patch

from datascraper import datascraper as ds
from datascraper.models_config import MODELS_CONFIG


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

def test_finsearch_trader_registered_with_real_schema_keys():
    cfg = MODELS_CONFIG["FinSearch-Trader"]
    assert cfg["direct"] is True
    assert cfg["persona"] == "trader"
    assert cfg["model_name"]                 # the key every read site uses
    assert cfg["streaming"] is False         # the key datascraper.py reads
    assert cfg["supports_mcp"] is False      # defense-in-depth: agent path
    assert cfg["supports_advanced"] is False  # rejects it even without `direct`


def test_buffet_agent_migrated_to_config_driven_mechanism():
    cfg = MODELS_CONFIG["Buffet-Agent"]
    assert cfg["direct"] is True
    assert cfg["persona"] == "buffett"
    assert cfg["provider"] == "buffet"  # HF-endpoint transport key unchanged


# ---------------------------------------------------------------------------
# Persona loader (prompts/personas/*.md are the single source of truth)
# ---------------------------------------------------------------------------

def test_persona_loader_reads_prompt_files():
    trader = ds.load_persona_instruction("trader")
    buffett = ds.load_persona_instruction("buffett")
    assert "FinSearch Trader" in trader
    assert "Warren Buffett" in buffett


def test_persona_instructions_keep_security_guardrails():
    """Every persona rides with the shared security fragment, like
    INSTRUCTION/BUFFETT_INSTRUCTION always have."""
    trader = ds.load_persona_instruction("trader")
    assert ds._SECURITY_GUARDRAILS in trader


def test_persona_loader_falls_back_for_unknown_name():
    assert ds.load_persona_instruction("nonexistent") == ds.INSTRUCTION


def test_prepare_messages_uses_trader_instruction():
    msgs, _system = ds._prepare_messages([], "What do you do?",
                                         model="FinSearch-Trader")
    joined = " ".join(m.get("content", "") for m in msgs)
    assert "FinSearch Trader" in joined
    assert "Warren Buffett" not in joined


def test_buffet_still_uses_buffett_instruction():
    msgs, _system = ds._prepare_messages([], "hi", model="Buffet-Agent")
    joined = " ".join(m.get("content", "") for m in msgs)
    assert "Warren Buffett" in joined


def test_default_models_keep_plain_instruction():
    msgs, _system = ds._prepare_messages([], "hi", model="FinGPT")
    joined = " ".join(m.get("content", "") for m in msgs)
    assert "Warren Buffett" not in joined
    assert "FinSearch Trader" not in joined


# ---------------------------------------------------------------------------
# Routing matrix — assert at the provider-client / _call_buffet_agent seam,
# NOT by patching create_response (which would hide misrouting inside it).
# ---------------------------------------------------------------------------

class _FakeChatCompletions:
    def __init__(self, log):
        self._log = log

    def create(self, **kwargs):
        self._log.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="DIRECT-PATH-RESPONSE"))],
        )


def _fake_google_client(log):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeChatCompletions(log)))


def test_normal_mode_routes_direct_to_provider_client():
    """create_agent_response('normal'/'thinking' path) must produce the text
    from the model's own provider client — no agent runner, no Buffet HF."""
    calls = []
    with patch.dict(ds.clients, {"google": _fake_google_client(calls)}), \
         patch.object(ds, "_call_buffet_agent") as buffet:
        text, sources = ds.create_agent_response(
            "What is your read?", [], model="FinSearch-Trader")
    assert text == "DIRECT-PATH-RESPONSE"
    assert sources == []
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-3-flash-preview"
    buffet.assert_not_called()


def test_research_mode_routes_direct_to_provider_client():
    """create_advanced_response must short-circuit direct models before any
    MCP-first / research-engine machinery."""
    calls = []
    with patch.dict(ds.clients, {"google": _fake_google_client(calls)}), \
         patch.object(ds, "_call_buffet_agent") as buffet:
        text, sources = ds.create_advanced_response(
            "What is the current price of AAPL?",  # numerical: would trip MCP-first
            [], model="FinSearch-Trader")
    assert text == "DIRECT-PATH-RESPONSE"
    assert sources == []
    assert len(calls) == 1
    buffet.assert_not_called()


def test_agent_stream_routes_direct_models_to_non_agent_path():
    """create_agent_response_stream must route direct models to
    create_response(stream=True) — patching create_response is fine HERE
    because the assertion is only about the dispatch, and the internal
    provider routing is covered by the two tests above."""
    with patch.object(ds, "create_response", return_value=iter(["ok"])) as cr:
        _stream, _state = ds.create_agent_response_stream(
            "hi", [], model="FinSearch-Trader")
    cr.assert_called_once()
    assert cr.call_args.kwargs.get("stream") is True or cr.call_args.args[-1] is True


def test_research_stream_routes_direct_models_to_non_agent_path():
    with patch.object(ds, "create_response", return_value=iter(["ok"])) as cr:
        _stream, state = ds.create_advanced_response_streaming(
            "hi", [], model="FinSearch-Trader")
    cr.assert_called_once()
    assert "final_output" in state


def test_buffet_agent_still_reaches_hf_endpoint():
    """Behavior preservation: Buffet's direct routing must still land on the
    dedicated HF endpoint, not the generic provider dispatch."""
    with patch.object(ds, "_call_buffet_agent",
                      return_value="BUFFETT-SAYS") as buffet:
        text, sources = ds.create_agent_response("hi", [], model="Buffet-Agent")
    assert text == "BUFFETT-SAYS"
    buffet.assert_called_once()
