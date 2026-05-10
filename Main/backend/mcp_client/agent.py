
import os
import json as _json
from typing import Optional, List
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from agents import Agent, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.model_settings import ModelSettings
from openai.types.shared import Reasoning
import httpx
import logging

from pathlib import Path
backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(backend_dir / '.env')

import sys
sys.path.insert(0, str(backend_dir))
from datascraper.models_config import get_model_config

from datascraper.url_tools import get_url_tools
from datascraper.playwright_tools import get_playwright_tools

from .apps import get_global_mcp_manager
from .prompt_builder import PromptBuilder


_mcp_init_lock = None
_prompt_builder = PromptBuilder()

USER_ONLY_MODELS = {"o3-mini", "o1-mini", "o1-preview", "gpt-5-mini", "gpt-5.1-chat-latest"}


async def _gemini_error_hook(response: httpx.Response):
    """Capture full Gemini error responses for debugging."""
    if response.status_code < 400:
        return
    await response.aread()
    logging.error(
        f"[GEMINI] HTTP {response.status_code} | {response.url}\n"
        f"  Response body: {response.text[:3000]}"
    )
    try:
        req_body = response.request.content.decode("utf-8")
        parsed = _json.loads(req_body)
        summary = {
            "model": parsed.get("model"),
            "stream": parsed.get("stream"),
            "n_messages": len(parsed.get("messages", [])),
            "n_tools": len(parsed.get("tools", [])),
        }
        logging.error(f"[GEMINI] Request summary: {_json.dumps(summary, default=str)}")
        with open("/tmp/gemini_failed_request.json", "w") as f:
            _json.dump(parsed, f, indent=2, default=str)
        logging.error("[GEMINI] Full request dumped to /tmp/gemini_failed_request.json")
    except Exception as exc:
        logging.error(f"[GEMINI] Could not parse request: {exc}")


@asynccontextmanager
async def create_fin_agent(model: str = "gpt-4o-mini",
                          system_prompt: Optional[str] = None,
                          current_url: Optional[str] = None,
                          user_input: Optional[str] = None,
                          user_timezone: Optional[str] = None,
                          user_time: Optional[str] = None,
                          allowed_tools: Optional[List[str]] = None,
                          instructions_override: Optional[str] = None,
                          session_id: Optional[str] = None):
    """
    Create a financial agent with tools (URL scraping, SEC-EDGAR, filesystem).

    Args:
        model: The OpenAI model to use (e.g., 'gpt-4o', 'o4-mini')
        system_prompt: Custom system prompt (if None, uses default)
        current_url: Current webpage URL for context
        user_input: User's query
        user_timezone: User's IANA timezone (e.g., "America/New_York")
        user_time: User's current time in ISO format
        allowed_tools: If provided, only these tool names are included.
                       None means all tools; [] means no tools.
        instructions_override: If provided, skip PromptBuilder and use this
                               string as the agent's system instructions.

    Yields:
        Agent instance configured with tools
    """
    # `instructions` is finalised after tool collection so PromptBuilder can
    # filter the AVAILABLE TOOLS catalog against the actual tool registry for
    # this request. instructions_override bypasses PromptBuilder entirely.
    instructions: Optional[str] = instructions_override
    tools_attached = allowed_tools is None or len(allowed_tools) > 0

    model_config = get_model_config(model)
    if not model_config:
        logging.warning(f"Model ID '{model}' not found in config, using as-is")
        actual_model = model
    else:
        actual_model = model_config["model_name"]
        logging.info(f"Model resolution: {model} -> {actual_model}")

    # Build model object — string for OpenAI, OpenAIChatCompletionsModel for other providers
    gemini_http = None
    model_obj = actual_model
    if model_config and model_config.get("provider") == "google":
        google_api_key = os.getenv("GOOGLE_API_KEY", "")
        if google_api_key:
            gemini_http = httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=10.0),
                event_hooks={"response": [_gemini_error_hook]},
            )
            google_client = AsyncOpenAI(
                api_key=google_api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                http_client=gemini_http,
            )
            model_obj = OpenAIChatCompletionsModel(
                model=actual_model,
                openai_client=google_client
            )
            logging.info(f"Using Google OpenAI-compat client for {actual_model}")
        else:
            logging.error("GOOGLE_API_KEY not set but Google model requested")
            raise ValueError("GOOGLE_API_KEY environment variable is required for Google models")

    tools: List = []

    # Skip tool collection entirely when plan specifies zero tools.
    if not tools_attached:
        logging.info("[AGENT] Skill specifies zero tools, skipping tool collection")
    else:
        url_tools = get_url_tools()
        tools.extend(url_tools)

        playwright_tools = get_playwright_tools()
        tools.extend(playwright_tools)

        # Calculator tool for safe arithmetic (prevents LLM from doing math in text)
        from datascraper.calculator_tool import get_calculator_tools
        calculator_tools = get_calculator_tools()
        tools.extend(calculator_tools)

        from .mcp_manager import MCPClientManager
        from .tool_wrapper import convert_mcp_tool_to_python_callable
        import asyncio

        global _mcp_init_lock

        _mcp_manager = get_global_mcp_manager()

        if _mcp_manager is None:
            logging.warning("Global MCP manager not found, creating fallback instance")

            if _mcp_init_lock is None:
                _mcp_init_lock = asyncio.Lock()

            async with _mcp_init_lock:
                _mcp_manager = get_global_mcp_manager()
                if _mcp_manager is None:
                    manager = MCPClientManager()
                    try:
                        await manager.connect_to_servers()
                        _mcp_manager = manager
                        logging.info("Fallback MCP manager connected")
                    except Exception as e:
                        logging.error(f"Failed to initialize MCP tools: {e}")
                        _mcp_manager = None

        if _mcp_manager:
            try:
                if _mcp_manager._loop:
                    import concurrent.futures
                    future = asyncio.run_coroutine_threadsafe(
                        _mcp_manager.get_all_tools(),
                        _mcp_manager._loop
                    )
                    try:
                        mcp_tools = future.result(timeout=10)
                    except concurrent.futures.TimeoutError:
                        logging.warning("Timeout fetching MCP tools")
                        mcp_tools = []
                else:
                    mcp_tools = await _mcp_manager.get_all_tools()

                if mcp_tools:
                    logging.info(f"Agent configured with {len(mcp_tools)} MCP tools")

                    for tool in mcp_tools:

                        async def execute_mcp_tool(name, args, mgr=_mcp_manager):
                            if mgr._loop:
                                future = asyncio.run_coroutine_threadsafe(
                                    mgr.execute_tool(name, args),
                                    mgr._loop
                                )
                                return future.result(timeout=60)
                            else:
                                return await mgr.execute_tool(name, args)

                        agent_tool = convert_mcp_tool_to_python_callable(tool, execute_mcp_tool)
                        tools.append(agent_tool)

                else:
                    logging.warning("No MCP tools found")

            except Exception as e:
                logging.error(f"Error fetching/adding MCP tools: {e}", exc_info=True)

        # Apply tool filter if specified
        if allowed_tools is not None:
            pre_filter_count = len(tools)
            tools = [t for t in tools if t.name in allowed_tools]
            logging.info(
                f"[AGENT] Tool filter applied: {pre_filter_count} -> {len(tools)} "
                f"(allowed: {allowed_tools})"
            )

    # Build the system prompt now that the tool registry for this request is
    # final, so PromptBuilder can render the AVAILABLE TOOLS catalog from the
    # actual data-tool list (axiom output-protocol tools are added below and
    # are intentionally excluded from the catalog).
    if instructions is None:
        instructions = _prompt_builder.build(
            current_url=current_url,
            system_prompt=system_prompt,
            user_timezone=user_timezone,
            user_time=user_time,
            actual_tool_names=[t.name for t in tools],
        )

    # Output-protocol tools (e.g. report_claim) live in a separate prompt
    # section and are deliberately not in scope here.
    if tools_attached and allowed_tools is not None:
        tool_names = ", ".join(allowed_tools)
        instructions += (
            f"\n\nTOOL SCOPE: For this request, the only data-gathering "
            f"tools available to you are: {tool_names}. Other tools listed "
            f"in the AVAILABLE TOOLS catalog are not attached to this run."
        )

    # Axiom claim-reporting tool is a session-bound logging hook, not a
    # capability the planner chooses; add after the allow-list filter so the
    # per-skill restriction can't strip it.
    if session_id and tools_attached:
        from axioms.tool import get_axiom_tools
        tools.extend(get_axiom_tools(session_id))

    try:
        # Handle foundation models that don't support "system" roles
        if actual_model in USER_ONLY_MODELS:
            logging.info(f"Foundation model detected ({actual_model}), moving instructions to message layer")
            agent_instructions = ""
        else:
            agent_instructions = instructions

        model_settings_kwargs = {"tool_choice": "auto" if tools else None}
        if (model_config
                and "reasoning_effort" in model_config
                and model_config.get("provider") == "openai"):
            model_settings_kwargs["reasoning"] = Reasoning(effort=model_config["reasoning_effort"])

        # Disable parallel tool calls for Gemini — its streaming format causes
        # the SDK to concatenate multiple tool-call arguments into malformed JSON.
        if model_config and model_config.get("provider") == "google":
            model_settings_kwargs["parallel_tool_calls"] = False

        agent = Agent(
            name="Agentic FinSearch",
            instructions=agent_instructions,
            model=model_obj,
            tools=tools if tools else [],
            model_settings=ModelSettings(**model_settings_kwargs)
        )

        # Attach instructions as a property for the runner to use if agent_instructions is empty
        if actual_model in USER_ONLY_MODELS:
            agent._foundation_instructions = instructions

        yield agent

    finally:
        # Clean up agent resources
        try:
            if hasattr(agent, 'close'):
                await agent.close()
            logging.debug("Agent cleanup completed")
        except Exception as e:
            logging.warning(f"Error during agent cleanup: {e}")
        # Close any httpx client we created for Gemini
        try:
            if gemini_http is not None:
                await gemini_http.aclose()
                logging.debug("Gemini httpx client closed")
        except Exception as e:
            logging.warning(f"Error closing Gemini httpx client: {e}")
