"""
OpenAI Responses API integration for web search functionality.

Optimized version with preview/snippet functionality removed for better performance.
"""

import os
import re
import logging
import asyncio
import json
from typing import List, Dict, Optional, Any, Iterable, AsyncGenerator, Tuple, Set
from urllib.parse import urlparse
from openai import OpenAI, AsyncOpenAI
from dotenv import load_dotenv
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(backend_dir / '.env')

logger = logging.getLogger(__name__)

from api.utils.llm_debug_logger import log_llm_payload

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY missing; OpenAI-backed features will be unavailable until configured.")
    sync_client = None
    async_client = None
else:
    sync_client = OpenAI(api_key=OPENAI_API_KEY)
    async_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

USE_RESPONSES_API = os.getenv("USE_OPENAI_RESPONSES_API", "true").lower() == "true"


def _get_site_name(url: str) -> str:
    """Extract a readable site name from URL."""
    try:
        hostname = urlparse(url).netloc
        if not hostname:
            return "Source"

        hostname = re.sub(r'^www\.', '', hostname, flags=re.IGNORECASE)

        parts = hostname.split('.')
        if len(parts) >= 2:
            site_name = parts[-2] if parts[-1] in ['com', 'org', 'net', 'edu', 'gov', 'io', 'co'] else parts[0]
            return site_name.capitalize()

        return hostname.capitalize()
    except Exception:
        return "Source"


def _format_display_url(url: str, max_length: int = 30) -> str:
    """Format URL for display with truncation."""
    try:
        parsed = urlparse(url)
        display = f"{parsed.netloc}{parsed.path or ''}"
        if parsed.query:
            display += f"?{parsed.query}"

        display = re.sub(r'^www\.', '', display, flags=re.IGNORECASE)

        if len(display) > max_length:
            display = display[:max_length - 3] + "..."

        return display
    except Exception:
        return url[:max_length] if len(url) > max_length else url


def extract_citations_from_response(response) -> List[Dict[str, Any]]:
    """
    Extract URL citations from OpenAI Responses API response.

    Args:
        response: The response object from OpenAI Responses API

    Returns:
        List of dictionaries containing citation information
    """
    citations = []

    try:
        if hasattr(response, 'output') and response.output:
            for output_item in response.output:
                if hasattr(output_item, 'type') and output_item.type == 'message':
                    if hasattr(output_item, 'content') and output_item.content:
                        for content_block in output_item.content:
                            if hasattr(content_block, 'annotations') and content_block.annotations:
                                for annotation in content_block.annotations:
                                    if annotation.type == 'url_citation':
                                        citations.append({
                                            'url': annotation.url,
                                            'title': getattr(annotation, 'title', '') or '',
                                            'snippet': getattr(annotation, 'text', '') or getattr(annotation, 'snippet', '') or '',
                                            'type': 'url_citation'
                                        })
                                    elif annotation.type == 'file_citation':
                                        citations.append({
                                            'file_id': getattr(annotation, 'file_id', None),
                                            'quote': getattr(annotation, 'quote', '') or '',
                                            'type': 'file_citation'
                                        })

        logger.info(f"Extracted {len(citations)} citations from response")
        for idx, citation in enumerate(citations, 1):
            if citation['type'] == 'url_citation':
                logger.info(f"  Citation {idx}: {citation['url']} - {citation.get('title', 'No title')}")

    except Exception as e:
        logger.error(f"Error extracting citations: {e}")

    return citations


def _normalize_citation_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize citation metadata to the structure expected downstream.
    """
    url = entry.get('url')
    if not url:
        return {}

    normalized_title = entry.get('title') or _format_display_url(url)
    snippet = (entry.get('snippet') or '').strip()

    return {
        'url': url,
        'title': normalized_title,
        'snippet': snippet,
        'site_name': entry.get('site_name') or _get_site_name(url),
        'display_url': entry.get('display_url') or _format_display_url(url),
        'icon': None,
        'provisional': bool(entry.get('provisional', False))
    }


_TOOL_URL_PATTERN = re.compile(r'https?://[^\s<>"\'\]]+', re.IGNORECASE)
_MAX_PROVISIONAL_SOURCES = 12

# ---------------------------------------------------------------------------
# Output validation: detect raw JSON / tool-call leaks
# ---------------------------------------------------------------------------
_RAW_JSON_PATTERNS = [
    re.compile(r'^\s*\{.*"search_query"', re.DOTALL),
    re.compile(r'^\s*\{.*"tool_call"', re.DOTALL),
    re.compile(r'^\s*\{.*"function_call"', re.DOTALL),
    re.compile(r'^\s*\{.*"response_length"', re.DOTALL),
    re.compile(r'^\s*\{.*"q"\s*:', re.DOTALL),
]


def _is_raw_json_leak(text: str) -> bool:
    """
    Detect if a response looks like a leaked internal tool call / raw JSON
    rather than a human-readable answer.

    Returns True if the text appears to be raw JSON that should not be shown to users.
    """
    if not text or not text.strip():
        return False

    stripped = text.strip()

    # Quick check: if it starts with { and ends with }, it's likely JSON
    if stripped.startswith('{') and stripped.endswith('}'):
        try:
            parsed = json.loads(stripped)
            # Known internal tool-call keys that should never reach users
            internal_keys = {"search_query", "tool_call", "function_call", "response_length", "tool_name", "tool_input"}
            if isinstance(parsed, dict) and internal_keys & set(parsed.keys()):
                logger.warning(f"[OUTPUT VALIDATION] Detected raw JSON tool-call leak: {stripped[:200]}")
                return True
        except json.JSONDecodeError:
            pass

    # Pattern-based fallback
    for pattern in _RAW_JSON_PATTERNS:
        if pattern.search(stripped):
            logger.warning(f"[OUTPUT VALIDATION] Detected raw JSON pattern in response: {stripped[:200]}")
            return True

    return False


def _safe_json_loads(value: str) -> Optional[Any]:
    """
    Attempt to parse a string as JSON. Returns None if parsing fails.
    """
    try:
        return json.loads(value)
    except Exception:
        return None


def _collect_sources_from_payload(payload: Any, visited_urls: Set[str], seen_nodes: Set[int]) -> List[Dict[str, Any]]:
    """
    Recursively walk payloads emitted by the Responses web_search tool and extract URL candidates.
    """
    if payload is None:
        return []

    node_id = id(payload)
    if node_id in seen_nodes:
        return []
    seen_nodes.add(node_id)

    results: List[Dict[str, Any]] = []
    if isinstance(payload, str):
        payload_str = payload.strip()
        if payload_str.startswith("{") or payload_str.startswith("["):
            parsed = _safe_json_loads(payload_str)
            if parsed is not None:
                results.extend(_collect_sources_from_payload(parsed, visited_urls, seen_nodes))
        else:
            for match in _TOOL_URL_PATTERN.findall(payload_str):
                if len(visited_urls) >= _MAX_PROVISIONAL_SOURCES:
                    break
                if match in visited_urls:
                    continue
                provisional = _normalize_citation_entry({
                    "url": match,
                    "provisional": True
                })
                if provisional:
                    visited_urls.add(match)
                    results.append(provisional)
        return results

    if isinstance(payload, list):
        for item in payload:
            if len(visited_urls) >= _MAX_PROVISIONAL_SOURCES:
                break
            results.extend(_collect_sources_from_payload(item, visited_urls, seen_nodes))
        return results

    if isinstance(payload, dict):
        url = payload.get("url") or payload.get("link") or payload.get("href")
        if url and url not in visited_urls and len(visited_urls) < _MAX_PROVISIONAL_SOURCES:
            entry = {
                "url": url,
                "title": payload.get("title") or payload.get("name") or payload.get("headline") or payload.get("page_title"),
                "snippet": payload.get("snippet") or payload.get("description") or payload.get("summary") or payload.get("text") or "",
                "site_name": payload.get("site_name") or payload.get("source") or payload.get("publisher"),
                "display_url": payload.get("display_url") or payload.get("displayUrl") or payload.get("formattedUrl"),
                "provisional": True
            }
            normalized = _normalize_citation_entry(entry)
            if normalized:
                visited_urls.add(url)
                results.append(normalized)

        candidate_keys = [
            "results", "items", "value", "web_results", "web_pages", "documents",
            "data", "webSearchResults", "entries"
        ]
        for key in candidate_keys:
            if key in payload and len(visited_urls) < _MAX_PROVISIONAL_SOURCES:
                results.extend(_collect_sources_from_payload(payload[key], visited_urls, seen_nodes))

        for value in payload.values():
            if len(visited_urls) >= _MAX_PROVISIONAL_SOURCES:
                break
            results.extend(_collect_sources_from_payload(value, visited_urls, seen_nodes))

        return results

    return results


def _gather_text_fragments(payload: Any, seen_nodes: Set[int]) -> List[str]:
    """
    Recursively collect textual fragments from tool payloads for buffering.
    """
    if payload is None:
        return []

    node_id = id(payload)
    if node_id in seen_nodes:
        return []
    seen_nodes.add(node_id)

    fragments: List[str] = []
    if isinstance(payload, str):
        fragments.append(payload)
        return fragments

    if isinstance(payload, list):
        for item in payload:
            fragments.extend(_gather_text_fragments(item, seen_nodes))
        return fragments

    if isinstance(payload, dict):
        for key in ("text", "output_text", "result", "delta"):
            value = payload.get(key)
            if isinstance(value, str):
                fragments.append(value)
        for value in payload.values():
            fragments.extend(_gather_text_fragments(value, seen_nodes))
        return fragments

    return fragments


def _extract_sources_from_tool_event(event_type: str, event: Any, buffers: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """
    Parse Responses tool output events to surface provisional sources before citations land.
    """
    if not event_type or "tool" not in event_type:
        return []

    tool_name = getattr(event, "tool_name", None) or getattr(event, "tool_type", None)
    if not tool_name and "web_search" in event_type:
        tool_name = "web_search"

    if tool_name and "web_search" not in str(tool_name):
        return []

    tool_call_id = getattr(event, "tool_call_id", None) or getattr(event, "id", None) or getattr(event, "tool_call", None)
    buffer_key = str(tool_call_id) if tool_call_id is not None else None

    payloads: List[Any] = []
    for attr in ("output_json", "output", "delta", "result", "content"):
        if hasattr(event, attr):
            value = getattr(event, attr)
            if value not in (None, "", []):
                payloads.append(value)

    if event_type.endswith(".delta") and buffer_key:
        fragment_nodes: Set[int] = set()
        for payload in payloads:
            fragments = _gather_text_fragments(payload, fragment_nodes)
            if fragments:
                buffers.setdefault(buffer_key, []).extend(fragments)
        return []

    if buffer_key and buffer_key in buffers:
        buffered_text = "".join(buffers.pop(buffer_key))
        if buffered_text:
            payloads.append(buffered_text)

    collected: List[Dict[str, Any]] = []
    visited_urls: Set[str] = set()
    seen_nodes: Set[int] = set()

    for payload in payloads:
        if len(visited_urls) >= _MAX_PROVISIONAL_SOURCES:
            break
        extracted = _collect_sources_from_payload(payload, visited_urls, seen_nodes)
        if extracted:
            collected.extend(extracted)

    return collected


def prepare_search_prompt(user_query: str, preferred_domains: List[str] = None) -> str:
    """
    Prepare an enhanced prompt that guides the web search behavior.

    Args:
        user_query: The user's original question
        preferred_domains: List of preferred domains to search

    Returns:
        Enhanced prompt string
    """
    prompt_parts = []

    if preferred_domains:
        domains_str = ', '.join(preferred_domains)
        prompt_parts.append(
            f"When searching for numerical financial data, ONLY use these authoritative sources: "
            f"{domains_str}, finance.yahoo.com, bloomberg.com, marketwatch.com, nasdaq.com. "
            f"Do NOT use data from unfamiliar or unverified websites."
        )

    prompt_parts.append(user_query)

    prompt_parts.append(
        "\nProvide a comprehensive answer with citations from reputable sources. "
        "For every numerical claim, cite the exact source."
    )

    return '\n'.join(prompt_parts)


async def create_responses_api_search_async(
    user_query: str,
    message_history: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    preferred_links: List[str] = None,
    stream: bool = False,
    user_timezone: str = None,
    user_time: str = None
):
    """
    Async version: Create a response using OpenAI Responses API with web search.

    Args:
        user_query: The user's question
        message_history: Previous conversation messages
        model: OpenAI model to use (must support Responses API)
        preferred_links: List of preferred URLs/domains to search
        stream: If True, returns async generator; if False, returns Tuple[str, List[Dict[str, Any]]]
        user_timezone: User's IANA timezone
        user_time: User's current time in ISO format

    Returns:
        If stream=False: Tuple of (response_text, list_of_source_entries)
        If stream=True: Async generator yielding (chunk_text, source_entries) tuples
    """
    if async_client is None:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot execute OpenAI search.")

    try:
        preferred_domains = []
        if preferred_links:
            for link in preferred_links:
                domain = urlparse(link).netloc
                if domain and domain not in preferred_domains:
                    preferred_domains.append(domain)

        enhanced_prompt = prepare_search_prompt(user_query, preferred_domains)

        system_instructions = (
            "Instructions: You are an expert and a helpful financial assistant with access to web search. "
            "Decide whether to search based on intent: use web_search when the question needs external or current information; "
            "if the user asks to recap/summarize/clarify something from this conversation, answer from the existing messages and do NOT search. "
            "Cite your sources inline and provide comprehensive, accurate answers based on calculations or fetched sources. "
            "Focus on factual information from reputable sources. "
            "\n\nDATA SOURCE REQUIREMENTS:\n"
            "For numerical financial data (prices, volumes, market cap, ratios, percentage changes, etc.):\n"
            "1. ONLY use data from these authoritative sources: Yahoo Finance, Bloomberg, MarketWatch, "
            "Nasdaq.com, SEC EDGAR, Reuters, CNBC, Investing.com, Google Finance. "
            "Do NOT trust data from unfamiliar or unverified websites.\n"
            "2. Every numerical claim MUST include its source. If you cannot cite a specific authoritative source "
            "for a number, state that the data could not be verified rather than presenting it without attribution.\n"
            "3. Always verify the DATE on any data you retrieve. If the user asks for 'today's price' and "
            "you find data from a different date, clearly state the actual date of the data.\n"
            "4. NEVER present data from a future date. If a source claims to have data for a date that hasn't "
            "occurred yet, that source is unreliable — discard it.\n"
            "5. When multiple sources disagree on a number, prefer Yahoo Finance data as the authoritative source.\n"
            "\n\nIMPORTANT - Mathematical Formatting:\n"
            "Use LaTeX with \\(...\\) for inline math and $$...$$ for display equations.\n"
            "NEVER use single $...$ for math: financial prose contains currency "
            "(\"$1.00\", \"$13.63 billion\") that would collide with math delimiters "
            "and corrupt rendering. The renderer treats single $ as literal text.\n"
            "Math delimiters are for typeset SYMBOLIC expressions only. Prose lines "
            "that just mention currency values (e.g., \"Assets ($106.62B) = Liabilities "
            "($43.01B) + Equity ($63.61B)\") must stay as plain text — do NOT wrap them "
            "in \\(...\\) or $$...$$.\n\n"
            "Display equations - use $$...$$:\n"
            "$$C = S_0 N(d_1) - K e^{-rT} N(d_2)$$\n"
            "$$d_1 = \\frac{\\ln(S_0/K) + (r + \\sigma^2/2)T}{\\sigma\\sqrt{T}}$$\n\n"
            "Inline math - use \\(...\\):\n"
            "where \\(S_0 = 262.76\\), \\(K = 270\\), \\(r = 0.05\\), \\(\\sigma = 0.2974\\)\n\n"
            "Do NOT use:\n"
            "- Single $...$ delimiters for math (use \\(...\\) instead)\n"
            "- Square brackets [...] for equations (use \\[...\\] or $$...$$)\n"
            "- Plain text like S0 = 100 or d1 ≈ -0.0976\n"
            "- Parentheses (S_0) for inline variables\n"
            "- Unicode symbols like ≈ or · outside of math delimiters"
        )

        if user_timezone or user_time:
            from datascraper.market_time import build_market_time_context
            time_context = build_market_time_context(user_timezone, user_time)
            if time_context:
                system_instructions += f"\n\n{time_context}"

        conversation_context = ""

        if message_history:
            system_msg = None
            conversation_parts = []

            for msg in message_history:
                content = msg.get('content', '')

                if content.startswith("[SYSTEM MESSAGE]:"):
                    system_msg = content.replace("[SYSTEM MESSAGE]:", "").strip()
                elif content.startswith("[USER MESSAGE]:"):
                    user_content = content.replace("[USER MESSAGE]:", "").strip()
                    conversation_parts.append(f"User: {user_content}")
                elif content.startswith("[ASSISTANT MESSAGE]:"):
                    assistant_content = content.replace("[ASSISTANT MESSAGE]:", "").strip()
                    conversation_parts.append(f"Assistant: {assistant_content}")

            if system_msg:
                system_instructions = system_msg + "\n\n" + system_instructions

            if len(conversation_parts) > 1:
                conversation_context = "\n\n[CONVERSATION HISTORY]:\n" + "\n".join(conversation_parts[:-1])

        combined_input = system_instructions
        if conversation_context:
            combined_input += conversation_context
        combined_input += f"\n\n[CURRENT QUERY]:\n{enhanced_prompt}"

        logger.info(f"Calling OpenAI Responses API with model: {model} (stream={stream})")
        logger.info(f"Web search enabled for query: {user_query[:100]}...")
        logger.info(f"Including {len(message_history)} messages from conversation history")
        log_llm_payload(
            call_site="create_responses_api_search_async",
            model=model, provider="openai_responses",
            messages=combined_input, stream=stream,
            extra={"tools": "web_search", "history_count": len(message_history)},
        )

        response = await async_client.responses.create(
            model=model,
            input=combined_input,
            tools=[{"type": "web_search"}],
            tool_choice="auto",
            parallel_tool_calls=True,
            stream=stream
        )

        if stream:
            return _stream_response_chunks(response)

        response_text = ""
        if hasattr(response, 'output_text') and response.output_text:
            response_text = response.output_text
        elif hasattr(response, 'output') and response.output:
            if isinstance(response.output, str):
                response_text = response.output
            elif isinstance(response.output, list):
                for item in response.output:
                    if hasattr(item, 'content'):
                        response_text = str(item.content)
                        break

        if not response_text:
            logger.warning("Could not extract text from response. Response structure may have changed.")

        # --- Output validation: intercept raw JSON / tool-call leaks ---
        if _is_raw_json_leak(response_text):
            logger.warning("[OUTPUT VALIDATION] Retrying due to raw JSON leak in response")
            # Retry once with a more explicit prompt
            retry_response = await async_client.responses.create(
                model=model,
                input=combined_input + "\n\nIMPORTANT: Provide a clear, human-readable answer. Do NOT return raw JSON or tool call specifications.",
                tools=[{"type": "web_search"}],
                tool_choice="auto",
                parallel_tool_calls=True,
                stream=False
            )
            retry_text = ""
            if hasattr(retry_response, 'output_text') and retry_response.output_text:
                retry_text = retry_response.output_text
            elif hasattr(retry_response, 'output') and retry_response.output:
                if isinstance(retry_response.output, str):
                    retry_text = retry_response.output
                elif isinstance(retry_response.output, list):
                    for item in retry_response.output:
                        if hasattr(item, 'content'):
                            retry_text = str(item.content)
                            break

            if retry_text and not _is_raw_json_leak(retry_text):
                logger.info("[OUTPUT VALIDATION] Retry succeeded with clean response")
                response_text = retry_text
                response = retry_response
            else:
                logger.error("[OUTPUT VALIDATION] Retry also produced invalid output, returning error message")
                response_text = "I wasn't able to retrieve that information. Please try rephrasing your question, or switch to Thinking mode for more reliable results."

        citations = extract_citations_from_response(response)
        source_entries: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        for citation in citations:
            if citation.get('type') != 'url_citation':
                continue
            normalized = _normalize_citation_entry(citation)
            url = normalized.get('url')
            if not url or url in seen_urls:
                continue
            source_entries.append(normalized)
            seen_urls.add(url)

        logger.info(f"Response generated successfully with {len(source_entries)} unique source URLs")

        return response_text, source_entries

    except Exception as e:
        error_msg = f"Error in Responses API search: {str(e)}"
        if "unexpected keyword argument" in str(e).lower():
            error_msg += " (API parameter issue - check OpenAI SDK version)"
        elif "not found" in str(e).lower():
            error_msg += f" (Model '{model}' may not support Responses API)"
        elif "api key" in str(e).lower():
            error_msg += " (Authentication issue - check OPENAI_API_KEY)"

        logger.error(error_msg)
        raise Exception(error_msg) from e


async def _stream_response_chunks(stream_response) -> AsyncGenerator[Tuple[str, List[Dict[str, Any]]], None]:
    """
    Async generator that yields text chunks from streaming Responses API.
    Also collects source URLs and yields them incrementally, falling back to a final
    snapshot if no intermediate events triggered updates.

    Yields:
        Tuples of (chunk_text, source_entries)
        During streaming text: (chunk_text, [])
        On source updates: ("", source_entries) with current ordering
        Final chunk: ("", source_entries)
    """
    full_response = ""
    final_response = None
    source_order: list[str] = []
    source_map: Dict[str, Dict[str, Any]] = {}
    last_signature: Optional[List[Tuple[str, str, str, bool]]] = None
    tool_output_buffers: Dict[str, List[str]] = {}

    def _current_snapshot(force: bool = False) -> Optional[List[Dict[str, Any]]]:
        nonlocal last_signature
        ordered = [source_map[url] for url in source_order if url in source_map]
        signature = [
            (
                entry.get("url"),
                entry.get("title"),
                entry.get("snippet"),
                bool(entry.get("provisional"))
            )
            for entry in ordered
        ]
        if force or signature != last_signature:
            last_signature = signature
            return [dict(entry) for entry in ordered]
        return None

    def upsert_citation(entry: Dict[str, Any], *, provisional: Optional[bool] = None) -> bool:
        candidate = dict(entry)
        if provisional is not None:
            candidate["provisional"] = provisional

        normalized = _normalize_citation_entry(candidate)
        url = normalized.get('url')
        if not url:
            return False

        if url not in source_map:
            source_map[url] = normalized
            source_order.append(url)
            return True

        changed = False
        current = source_map[url]
        for key, value in normalized.items():
            if key == 'url':
                continue
            if key == 'provisional':
                desired = bool(value)
                existing = bool(current.get('provisional'))
                if desired != existing:
                    current['provisional'] = desired
                    changed = True
                continue
            if value and value != current.get(key):
                current[key] = value
                changed = True
        return changed

    try:
        async for event in stream_response:
            event_type = getattr(event, "type", "") or ""
            changed = False

            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                text = getattr(delta, "text", "") if hasattr(delta, "text") else delta or ""
                if text:
                    full_response += text
                    yield (text, [])
                continue

            if event_type == "response.refusal.delta":
                delta = getattr(event, "delta", "")
                text = getattr(delta, "text", "") if hasattr(delta, "text") else delta or ""
                if text:
                    full_response += text
                    yield (text, [])
                continue

            if event_type in ("response.citation.delta", "response.citation.done"):
                citation = getattr(event, "citation", None)
                if citation:
                    entry = {
                        "url": getattr(citation, "url", None),
                        "title": getattr(citation, "title", None),
                        "snippet": getattr(citation, "snippet", None) or getattr(citation, "text", None),
                        "site_name": getattr(citation, "site_name", None),
                        "display_url": getattr(citation, "display_url", None),
                    }
                    changed = upsert_citation(entry, provisional=False)
            elif event_type == "response.error":
                error_obj = getattr(event, "error", None)
                message = getattr(error_obj, "message", None) if error_obj else None
                raise RuntimeError(message or "Responses API streaming error")
            elif event_type == "response.completed":
                final_response = getattr(event, "response", None)
            elif event_type == "response.output_text.done":
                pass
            else:
                if event_type.startswith("response.tool"):
                    provisional_sources = _extract_sources_from_tool_event(event_type, event, tool_output_buffers)
                    for provisional_entry in provisional_sources:
                        changed = upsert_citation(provisional_entry, provisional=True) or changed
                else:
                    continue

            if changed:
                snapshot = _current_snapshot()
                if snapshot:
                    yield ("", snapshot)

        if not final_response:
            get_final = getattr(stream_response, "get_final_response", None)
            if callable(get_final):
                try:
                    final_candidate = await get_final()
                    if final_candidate:
                        final_response = final_candidate
                except Exception as final_err:
                    logger.debug(f"Unable to fetch final streamed response: {final_err}")

        if tool_output_buffers:
            visited_flush: Set[str] = set()
            flushed = False
            for fragments in list(tool_output_buffers.values()):
                buffered_text = "".join(fragments)
                if not buffered_text:
                    continue
                extracted = _collect_sources_from_payload(buffered_text, visited_flush, set())
                for entry in extracted:
                    flushed = upsert_citation(entry, provisional=True) or flushed
            tool_output_buffers.clear()
            if flushed:
                snapshot = _current_snapshot()
                if snapshot:
                    yield ("", snapshot)

        if final_response:
            try:
                citations = extract_citations_from_response(final_response)
                for citation in citations:
                    if citation.get('type') != 'url_citation':
                        continue
                    changed = upsert_citation(citation, provisional=False) or changed
            except Exception as citation_err:
                logger.warning(f"Failed to extract citations from final response: {citation_err}")

        # --- Output validation: detect raw JSON leak in streamed response ---
        if _is_raw_json_leak(full_response):
            logger.warning(f"[OUTPUT VALIDATION STREAM] Full streamed response is a raw JSON leak ({len(full_response)} chars)")
            # Yield an error message to replace the leaked JSON
            error_msg = "\n\nI wasn't able to retrieve that information properly. Please try rephrasing your question, or switch to Thinking mode for more reliable results."
            yield (error_msg, [])

        snapshot = _current_snapshot(force=True)
        if snapshot is None:
            snapshot = []
        logger.info(f"Streaming completed with {len(snapshot)} source URLs")
        yield ("", snapshot)

    except Exception as e:
        logger.error(f"Error in streaming response: {e}")
        raise


def create_responses_api_search(
    user_query: str,
    message_history: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    preferred_links: List[str] = None,
    user_timezone: str = None,
    user_time: str = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Sync wrapper for create_responses_api_search_async.
    """
    if async_client is None:
        raise RuntimeError("OPENAI_API_KEY is not configured; cannot execute OpenAI search.")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            task = asyncio.create_task(
                create_responses_api_search_async(
                    user_query,
                    message_history,
                    model,
                    preferred_links,
                    stream=False,
                    user_timezone=user_timezone,
                    user_time=user_time
                )
            )
            return asyncio.run_coroutine_threadsafe(task, loop).result()
        return asyncio.run(
            create_responses_api_search_async(
                user_query,
                message_history,
                model,
                preferred_links,
                stream=False,
                user_timezone=user_timezone,
                user_time=user_time
            )
        )
    except RuntimeError:
        return asyncio.run(
            create_responses_api_search_async(
                user_query,
                message_history,
                model,
                preferred_links,
                stream=False,
                user_timezone=user_timezone,
                user_time=user_time
            )
        )


def format_sources_for_frontend(
    source_entries: Iterable[Dict[str, Any]],
    current_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Prepare structured source metadata for the frontend.
    Optimized version without preview fetching.

    Args:
        source_entries: Iterable of citation metadata dicts.
        current_url: The user's active page URL.

    Returns:
        Dict with `current_page` metadata and ordered `sources` list.
    """
    entries = []
    seen: set[str] = set()
    current_entry: Optional[Dict[str, Any]] = None

    for raw_entry in source_entries or []:
        url = raw_entry.get("url")
        if not url or url in seen:
            continue
        seen.add(url)

        normalized = {
            "url": url,
            "title": raw_entry.get("title") or _format_display_url(url),
            "site_name": raw_entry.get("site_name") or _get_site_name(url),
            "display_url": raw_entry.get("display_url") or _format_display_url(url),
            "snippet": raw_entry.get("snippet") or "",
            "icon": None,
            "provisional": bool(raw_entry.get("provisional", False))
        }

        if current_url and url == current_url:
            current_entry = normalized
        else:
            entries.append(normalized)

    if current_url and not current_entry:
        current_entry = {
            "url": current_url,
            "title": _format_display_url(current_url),
            "site_name": _get_site_name(current_url),
            "display_url": _format_display_url(current_url),
            "snippet": "",
            "icon": None,
            "provisional": False
        }

    return {
        "current_page": current_entry,
        "sources": entries
    }


def is_responses_api_available(model: str) -> bool:
    """
    Check if a model supports the Responses API with web search.

    Args:
        model: Model identifier (can be actual model name or frontend ID)

    Returns:
        True if model supports Responses API, False otherwise
    """
    if not USE_RESPONSES_API:
        logger.info(f"Responses API integration DISABLED via USE_OPENAI_RESPONSES_API=false")
        return False

    # Standard model families that support Responses API
    supported_patterns = {
        "gpt-4o",
        "gpt-5",
        "gpt-5.1",
        "gpt-5.2",
        "o1-preview",
        "o1-mini",
        "o3",
        "o4",
    }

    model_lower = model.lower()
    for pattern in supported_patterns:
        if pattern in model_lower:
            logger.info(f"Model '{model}' matches supported pattern '{pattern}' for Responses API")
            return True

    logger.info(f"Model '{model}' does not support Responses API with web search - will use fallback")
    return False


__all__ = [
    'create_responses_api_search',
    'create_responses_api_search_async',
    'extract_citations_from_response',
    'format_sources_for_frontend',
    'is_responses_api_available',
    'prepare_search_prompt',
    '_is_raw_json_leak'
]
