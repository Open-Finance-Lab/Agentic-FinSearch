"""
Context Integration Module - Clean Version
Bridges the Unified Context Manager with API views
NO legacy support, NO backward compatibility - pure unified context
Author: Linus (pragmatic integration approach)
"""

import logging
from typing import Dict, List, Optional, Any
from django.http import HttpRequest

from .unified_context_manager import (
    UnifiedContextManager,
    ContextMode,
    get_context_manager
)

logger = logging.getLogger(__name__)


class ContextIntegration:
    """
    Integration layer between Unified Context Manager and API views.
    Clean implementation with no legacy support.
    """

    def __init__(self):
        self.context_manager = get_context_manager()

    def _get_session_id(self, request: HttpRequest) -> str:
        """Resolve the conversation key, bound to the signed session cookie.

        Delegates to the single shared derivation so the two request paths
        (api.views and ContextIntegration) can never key the same conversation
        differently. See datascraper.session_key.derive_conversation_key.
        """
        from .session_key import derive_conversation_key
        return derive_conversation_key(request)

    def _determine_mode(self, request: HttpRequest, endpoint: str) -> ContextMode:
        """Determine the context mode based on request and endpoint"""
        mode_param = request.GET.get('mode') or request.POST.get('mode')
        if mode_param:
            try:
                return ContextMode(mode_param)
            except ValueError:
                pass

        if 'adv' in endpoint or 'advanced' in endpoint:
            return ContextMode.RESEARCH
        elif 'agent' in endpoint:
            return ContextMode.THINKING
        else:
            return ContextMode.NORMAL

    def prepare_context_for_request(
        self,
        request: HttpRequest,
        question: str,
        current_url: Optional[str] = None,
        endpoint: str = ""
    ) -> tuple[List[Dict[str, str]], str]:
        """
        Prepare context for LLM API call.
        Returns (messages_list, session_id)
        """
        session_id = self._get_session_id(request)
        mode = self._determine_mode(request, endpoint)

        self.context_manager.update_metadata(
            session_id=session_id,
            mode=mode,
            current_url=current_url or request.GET.get('current_url'),
            user_timezone=request.GET.get('user_timezone'),
            user_time=request.GET.get('user_time')
        )

        self.context_manager.add_user_message(session_id, question)

        messages = self.context_manager.get_formatted_messages_for_api(session_id)

        return messages, session_id

    def add_response_to_context(
        self,
        session_id: str,
        response: str,
        model: Optional[str] = None,
        sources_used: Optional[List[Dict[str, str]]] = None,
        tools_used: Optional[List[str]] = None,
        response_time_ms: Optional[int] = None
    ) -> None:
        """Add assistant response to context"""
        self.context_manager.add_assistant_message(
            session_id=session_id,
            content=response,
            model=model,
            sources_used=sources_used,
            tools_used=tools_used,
            response_time_ms=response_time_ms
        )

    def add_web_content(
        self,
        request: HttpRequest,
        text_content: str,
        current_url: str,
        source_type: str = "js_scraping",
        session_id: Optional[str] = None
    ) -> str:
        """
        Add web content to context.
        Returns session_id
        """
        if not session_id:
            session_id = self._get_session_id(request)

        MAX_CONTENT_LENGTH = 10000
        if len(text_content) > MAX_CONTENT_LENGTH:
            text_content = text_content[:MAX_CONTENT_LENGTH] + "... (truncated)"

        self.context_manager.add_fetched_context(
            session_id=session_id,
            source_type=source_type,
            content=text_content,
            url=current_url
        )

        return session_id

    def add_search_results(
        self,
        session_id: str,
        search_results: List[Dict[str, Any]]
    ) -> None:
        """Add web search results to context"""
        for result in search_results:
            content = f"Title: {result.get('title', 'N/A')}\n"
            content += f"Snippet: {result.get('snippet', 'N/A')}\n"
            if result.get('body'):
                content += f"Content: {result['body'][:500]}..."

            self.context_manager.add_fetched_context(
                session_id=session_id,
                source_type="web_search",
                content=content,
                url=result.get('url'),
                extracted_data={
                    'title': result.get('title'),
                    'site_name': result.get('site_name'),
                    'published_date': result.get('published_date')
                }
            )

    def clear_messages(
        self,
        request: HttpRequest,
        preserve_web_content: bool = False
    ) -> str:
        """
        Clear conversation history, optionally preserving web content.
        Returns session_id
        """
        session_id = self._get_session_id(request)

        if preserve_web_content:
            self.context_manager.clear_conversation_history(session_id)
        else:
            self.context_manager.clear_session(session_id)

        return session_id

    def get_scraped_urls(self, session_id: str) -> List[str]:
        """Get list of URLs that have already been scraped"""
        return self.context_manager.get_scraped_urls(session_id)


_integration = None

def get_context_integration() -> ContextIntegration:
    """Get or create the singleton integration instance"""
    global _integration
    if _integration is None:
        _integration = ContextIntegration()
    return _integration
