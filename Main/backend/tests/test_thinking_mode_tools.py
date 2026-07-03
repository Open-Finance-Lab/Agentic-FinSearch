"""Regression test for the deny-by-default tool-attach hole on the NON-streaming
thinking-mode agent path (``_create_agent_response_async``).

After ``create_fin_agent``'s ``allowed_tools=None`` was changed from "all tools"
to "deny-all", the non-streaming path (used by ``get_chat_response/`` and the
OpenAI ``/v1`` thinking path) was still calling
``create_fin_agent(...)`` WITHOUT an allow-list, so it silently attached ZERO
tools. This test pins that the non-streaming path now passes a NON-empty,
finite ``allowed_tools`` containing the real read-only data tools.

No live agent/LLM runs: ``create_fin_agent`` is replaced with a stub async
context manager and ``Runner.run`` is mocked.
"""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import datascraper.datascraper as ds


def _run_async(coro):
    # asyncio.run() creates/closes its own loop, so it is robust whether or not
    # a prior test left a usable current event loop in this thread.
    return asyncio.run(coro)


def test_non_streaming_path_attaches_real_data_tools():
    captured = {}

    @asynccontextmanager
    async def fake_create_fin_agent(*args, **kwargs):
        captured["kwargs"] = kwargs
        agent = MagicMock()
        agent._foundation_instructions = ""
        yield agent

    fake_result = MagicMock()
    fake_result.final_output = "ok"
    fake_result.raw_responses = []

    async def fake_runner_run(agent, prompt, max_turns=30):
        return fake_result

    with patch("mcp_client.agent.create_fin_agent", fake_create_fin_agent), \
         patch("agents.Runner.run", side_effect=fake_runner_run), \
         patch("agents.set_tracing_disabled"), \
         patch.object(ds, "_extract_tool_sources_from_result", return_value=[]):
        _run_async(
            ds._create_agent_response_async(
                user_input="What is AAPL price?",
                message_list=[{"role": "user", "content": "What is AAPL price?"}],
                model="gpt-4o-mini",
            )
        )

    assert "kwargs" in captured, "create_fin_agent was never called"
    allowed = captured["kwargs"].get("allowed_tools")
    # Must NOT be None (would mean deny-all under the new policy) and must NOT
    # be empty.
    assert allowed is not None, "allowed_tools=None -> deny-all (the regression)"
    assert isinstance(allowed, list) and len(allowed) > 0, "no tools attached"
    # Real data tools must be present.
    assert "get_stock_info" in allowed
    assert "scrape_url" in allowed
