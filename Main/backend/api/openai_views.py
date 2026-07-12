"""
OpenAI-compatible API views for FinGPT.
Stateless adapter for the stateful UnifiedContextManager.

Provides /v1/models and /v1/chat/completions endpoints with:
- Bearer token authentication
- Research mode with domain scoping and source return
- Thinking mode with MCP tool source tracking
"""

import json
import time
import uuid
import logging
from typing import List, Dict, Any, Optional

from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django_ratelimit import ALL
from django_ratelimit.decorators import ratelimit

from api.auth import authenticate_request as _authenticate_request
from api.agent_budget import agent_run_slot, BudgetExceeded, ConcurrencyExceeded
from api.identity import get_request_identity
from datascraper import datascraper as ds
from datascraper.url_tools import _scrape_url_impl as scrape_url
from datascraper.models_config import MODELS_CONFIG
from datascraper.unified_context_manager import (
    UnifiedContextManager,
    ContextMode,
    get_context_manager
)
from datascraper.context_integration import (
    ContextIntegration,
    get_context_integration
)

logger = logging.getLogger(__name__)

# Valid mode strings for the API
_VALID_MODES = {'thinking', 'research', 'normal'}


def _safe_error_message(exception: Exception, context: str = "") -> str:
    """
    Return a safe error message for client responses.
    NEVER returns exception details to prevent information disclosure.
    Full error details are logged server-side for debugging.
    """
    logger.error(f"Error in {context}: {type(exception).__name__}: {str(exception)}", exc_info=True)
    return "An error occurred while processing your request. Please check server logs for details."


def _busy_response() -> JsonResponse:
    """OpenAI-style 503 for an agent concurrency/daily-budget rejection.

    P0 Root-C.3: /v1 drives a full agent run and must honor the SAME global
    concurrency + daily caps the other agent views enforce via agent_run_slot.
    On rejection we RETURN (never raise) this 503 + Retry-After so the slot
    rejection is not masked by the view's generic 500 handler, matching the
    OpenAI error JSON shape used by the other /v1 errors.
    """
    resp = JsonResponse(
        {'error': {'message': 'Server is busy; too many concurrent agent runs. Retry shortly.',
                   'type': 'server_error'}},
        status=503,
    )
    resp['Retry-After'] = '30'
    return resp


def _get_api_session_id(request: HttpRequest, user_id: Optional[str] = None) -> str:
    """
    Get a session ID.
    If user_id is provided, use it for potential continuity.
    If no user_id, generate a random one for this request.
    """
    if user_id:
        return f"api_user_{user_id}"
    return f"api_req_{uuid.uuid4().hex}"


def _merge_domains_into_preferred_links(
    preferred_links: List[str],
    search_domains: Optional[List[str]]
) -> List[str]:
    """
    Merge search_domains into the preferred_links list.
    Domains are normalized to URL format (e.g., 'reuters.com' -> 'https://reuters.com').
    """
    if not search_domains:
        return preferred_links

    merged = list(preferred_links)
    for domain in search_domains:
        domain = domain.strip()
        if not domain:
            continue
        # Normalize bare domains to URLs
        if not domain.startswith('http://') and not domain.startswith('https://'):
            domain = f"https://{domain}"
        if domain not in merged:
            merged.append(domain)
    return merged


@csrf_exempt
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT, method=ALL, block=True)
def models_list(request: HttpRequest) -> JsonResponse:
    """
    List available models in OpenAI format.
    GET /v1/models
    """
    auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    data = []
    for model_id, config in MODELS_CONFIG.items():
        data.append({
            "id": model_id,
            "object": "model",
            "created": int(time.time()),
            "owned_by": config.get("provider", "fingpt"),
            "permission": [],
            "root": model_id,
            "parent": None,
        })

    return JsonResponse({
        "object": "list",
        "data": data
    })


@csrf_exempt
@ratelimit(key='api.identity.ratelimit_key', rate=settings.API_RATE_LIMIT, method=ALL, block=True)
def chat_completions(request: HttpRequest) -> JsonResponse:
    """
    Create chat completion.
    POST /v1/chat/completions

    Stateless adapter:
    1. Authenticates the request via Bearer token.
    2. Accepts 'messages' list with 'mode' (required: 'thinking' or 'research').
    3. Resets/Creates a session context.
    4. Populates context with history.
    5. Generates response using the last user message as prompt.

    Extra parameters beyond OpenAI standard:
    - mode (required): 'thinking' or 'research'
    - url (optional): target URL for page context / site-specific agent behavior
    - search_domains (optional, research mode): list of domains to scope research to
    - preferred_links (optional, research mode): list of preferred URLs for research
    - user_timezone (optional): IANA timezone string
    - user_time (optional): ISO 8601 current time

    Response extensions (in addition to standard OpenAI fields):
    - sources: list of source objects used to generate the response
    """
    auth_error = _authenticate_request(request)
    if auth_error:
        return auth_error

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': {'message': 'Invalid JSON body', 'type': 'invalid_request_error'}}, status=400)

    model = body.get('model', 'FinGPT')
    messages = body.get('messages', [])
    user_id = body.get('user')

    # Required parameter
    mode_str = body.get('mode')

    # Optional parameters
    target_url = body.get('url')
    user_timezone = body.get('user_timezone')
    user_time = body.get('user_time')
    preferred_links = body.get('preferred_links', [])
    search_domains = body.get('search_domains')

    # --- Validation ---
    if not messages:
        return JsonResponse(
            {'error': {'message': 'messages array is required', 'type': 'invalid_request_error'}},
            status=400
        )

    if not mode_str:
        return JsonResponse(
            {'error': {'message': "mode is required. Valid values: 'thinking', 'research'", 'type': 'invalid_request_error'}},
            status=400
        )

    if mode_str.lower() not in _VALID_MODES:
        return JsonResponse(
            {'error': {'message': f"Invalid mode '{mode_str}'. Valid values: {', '.join(sorted(_VALID_MODES))}", 'type': 'invalid_request_error'}},
            status=400
        )

    if model not in MODELS_CONFIG:
        return JsonResponse(
            {'error': {'message': f"Model '{model}' does not exist. Use GET /v1/models to list available models.", 'type': 'invalid_request_error'}},
            status=404
        )

    # Merge search_domains into preferred_links for research mode
    if search_domains and mode_str.lower() == 'research':
        preferred_links = _merge_domains_into_preferred_links(preferred_links, search_domains)
        logger.info(f"Merged {len(search_domains)} search domains into preferred_links (total: {len(preferred_links)})")

    # --- Session Setup ---
    session_id = _get_api_session_id(request, user_id)
    context_mgr = get_context_manager()
    integration = get_context_integration()

    # Reset Context (Statelessness) — call context_mgr directly since we already have session_id
    context_mgr.clear_session(session_id)
    context_mgr.set_system_prompt(session_id, "You are a helpful financial assistant.")

    # Handle URL initialization (Scraping)
    if target_url:
        try:
            logger.info(f"API initializing with URL: {target_url}")
            # SSRF: scrape_url is _scrape_url_impl, which validates + IP-pins the
            # fetch via datascraper.ssrf_guard (validate_fetch_url + safe_get).
            # This url-param sink is therefore covered transitively — no extra
            # guard is needed here.
            scrape_result_json = scrape_url(target_url)
            scrape_result = json.loads(scrape_result_json)

            if "error" not in scrape_result:
                content = scrape_result.get("content", "")
                integration.add_web_content(
                    request=request,
                    text_content=content,
                    current_url=target_url,
                    source_type="js_scraping",
                    session_id=session_id
                )
        except Exception as e:
            logger.error(f"Failed to scrape initial URL {target_url}: {e}")

    # --- Populate Context from messages ---
    history_messages = messages[:-1]
    last_message = messages[-1]

    for msg in history_messages:
        role = msg.get('role')
        content = msg.get('content', '')
        if role == 'system':
            context_mgr.set_system_prompt(session_id, content)
        elif role == 'user':
            context_mgr.add_user_message(session_id, content)
        elif role == 'assistant':
            context_mgr.add_assistant_message(session_id, content, model=model)

    if last_message.get('role') == 'user':
        last_user_content = last_message.get('content', '')
        context_mgr.add_user_message(session_id, last_user_content)
    else:
        last_user_content = ""

    # Determine Context Mode
    mode_lower = mode_str.lower()
    if mode_lower == 'research':
        context_mode = ContextMode.RESEARCH
    elif mode_lower == 'normal':
        context_mode = ContextMode.NORMAL
    else:
        context_mode = ContextMode.THINKING

    # Update metadata
    context_mgr.update_metadata(
        session_id=session_id,
        mode=context_mode,
        current_url=target_url if target_url else "",
        user_timezone=user_timezone,
        user_time=user_time
    )

    formatted_messages = context_mgr.get_formatted_messages_for_api(session_id)

    logger.info(f"API request: mode={mode_lower}, model={model}, session={session_id}")

    # --- Generate Response ---
    return _handle_sync(
        context_mgr, integration, session_id,
        last_user_content, formatted_messages,
        model, context_mode, preferred_links,
        request=request,
    )


def _handle_sync(context_mgr, integration, session_id, question, messages, model, mode, preferred_links=None, request=None):
    """Handle synchronous (non-streaming) API responses."""
    try:
        start_time = time.time()

        meta = context_mgr.get_session_metadata(session_id)
        current_url = meta.current_url
        sources = []

        # P0 Root-C.3: this drives a full agent run, so it must take a global
        # agent slot exactly like the 5 main agent views. Without it, /v1
        # callers escape the concurrency (3) + daily caps entirely. The slot is
        # entered synchronously around the actual agent execution and released
        # on exit; a rejection returns an OpenAI-style 503 (never a 500).
        identity = get_request_identity(request) if request is not None else "ip:unknown"
        try:
            with agent_run_slot(identity):
                if mode == ContextMode.RESEARCH:
                    response_content, sources = ds.create_advanced_response(
                        user_input=question,
                        message_list=messages,
                        model=model,
                        preferred_links=preferred_links or [],
                        user_timezone=meta.user_timezone,
                        user_time=meta.user_time
                    )
                    # Store research sources in context
                    if sources:
                        integration.add_search_results(session_id, sources)
                else:
                    # Thinking mode
                    response_content, sources = ds.create_agent_response(
                        user_input=question,
                        message_list=messages,
                        model=model,
                        current_url=current_url,
                        user_timezone=meta.user_timezone,
                        user_time=meta.user_time
                    )
        except (ConcurrencyExceeded, BudgetExceeded):
            return _busy_response()

        # Record response in context
        response_time_ms = int((time.time() - start_time) * 1000)
        context_mgr.add_assistant_message(
            session_id=session_id,
            content=response_content,
            model=model,
            sources_used=sources if mode == ContextMode.RESEARCH else [],
            tools_used=[s.get('tool_name', '') for s in sources] if mode != ContextMode.RESEARCH else ["web_search"],
            response_time_ms=response_time_ms
        )

        # Format OpenAI-compatible response with source extensions
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        stats = context_mgr.get_session_stats(session_id)

        response_body = {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_content
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": stats.get('token_count', 0),
                "completion_tokens": len(response_content) // 4,
                "total_tokens": stats.get('token_count', 0) + (len(response_content) // 4)
            },
            # FinGPT extensions — source tracking
            "sources": sources,
        }

        return JsonResponse(response_body)

    except Exception as e:
        return JsonResponse(
            {'error': {'message': _safe_error_message(e, 'API Sync'), 'type': 'server_error'}},
            status=500
        )
