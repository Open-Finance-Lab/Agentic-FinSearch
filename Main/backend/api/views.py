"""
API views for the FinGPT agent using the unified context manager and browser-extension
friendly CSRF exemptions guarded by CORS and secure session settings.
"""

import json
import os
import csv
import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_page
from django.http import (
    FileResponse,
    HttpRequest,
    HttpResponseNotFound,
    JsonResponse,
    StreamingHttpResponse,
)
from django.conf import settings
from django_ratelimit.decorators import ratelimit

from axioms.registry import get_claims
from axioms.resolver import FILINGS_DIR
from axioms.sources import build_xbrl_sources, merge_xbrl_sources
from axioms.wrapper import wrap_claim_values

from datascraper import datascraper as ds
from datascraper.preferred_links_manager import get_manager
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
from datascraper.url_tools import _scrape_url_impl as scrape_url

logger = logging.getLogger(__name__)


def _safe_error_message(exception: Exception, context: str = "") -> str:
    """
    Return a safe error message for client responses.
    NEVER returns exception details to prevent information disclosure.
    Full error details are logged server-side for debugging.

    Args:
        exception: The exception that occurred
        context: Optional context about where the error occurred

    Returns:
        Safe generic error message for client (never contains exception details)
    """
    # Log full error details server-side for debugging
    # Developers should check server logs, not client responses
    logger.error(f"Error in {context}: {type(exception).__name__}: {str(exception)}", exc_info=True)

    # Always return generic message - never expose exception details to clients
    return "An error occurred while processing your request. Please check server logs for details."


def _get_version():
    """Dynamically fetch version from pyproject.toml"""
    try:
        pyproject_path = Path(__file__).resolve().parent.parent / 'pyproject.toml'
        with open(pyproject_path, 'r', encoding='utf-8') as f:
            content = f.read()
            match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                return match.group(1)
    except Exception:
        pass
    return 'unknown'


def _int_env(name: str, default: int) -> int:
    """Safely parse integer environment variables."""
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_session_id(request: HttpRequest) -> str:
    """Get or create session ID for context management."""
    custom_session_id = request.GET.get('session_id')

    if not custom_session_id and request.method == 'POST':
        try:
            body_data = json.loads(request.body)
            custom_session_id = body_data.get('session_id')
        except (json.JSONDecodeError, ValueError):
            pass

    if custom_session_id:
        return custom_session_id

    if not request.session.session_key:
        request.session.create()

    return request.session.session_key


def _build_status_frame(label: str, detail: Optional[str] = None, url: Optional[str] = None) -> bytes:
    """Create an SSE frame containing only status data."""
    status_payload = {"status": {"label": label}}
    if detail:
        status_payload["status"]["detail"] = detail
    if url:
        status_payload["status"]["url"] = url
    return f'data: {json.dumps(status_payload)}\n\n'.encode('utf-8')


def _wrap_for_client(prose: str, session_id: str) -> str:
    """Wrap claim values for client rendering; return prose unchanged on error.

    The unwrapped prose is what we persist to conversation history so future
    turns don't see stray <span> markup in context.
    """
    try:
        return wrap_claim_values(prose, get_claims(session_id), session_id=session_id)
    except Exception as err:
        logger.debug(f"wrap_claim_values failed (non-critical): {err}")
        return prose


@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def has_axiom_claims(request: HttpRequest) -> JsonResponse:
    """Lightweight check: does the current session have any ratio claims
    awaiting validation? The frontend uses this to decide whether to show
    the Validate button on a response bubble.
    """
    try:
        session_id = request.GET.get('session_id') or _get_session_id(request)
        claims = get_claims(session_id) if session_id else []
        return JsonResponse({
            'session_id': session_id,
            'has_claims': len(claims) > 0,
            'count': len(claims),
        })
    except Exception as e:
        return JsonResponse({'error': _safe_error_message(e, 'has_axiom_claims')}, status=500)


_XBRL_FILENAME_RE = re.compile(r"^[a-z0-9]+-[0-9]{8}\.xml$")
_FILINGS_DIR_RESOLVED = FILINGS_DIR.resolve()


@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def xbrl_filing_download(request: HttpRequest, filename: str) -> FileResponse:
    """Serve a local SEC XBRL filing used for Layer 1 Validate.

    The sources popup links here so users can inspect the ground-truth
    document the axiom engine verified against. Filename is validated
    against a strict pattern (``aapl-20230930.xml``) to block path
    traversal; we never concatenate raw user input into a Path.
    """
    if not _XBRL_FILENAME_RE.match(filename):
        return HttpResponseNotFound('XBRL filing not found')

    try:
        resolved = (FILINGS_DIR / filename).resolve(strict=True)
        if _FILINGS_DIR_RESOLVED not in resolved.parents:
            return HttpResponseNotFound('XBRL filing not found')
    except (FileNotFoundError, OSError):
        return HttpResponseNotFound('XBRL filing not found')

    response = FileResponse(open(resolved, 'rb'), content_type='application/xml')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def validate_claims(request: HttpRequest) -> JsonResponse:
    """Layer 1 Validate: run deterministic proof over claims recorded for a session.

    Request (POST JSON): {"session_id": "..."}
    Response: {"claims": [per-claim result], "summary": {...}}

    Each claim result contains:
      ratio, ticker, period, claimed_value, status (VERIFIED|FAILED|SKIPPED|NOT_APPLICABLE),
      expected, actual, variance_pct, formula, xbrl_source, message
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        body = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'invalid JSON body'}, status=400)

    session_id = body.get('session_id') or _get_session_id(request)
    if not session_id:
        return JsonResponse({'error': 'session_id required'}, status=400)

    try:
        from axioms import validate_session
        return JsonResponse(validate_session(session_id))
    except Exception as e:
        return JsonResponse({'error': _safe_error_message(e, 'validate_claims')}, status=500)


 

@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def chat_response(request: HttpRequest) -> JsonResponse:
    """
    Thinking Mode: Process user questions using LLM with available MCP tools.
    Note: Browser automation has been removed. For web research, use Research mode.
    Uses Unified Context Manager for full conversation history.
    """
    try:
        question = request.GET.get('question', '')
        selected_models = request.GET.get('models', 'gpt-4o-mini')
        current_url = request.GET.get('current_url', '')
        use_unified = request.GET.get('use_unified', 'true').lower() == 'true'

        if not question:
            return JsonResponse({'error': 'No question provided'}, status=400)

        logger.info(f"Chat request: question='{question[:50]}...'")

        session_id = _get_session_id(request)

        integration = get_context_integration()
        context_mgr = get_context_manager()

        context_mgr.update_metadata(
            session_id=session_id,
            mode=ContextMode.THINKING,
            current_url=current_url,
            user_timezone=request.GET.get('user_timezone'),
            user_time=request.GET.get('user_time')
        )

        context_mgr.add_user_message(session_id, question)

        messages = context_mgr.get_formatted_messages_for_api(session_id)

        models = [m.strip() for m in selected_models.split(',') if m.strip()]
        responses = {}

        for model in models:
            try:
                start_time = time.time()

                response, _sources = ds.create_agent_response(
                    user_input=question,
                    message_list=messages,
                    model=model,
                    current_url=current_url,
                    user_timezone=request.GET.get('user_timezone'),
                    user_time=request.GET.get('user_time'),
                    session_id=session_id,
                )

                responses[model] = _wrap_for_client(response, session_id)

                response_time_ms = int((time.time() - start_time) * 1000)
                context_mgr.add_assistant_message(
                    session_id=session_id,
                    content=response,
                    model=model,
                    tools_used=[],
                    response_time_ms=response_time_ms
                )

            except Exception as e:
                responses[model] = f"Error: {_safe_error_message(e, f'model {model}')}"

        stats = context_mgr.get_session_stats(session_id)

        first_response = next(iter(responses.values()), "No response")
        logger.info(f"Interaction [normal_chat]: URL={current_url}, Q='{question[:50]}...', Resp='{str(first_response)[:50]}...'")

        result = {
            'resp': responses,
            'has_axiom_claims': bool(get_claims(session_id)),
            'context_stats': {
                'session_id': session_id,
                'mode': stats['mode'],
                'message_count': stats['message_count'],
                'token_count': stats['token_count'],
                'fetched_context': stats['fetched_context_counts']
            }
        }

        return JsonResponse(result)

    except Exception as e:
        logger.error(f"Chat response error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)


@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def adv_response(request: HttpRequest) -> JsonResponse:
    """
    Extensive Mode: Search for information ANYWHERE on the web using web_search.
    Uses OpenAI Responses API with built-in web_search tool (no domain restrictions).
    Now uses Unified Context Manager for full conversation history.
    """
    try:
        question = request.GET.get('question', '')
        selected_models = request.GET.get('models', 'gpt-4o-mini')
        preferred_links_json = request.GET.get('preferred_links', '')
        current_url = request.GET.get('current_url', '')

        if not question:
            return JsonResponse({'error': 'No question provided'}, status=400)

        preferred_links = []
        if preferred_links_json:
            try:
                preferred_links = json.loads(preferred_links_json)
                logger.info(f"Received {len(preferred_links)} preferred links")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse preferred links JSON")

        session_id = _get_session_id(request)

        integration = get_context_integration()
        context_mgr = get_context_manager()

        context_mgr.update_metadata(
            session_id=session_id,
            mode=ContextMode.RESEARCH,
            current_url=current_url,
            user_timezone=request.GET.get('user_timezone'),
            user_time=request.GET.get('user_time')
        )

        context_mgr.add_user_message(session_id, question)

        messages = context_mgr.get_formatted_messages_for_api(session_id)

        models = [m.strip() for m in selected_models.split(',') if m.strip()]
        responses = {}
        all_sources = []

        for model in models:
            try:
                start_time = time.time()

                response, sources = ds.create_advanced_response(
                    user_input=question,
                    message_list=messages,
                    model=model,
                    preferred_links=preferred_links,
                    stream=False,
                    user_timezone=request.GET.get('user_timezone'),
                    user_time=request.GET.get('user_time')
                )

                responses[model] = _wrap_for_client(response, session_id)
                all_sources.extend(sources)

                if sources:
                    integration.add_search_results(session_id, sources)

                # XBRL filings must be persisted into sources_used so that the
                # /get_source_urls/ endpoint (which backs the Sources popup) can
                # surface them. Build here — post-agent-run — so report_claim()
                # claims emitted during ds.create_advanced_response are visible.
                try:
                    xbrl_sources = build_xbrl_sources(session_id, request.build_absolute_uri)
                except Exception as xbrl_err:
                    logger.debug(f"XBRL source collection failed (non-critical): {xbrl_err}")
                    xbrl_sources = []

                response_time_ms = int((time.time() - start_time) * 1000)
                context_mgr.add_assistant_message(
                    session_id=session_id,
                    content=response,
                    model=model,
                    sources_used=merge_xbrl_sources(sources, xbrl_sources),
                    tools_used=["web_search"],
                    response_time_ms=response_time_ms
                )

            except Exception as e:
                responses[model] = f"Error: {_safe_error_message(e, f'model {model}')}"

        try:
            xbrl_sources = build_xbrl_sources(session_id, request.build_absolute_uri)
            all_sources = merge_xbrl_sources(all_sources, xbrl_sources)
        except Exception as xbrl_err:
            logger.debug(f"XBRL source collection failed (non-critical): {xbrl_err}")

        stats = context_mgr.get_session_stats(session_id)

        first_response = next(iter(responses.values()), "No response")
        logger.info(f"Interaction [advanced_search]: URL={current_url}, Q='{question[:50]}...', Resp='{str(first_response)[:50]}...'")

        result = {
            'resp': responses,
            'used_sources': all_sources,
            'used_urls': [s.get('url') for s in all_sources if isinstance(s, dict) and s.get('url')],
            'context_stats': {
                'session_id': session_id,
                'mode': stats['mode'],
                'message_count': stats['message_count'],
                'token_count': stats['token_count'],
                'fetched_context': stats['fetched_context_counts']
            }
        }

        return JsonResponse(result)

    except Exception as e:
        logger.error(f"Advanced response error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)


@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def agent_chat_response(request: HttpRequest) -> JsonResponse:
    """
    Process chat response via Agent with MCP tools (SEC-EDGAR, filesystem).
    Note: Browser automation has been removed. For web research, use Research mode.
    Uses Unified Context Manager for full conversation history.
    """
    try:
        question = request.GET.get('question', '')
        selected_models = request.GET.get('models', 'gpt-4o-mini')
        current_url = request.GET.get('current_url', '')

        if not question:
            return JsonResponse({'error': 'No question provided'}, status=400)

        session_id = _get_session_id(request)

        integration = get_context_integration()
        context_mgr = get_context_manager()

        context_mgr.update_metadata(
            session_id=session_id,
            mode=ContextMode.THINKING,
            current_url=current_url,
            user_timezone=request.GET.get('user_timezone'),
            user_time=request.GET.get('user_time')
        )

        context_mgr.add_user_message(session_id, question)

        messages = context_mgr.get_formatted_messages_for_api(session_id)

        models = [m.strip() for m in selected_models.split(',') if m.strip()]
        responses = {}

        for model in models:
            try:
                start_time = time.time()

                response, _sources = ds.create_agent_response(
                    user_input=question,
                    message_list=messages,
                    model=model,
                    current_url=current_url,
                    user_timezone=request.GET.get('user_timezone'),
                    user_time=request.GET.get('user_time'),
                    session_id=session_id,
                )

                responses[model] = _wrap_for_client(response, session_id)

                response_time_ms = int((time.time() - start_time) * 1000)
                context_mgr.add_assistant_message(
                    session_id=session_id,
                    content=response,
                    model=model,
                    tools_used=[],
                    response_time_ms=response_time_ms
                )

            except Exception as e:
                responses[model] = f"Error: {_safe_error_message(e, f'model {model}')}"

        stats = context_mgr.get_session_stats(session_id)

        first_response = next(iter(responses.values()), "No response")
        logger.info(f"Interaction [agent_chat]: URL={current_url}, Q='{question[:50]}...', Resp='{str(first_response)[:50]}...'")

        result = {
            'resp': responses,
            'context_stats': {
                'session_id': session_id,
                'mode': stats['mode'],
                'message_count': stats['message_count'],
                'token_count': stats['token_count'],
                'fetched_context': stats['fetched_context_counts']
            }
        }

        return JsonResponse(result)

    except Exception as e:
        logger.error(f"Agent response error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)



@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def chat_response_stream(request: HttpRequest) -> StreamingHttpResponse:
    """
    Thinking Mode Streaming: Process user questions using LLM with available MCP tools.
    Note: Browser automation has been removed. For web research, use Research mode.
    """
    try:
        question = request.GET.get('question', '')
        selected_models = request.GET.get('models', 'gpt-4o-mini')
        current_url = request.GET.get('current_url', '')

        if not question:
            return JsonResponse({'error': 'No question provided'}, status=400)

        session_id = _get_session_id(request)

        user_timezone = request.GET.get('user_timezone')
        user_time = request.GET.get('user_time')

        context_mgr = get_context_manager()
        integration = get_context_integration()
        context_mgr.update_metadata(
            session_id=session_id,
            mode=ContextMode.THINKING,
            current_url=current_url,
            user_timezone=user_timezone,
            user_time=user_time
        )

        context_mgr.add_user_message(session_id, question)

        messages = context_mgr.get_formatted_messages_for_api(session_id)

        model = selected_models.split(',')[0].strip()

        def event_stream():
            """Generator for SSE streaming"""
            try:
                yield b'event: connected\ndata: {"status": "connected"}\n\n'
                yield _build_status_frame("Preparing context")

                start_time = time.time()
                aggregated_chunks: List[str] = []

                stream_generator, stream_state = ds.create_agent_response_stream(
                    user_input=question,
                    message_list=messages,
                    model=model,
                    current_url=current_url,
                    user_timezone=user_timezone,
                    user_time=user_time,
                    session_id=session_id,
                )

                previous_loop = None
                try:
                    previous_loop = asyncio.get_event_loop()
                except RuntimeError:
                    previous_loop = None

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                stream_iter = stream_generator.__aiter__()
                drafting_sent = False

                try:
                    while True:
                        chunk = loop.run_until_complete(stream_iter.__anext__())
                        if not chunk:
                            continue

                        aggregated_chunks.append(chunk)
                        if not drafting_sent:
                            drafting_sent = True
                            yield _build_status_frame("Drafting answer")

                        yield f'data: {json.dumps({"content": chunk, "done": False})}\n\n'.encode('utf-8')
                except StopAsyncIteration:
                    pass
                finally:
                    try:
                        loop.run_until_complete(stream_iter.aclose())
                    except Exception as e:
                        logger.debug(f"stream_iter.aclose() error: {e}")
                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception as e:
                        logger.debug(f"shutdown_asyncgens() error: {e}")
                    loop.close()
                    asyncio.set_event_loop(previous_loop)

                final_response = ""
                if stream_state:
                    final_response = stream_state.get("final_output") or ""
                if not final_response and aggregated_chunks:
                    final_response = "".join(aggregated_chunks)

                try:
                    xbrl_sources = build_xbrl_sources(session_id, request.build_absolute_uri)
                except Exception as xbrl_err:
                    logger.debug(f"XBRL source collection failed (non-critical): {xbrl_err}")
                    xbrl_sources = []

                response_time_ms = int((time.time() - start_time) * 1000)
                context_mgr.add_assistant_message(
                    session_id=session_id,
                    content=final_response,
                    model=model,
                    sources_used=xbrl_sources,
                    tools_used=[],
                    response_time_ms=response_time_ms
                )

                stats = context_mgr.get_session_stats(session_id)

                yield _build_status_frame("Finalizing response")
                final_data = {
                    "content": "",
                    "done": True,
                    "wrapped_content": _wrap_for_client(final_response, session_id),
                    "used_sources": xbrl_sources,
                    "used_urls": [s.get('url') for s in xbrl_sources if isinstance(s, dict) and s.get('url')],
                    "context_stats": {
                        'session_id': session_id,
                        'message_count': stats['message_count'],
                        'token_count': stats['token_count'],
                        'response_time_ms': response_time_ms
                    }
                }
                yield f'data: {json.dumps(final_data)}\n\n'.encode('utf-8')

                logger.info(f"Interaction [normal_stream]: URL={current_url}, Q='{question[:50]}...', Resp='{final_response[:50]}...'")

            except Exception as e:
                error_msg = _safe_error_message(e, "streaming")
                yield f'data: {json.dumps({"error": error_msg, "done": True})}\n\n'.encode('utf-8')

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream'
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'

        return response

    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)


@csrf_exempt
@ratelimit(key='ip', rate=settings.API_RATE_LIMIT, method='ALL', block=True)
def adv_response_stream(request: HttpRequest) -> StreamingHttpResponse:
    """Process streaming advanced chat response from selected models using SSE"""
    try:
        question = request.GET.get('question', '')
        selected_models = request.GET.get('models', 'gpt-4o-mini')
        current_url = request.GET.get('current_url', '')
        preferred_links_json = request.GET.get('preferred_links', '')

        preferred_links = []
        if preferred_links_json:
            try:
                preferred_links = json.loads(preferred_links_json)
                logger.info(f"Received {len(preferred_links)} preferred links for streaming")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse preferred links JSON")

        if not question:
            return JsonResponse({'error': 'No question provided'}, status=400)

        session_id = _get_session_id(request)

        integration = get_context_integration()
        context_mgr = get_context_manager()
        context_mgr.update_metadata(
            session_id=session_id,
            mode=ContextMode.RESEARCH,
            current_url=current_url,
            user_timezone=request.GET.get('user_timezone'),
            user_time=request.GET.get('user_time')
        )

        context_mgr.add_user_message(session_id, question)

        messages = context_mgr.get_formatted_messages_for_api(session_id)

        model = selected_models.split(',')[0].strip()

        def event_stream():
            """Generator for SSE streaming"""
            try:
                yield b'event: connected\ndata: {"status": "connected"}\n\n'
                yield _build_status_frame("Preparing context", "Research mode")
                yield _build_status_frame("Searching the web")

                start_time = time.time()
                full_response = ""
                source_entries = []

                stream_generator, stream_state = ds.create_advanced_response_streaming(
                    question,
                    messages,
                    model,
                    preferred_links,
                    user_timezone=request.GET.get('user_timezone'),
                    user_time=request.GET.get('user_time')
                )

                previous_loop = None
                try:
                    previous_loop = asyncio.get_event_loop()
                except RuntimeError:
                    previous_loop = None

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                stream_iter = stream_generator.__aiter__()
                drafting_sent = False

                try:
                    while True:
                        try:
                            text_chunk, entries = loop.run_until_complete(stream_iter.__anext__())
                        except StopAsyncIteration:
                            break

                        # Status event from research engine streaming
                        if text_chunk is None and isinstance(entries, dict) and "label" in entries:
                            yield _build_status_frame(entries["label"], entries.get("detail"))
                            continue

                        if text_chunk:
                            full_response += text_chunk
                            if not drafting_sent:
                                drafting_sent = True
                                yield _build_status_frame("Drafting answer")
                            yield f'data: {json.dumps({"content": text_chunk, "done": False})}\n\n'.encode('utf-8')

                        if isinstance(entries, list) and entries:
                            source_entries = [dict(entry) for entry in entries if entry]
                finally:
                    try:
                        loop.run_until_complete(stream_iter.aclose())
                    except Exception as e:
                        logger.debug(f"stream_iter.aclose() error: {e}")
                    try:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                    except Exception as e:
                        logger.debug(f"shutdown_asyncgens() error: {e}")
                    loop.close()
                    asyncio.set_event_loop(previous_loop)

                if source_entries:
                    integration.add_search_results(session_id, source_entries)

                try:
                    xbrl_sources = build_xbrl_sources(session_id, request.build_absolute_uri)
                    source_entries = merge_xbrl_sources(source_entries, xbrl_sources)
                except Exception as xbrl_err:
                    logger.debug(f"XBRL source collection failed (non-critical): {xbrl_err}")

                response_time_ms = int((time.time() - start_time) * 1000)
                context_mgr.add_assistant_message(
                    session_id=session_id,
                    content=full_response,
                    model=model,
                    sources_used=source_entries,
                    tools_used=["web_search"],
                    response_time_ms=response_time_ms
                )

                stats = context_mgr.get_session_stats(session_id)

                yield _build_status_frame("Finalizing response")
                final_data = {
                    "content": "",
                    "done": True,
                    "wrapped_content": _wrap_for_client(full_response, session_id),
                    "used_sources": source_entries,
                    "used_urls": [s.get('url') for s in source_entries if isinstance(s, dict) and s.get('url')],
                    "context_stats": {
                        'session_id': session_id,
                        'message_count': stats['message_count'],
                        'token_count': stats['token_count'],
                        'response_time_ms': response_time_ms
                    }
                }
                yield f'data: {json.dumps(final_data)}\n\n'.encode('utf-8')

                logger.info(f"Interaction [advanced_stream]: URL={current_url}, Q='{question[:50]}...', Resp='{full_response[:50]}...'")

            except Exception as e:
                error_msg = _safe_error_message(e, "advanced_streaming")
                yield f'data: {json.dumps({"error": error_msg, "done": True})}\n\n'.encode('utf-8')

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream'
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'

        return response

    except Exception as e:
        logger.error(f"Advanced stream error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)



@csrf_exempt
def auto_scrape(request: HttpRequest) -> JsonResponse:
    """
    Automatically scrape the current page if not already in context.
    Triggered when agent launches/opens on a page.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method; use POST.'}, status=405)

    try:
        data = json.loads(request.body) if request.body else {}
        current_url = data.get('current_url') or data.get('currentUrl')
        
        if not current_url:
            return JsonResponse({'error': 'No URL provided'}, status=400)
            
        session_id = _get_session_id(request)
        
        integration = get_context_integration()
        
        scraped_urls = integration.get_scraped_urls(session_id)
        if current_url in scraped_urls:
            logger.info(f"URL already scraped, skipping: {current_url}")
            return JsonResponse({'status': 'skipped', 'reason': 'already_scraped'})
            
        logger.info(f"Auto-scraping URL: {current_url}")
        
        scrape_result_json = scrape_url(current_url)
        scrape_result = json.loads(scrape_result_json)
        
        if "error" in scrape_result:
            logger.error(f"Auto-scrape failed: {scrape_result['error']}")
            return JsonResponse({'error': scrape_result['error']}, status=500)
            
        content = scrape_result.get("content", "")
        
        integration.add_web_content(
            request=request,
            text_content=content,
            current_url=current_url,
            source_type="js_scraping",
            session_id=session_id
        )
        
        return JsonResponse({'status': 'success', 'url': current_url})
        
    except Exception as e:
        logger.error(f"Auto-scrape error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)


@csrf_exempt
def add_webtext(request: HttpRequest) -> JsonResponse:
    """
    Handle appending the site's text to the message list.
    Now uses Unified Context Manager for JS scraped content.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method; use POST.'}, status=405)

    try:
        data = json.loads(request.body) if request.body else {}
        text_content = data.get('textContent', '')
        current_url = data.get('currentUrl', '')

        if not text_content:
            return JsonResponse({'error': 'No text content provided'}, status=400)

        logger.info(f"Receiving web content from {current_url}, length={len(text_content)}")

        session_id = _get_session_id(request)

        integration = get_context_integration()

        MAX_CONTENT_LENGTH = 10000
        if len(text_content) > MAX_CONTENT_LENGTH:
            text_content = text_content[:MAX_CONTENT_LENGTH] + "... (truncated)"

        integration.add_web_content(
            request=request,
            text_content=text_content,
            current_url=current_url,
            source_type="js_scraping"
        )

        stats = integration.get_context_stats(session_id)

        logger.info(f"Interaction [web_content]: URL={current_url}, Msg='Web content received'")

        return JsonResponse({
            'status': 'success',
            'session_id': session_id,
            'context_stats': {
                'message_count': stats['message_count'],
                'token_count': stats['token_count'],
                'js_scraping_count': stats['fetched_context_counts'].get('js_scraping', 0)
            }
        })

    except Exception as e:
        logger.error(f"Input webtext error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)


@csrf_exempt
def clear(request: HttpRequest) -> JsonResponse:
    """
    Clear conversation messages and optionally preserve scraped web content.
    Now uses Unified Context Manager.
    """
    try:
        preserve_web = request.GET.get('preserve_web', 'false').lower() == 'true'

        logger.info(f"Clearing messages, preserve_web={preserve_web}")

        session_id = _get_session_id(request)

        integration = get_context_integration()

        integration.clear_messages(request, preserve_web_content=preserve_web)

        logger.info(f"Interaction [clear]: Msg='Cleared messages'")

        return JsonResponse({
            'status': 'success',
            'session_id': session_id,
            'preserved_web_content': preserve_web
        })

    except Exception as e:
        logger.error(f"Clear messages error: {e}", exc_info=True)
        return JsonResponse({'error': _safe_error_message(e, request.path)}, status=500)


@csrf_exempt
def get_memory_stats(request: HttpRequest) -> JsonResponse:
    """
    Get context statistics for current session.
    Now uses Unified Context Manager.
    """
    try:
        session_id = _get_session_id(request)

        integration = get_context_integration()

        stats = integration.get_context_stats(session_id)

        return JsonResponse({
            'stats': {
                'session_id': session_id,
                'mode': stats['mode'],
                'message_count': stats['message_count'],
                'token_count': stats['token_count'],
                'fetched_context_counts': stats['fetched_context_counts'],
                'total_fetched_items': stats['total_fetched_items'],
                'current_url': stats.get('current_url'),
                'last_updated': stats.get('last_updated'),
                'using_unified_context': True
            }
        })

    except Exception as e:
        return JsonResponse({'stats': {"error": _safe_error_message(e, "get_stats"), "using_unified_context": False}}, status=500)





def health(request: HttpRequest) -> JsonResponse:
    """
    Health check endpoint for load balancers and monitoring.
    Returns 200 OK if the service is running.
    """
    return JsonResponse({
        'status': 'healthy',
        'service': 'fingpt-backend',
        'timestamp': datetime.now().isoformat(),
        'version': _get_version(),
        'using_unified_context': True
    })


@csrf_exempt
def get_sources(request: HttpRequest) -> JsonResponse:
    """Get sources for a query"""
    query = request.GET.get('query', '')
    current_url = request.GET.get('current_url')
    session_id = _get_session_id(request)
    
    sources = ds.get_sources(query, current_url=current_url, session_id=session_id)

    logger.info(f"Interaction [sources]: URL={current_url or 'N/A'}, Q='Source request: {query}'")

    return JsonResponse({'resp': sources})


def log_question(request: HttpRequest) -> JsonResponse:
    """Legacy question logging (redirects to enhanced logging)"""
    question = request.GET.get('question', '')
    button_clicked = request.GET.get('button', '')
    current_url = request.GET.get('current_url', '')

    if question and button_clicked and current_url:
        logger.info(f"Interaction [{button_clicked}]: URL={current_url}, Q='{question}'")

    return JsonResponse({'status': 'success'})


def get_preferred_urls(request: HttpRequest) -> JsonResponse:
    """Retrieve preferred URLs from storage"""
    manager = get_manager()
    urls = manager.get_links()
    return JsonResponse({'urls': urls})


@csrf_exempt
def add_preferred_url(request: HttpRequest) -> JsonResponse:
    """Add new preferred URL to storage"""
    if request.method == 'POST':
        try:
            new_url = request.POST.get('url')
            if not new_url and request.body:
                data = json.loads(request.body)
                new_url = data.get('url')

            if new_url:
                manager = get_manager()
                success = manager.add_link(new_url)

                if success:
                    logger.info(f"Interaction [add_url]: URL={new_url}, Msg='Added preferred URL: {new_url}'")
                    return JsonResponse({'status': 'success'})
                else:
                    return JsonResponse({'status': 'exists'})
        except Exception as e:
            logger.error(f"Error adding preferred URL: {e}")
            return JsonResponse({'status': 'error', 'message': _safe_error_message(e, request.path)}, status=500)

    return JsonResponse({'status': 'failed'}, status=400)


@csrf_exempt
def sync_preferred_urls(request: HttpRequest) -> JsonResponse:
    """Sync preferred URLs from frontend to backend storage"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            urls = data.get('urls', [])

            manager = get_manager()
            manager.set_links(urls)

            return JsonResponse({'status': 'success', 'synced': len(urls)})
        except Exception as e:
            logger.error(f"Error syncing preferred URLs: {e}")
            return JsonResponse({'status': 'error', 'message': _safe_error_message(e, request.path)}, status=500)

    return JsonResponse({'status': 'failed'}, status=400)


def get_available_models(request: HttpRequest) -> JsonResponse:
    """Get list of available models with their configurations"""
    models = []
    for model_id, config in MODELS_CONFIG.items():
        models.append({
            'id': model_id,
            'provider': config['provider'],
            'description': config['description'],
            'supports_mcp': config['supports_mcp'],
            'supports_advanced': config['supports_advanced'],
            'display_name': f"{model_id} - {config['description']}"
        })
    return JsonResponse({'models': models})
