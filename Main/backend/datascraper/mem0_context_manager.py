"""
Mem0 Context Manager for Agentic FinSearch
Implements production-ready memory layer using Mem0 for conversation context.
Now with smart context compression for fetched context and conversation history.
"""

import logging
import os
from typing import List, Dict, Optional, Literal, Any
from datetime import datetime, timezone
UTC = timezone.utc
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from collections import defaultdict

try:
    from mem0 import MemoryClient
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    logging.warning("Mem0 not installed. Install with: pip install mem0ai")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logging.warning("OpenAI not installed. Install with: pip install openai")


class Mem0ContextManager:
    """
    Manages conversation context using Mem0's production memory layer.
    Automatically extracts facts, preferences, and relationships from conversations.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        max_recent_messages: int = 10,
    ):
        """
        Initialize Mem0 Context Manager with smart compression support.

        Args:
            api_key: Mem0 API key (defaults to MEM0_API_KEY env var)
            openai_api_key: OpenAI API key for smart compression (defaults to OPENAI_API_KEY env var)
            max_recent_messages: Number of recent messages to keep verbatim
        """
        if not MEM0_AVAILABLE:
            raise ImportError(
                "Mem0 is not installed. Install with: pip install mem0ai\n"
                "Visit https://mem0.ai to get an API key."
            )

        self.api_key = api_key or os.getenv("MEM0_API_KEY")
        if not self.api_key:
            raise ValueError(
                "MEM0_API_KEY not found. Set it in environment variables or pass it to the constructor.\n"
                "Get your API key at: https://app.mem0.ai/dashboard/api-keys"
            )

        self.max_recent_messages = max_recent_messages
        self.max_session_tokens = max(
            int(os.getenv("MEM0_CONTEXT_TOKEN_LIMIT", "100000")),
            2000,
        )
        target_ratio = float(os.getenv("MEM0_COMPRESSION_TARGET_RATIO", "0.7"))
        self.compression_target_ratio = min(max(target_ratio, 0.4), 0.9)
        self.max_compression_chars = max(int(os.getenv("MEM0_COMPRESSION_MAX_CHARS", "4000")), 500)

        try:
            self.client = MemoryClient(api_key=self.api_key)
        except Exception as e:
            logging.error(f"Failed to initialize Mem0 client: {e}")
            raise

        self.llm_client = None
        if OPENAI_AVAILABLE:
            openai_key = openai_api_key or os.getenv("OPENAI_API_KEY")
            if openai_key:
                try:
                    self.llm_client = OpenAI(api_key=openai_key)
                    logging.info("OpenAI client initialized for smart compression")
                except Exception as e:
                    logging.warning(f"Failed to initialize OpenAI client: {e}")
            else:
                logging.warning("OPENAI_API_KEY not found. Smart compression will use basic fallback.")

        self.sessions = defaultdict(self._session_factory)
        self._request_count = 0
        self._cleanup_interval = 10
        self._session_ttl_seconds = 3600  # 1 hour

        self.base_system_prompt = (
            "You are a helpful financial assistant. Always answer questions to the best of your ability. "
            "You are situated inside an agent. The user may ask questions directly related to an active webpage "
            "(which you will have context for), or the user may ask questions that require extensive research."
        )

    def _session_factory(self) -> dict:
        """Create new session storage with fetched context support."""
        return {
            "recent_messages": [],
            "fetched_context": {
                "web_search": [],
                "js_scraping": []
            },
            "content_hashes": set(),
            "message_count": 0,
            "token_count": 0,
            "current_webpage": None,
            "user_timezone": None,
            "user_time": None,
            "last_used": datetime.now(UTC),
            "mem0_operations": 0,
            "compressed_chunk_count": 0,
            "has_compressed_chunks": False,
        }

    def cleanup_expired_sessions(self) -> int:
        """Remove sessions that haven't been used within the TTL. Returns count removed."""
        now = datetime.now(UTC)
        expired = [
            sid for sid, data in self.sessions.items()
            if (now - data.get("last_used", now)).total_seconds() > self._session_ttl_seconds
        ]
        for sid in expired:
            del self.sessions[sid]
        if expired:
            logging.info(f"[Mem0] Cleaned up {len(expired)} expired sessions, {len(self.sessions)} remaining")
        return len(expired)

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """
        Add a message to session history and Mem0 memory.

        Args:
            session_id: Unique session identifier
            role: Message role (user/assistant/system)
            content: Message content
        """
        self._request_count += 1
        if self._request_count % self._cleanup_interval == 0:
            self.cleanup_expired_sessions()

        session = self.sessions[session_id]
        session["last_used"] = datetime.now(UTC)

        if role == "user" and "[Web Content from" in content:
            import re
            url_match = re.search(r'\[Web Content from ([^\]]+)\]:', content)
            if url_match:
                current_url = url_match.group(1)
                self.update_current_webpage(session_id, current_url)

        timestamp = datetime.now(UTC)
        formatted_content = self._format_message_content(role, content)
        token_estimate = self.count_tokens(content)

        message = {
            "role": role,
            "content": content,
            "formatted": formatted_content,
            "timestamp": timestamp,
            "token_estimate": token_estimate,
        }

        session["recent_messages"].append(message)
        session["message_count"] += 1
        session["token_count"] += token_estimate

        self._check_context_limits(session_id)

    def add_fetched_context(
        self,
        session_id: str,
        source_type: Literal["web_search", "js_scraping"],
        content: str,
        url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Add fetched context from various sources.

        Args:
            session_id: Unique session identifier
            source_type: Type of source (web_search, js_scraping)
            content: Content from the source
            url: Optional URL associated with the content
            metadata: Optional additional metadata
        """
        session = self.sessions[session_id]
        session["last_used"] = datetime.now(UTC)

        content_hash = hash(content)
        if content_hash in session["content_hashes"]:
            logging.info(f"[Mem0] Skipping duplicate context for session {session_id} (URL: {url})")
            return
        
        session["content_hashes"].add(content_hash)

        timestamp = datetime.now(UTC)
        token_estimate = self.count_tokens(content)

        context_item = {
            "source_type": source_type,
            "content": content,
            "url": url,
            "timestamp": timestamp,
            "token_estimate": token_estimate,
            "metadata": metadata or {}
        }

        session["fetched_context"][source_type].append(context_item)
        session["token_count"] += token_estimate

        logging.debug(f"[Mem0] Added {source_type} context to session {session_id}: {url}")

        self._check_context_limits(session_id)

    def get_context(self, session_id: str, query: Optional[str] = None) -> List[Dict]:
        """
        Get conversation context for a session.

        Args:
            session_id: Session identifier
            query: Optional query for retrieving relevant memories

        Returns:
            List of messages for the session (system prompt + relevant memories + recent messages)
        """
        session = self.sessions[session_id]
        session["last_used"] = datetime.now(UTC)

        system_prompt = self.base_system_prompt

        if session.get("user_timezone") or session.get("user_time"):
            time_info_parts = []
            user_timezone = session.get("user_timezone")
            user_time_str = session.get("user_time")

            if user_time_str:
                try:
                    utc_time = datetime.fromisoformat(user_time_str.replace('Z', '+00:00'))
                except ValueError as exc:
                    logging.warning(f"Error parsing user time '{user_time_str}': {exc}")
                    utc_time = None

                if utc_time and user_timezone:
                    try:
                        local_time = utc_time.astimezone(ZoneInfo(user_timezone))
                        time_info_parts.append(f"User's timezone: {user_timezone}")
                        time_info_parts.append(
                            f"Current local time for user: {local_time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                        )
                    except ZoneInfoNotFoundError:
                        logging.warning(f"Unknown timezone '{user_timezone}', using UTC time reference")
                        time_info_parts.append(f"User's timezone: {user_timezone} (unrecognized)")
                        time_info_parts.append(
                            f"User provided time (UTC): {utc_time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                        )
                elif utc_time:
                    time_info_parts.append(
                        f"User provided time (UTC): {utc_time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
                    )
            elif user_timezone:
                time_info_parts.append(f"User's timezone: {user_timezone}")

            if time_info_parts:
                system_prompt += f"\n\n[TIME CONTEXT]: {' | '.join(time_info_parts)}"

        if session.get("current_webpage"):
            system_prompt += (
                f"\n\n[CURRENT CONTEXT]: You are currently viewing the webpage: {session['current_webpage']}. "
                f"When users ask 'which page am I on' or similar questions about the current page, "
                f"always confidently tell them they are on: {session['current_webpage']}"
            )

        context = [{
            "role": "user",
            "content": system_prompt
        }]

        fetched = session.get("fetched_context", {})

        if fetched.get("web_search"):
            search_content = "\n\n[WEB SEARCH RESULTS]:"
            for item in fetched["web_search"]:
                search_content += f"\n- From {item.get('url', 'unknown')}: {item['content'][:500]}"
            context.append({
                "role": "user",
                "content": search_content
            })

        if fetched.get("js_scraping"):
            js_content = "\n\n[WEB PAGE CONTENT]:"
            for item in fetched["js_scraping"]:
                js_content += f"\n- From {item.get('url', 'page')}: {item['content'][:500]}"
            context.append({
                "role": "user",
                "content": js_content
            })

        if session.get("has_compressed_chunks"):
            for chunk in self._get_compressed_chunks(session_id, query=query):
                context.append(chunk)

        for msg in self._get_recent_conversation_entries(session_id):
            context.append({
                "role": "user",
                "content": msg["formatted"],
            })

        return context

    def update_current_webpage(self, session_id: str, url: str) -> None:
        """
        Update the current webpage URL for a session.

        Args:
            session_id: Session identifier
            url: Current webpage URL
        """
        session = self.sessions[session_id]
        session["current_webpage"] = url
        session["last_used"] = datetime.now(UTC)
        logging.debug(f"[Mem0] Updated current webpage for session {session_id}: {url}")

    def update_user_time_info(self, session_id: str, timezone: str = None, current_time: str = None) -> None:
        """
        Update the user's timezone and current time for a session.

        Args:
            session_id: Session identifier
            timezone: User's IANA timezone (e.g., "America/New_York")
            current_time: User's current time in ISO format
        """
        session = self.sessions[session_id]
        if timezone:
            session["user_timezone"] = timezone
        if current_time:
            session["user_time"] = current_time
        session["last_used"] = datetime.now(UTC)
        logging.debug(f"[Mem0] Updated time info for session {session_id}: {timezone}, {current_time}")

    def clear_session(self, session_id: str) -> None:
        """
        Clear all messages, fetched context, and memories for a session.

        Args:
            session_id: Session identifier
        """
        session = self.sessions.get(session_id)
        if session:
            session["recent_messages"].clear()
            for source_type in session.get("fetched_context", {}):
                session["fetched_context"][source_type] = []
            session["message_count"] = 0
            session["token_count"] = 0
            session["compressed_chunk_count"] = 0
            session["has_compressed_chunks"] = False

        try:
            self.client.delete_all(user_id=session_id)
            if session:
                session["mem0_operations"] += 1
            logging.info(f"[Mem0] Cleared all memories for session {session_id}")
        except Exception as e:
            logging.error(f"[Mem0] Failed to clear memories: {e}")

        if session_id in self.sessions:
            del self.sessions[session_id]

    def clear_conversation_only(self, session_id: str) -> None:
        """
        Clear recent conversation messages but preserve long-term memories and web content.

        Args:
            session_id: Session identifier
        """
        session = self.sessions.get(session_id)
        if not session:
            return

        preserved_messages = []
        for msg in session["recent_messages"]:
            if "[Web Content from" in msg.get("content", ""):
                preserved_messages.append(msg)

        session["recent_messages"] = preserved_messages
        session["message_count"] = len(preserved_messages)
        session["token_count"] = sum(
            m.get("token_estimate", self.count_tokens(m.get("content", ""))) for m in preserved_messages
        )
        session["compressed_chunk_count"] = 0
        session["has_compressed_chunks"] = False
        session["last_used"] = datetime.now(UTC)

        try:
            self.client.delete_all(user_id=session_id)
            session["mem0_operations"] += 1
        except Exception as e:
            logging.error(f"[Mem0] Failed to clear compressed chunks: {e}")

        logging.info(f"[Mem0] Cleared conversation for session {session_id}, preserved {len(preserved_messages)} web content messages")

    def get_session_stats(self, session_id: str) -> Dict:
        """
        Get statistics for a session.

        Args:
            session_id: Session identifier

        Returns:
            Dictionary with session statistics
        """
        session = self.sessions.get(session_id)
        if not session:
            return {}

        last_used = session.get("last_used")
        return {
            "recent_message_count": len(session["recent_messages"]),
            "total_message_count": session.get("message_count", 0),
            "compressed_chunk_count": session.get("compressed_chunk_count", 0),
            "memory_count": session.get("compressed_chunk_count", 0),
            "token_estimate": session.get("token_count", 0),
            "mem0_operations": session.get("mem0_operations", 0),
            "current_webpage": session.get("current_webpage"),
            "last_used": last_used.isoformat() if last_used else None,
            "active_sessions": len(self.sessions),
            "using_mem0": True,
        }

    def _format_message_content(self, role: str, content: str) -> str:
        if role == "assistant":
            return f"[ASSISTANT RESPONSE]: {content}"
        if role == "user":
            return f"[USER QUESTION]: {content}"
        return content

    def _check_context_limits(self, session_id: str) -> None:
        session = self.sessions[session_id]
        if session["token_count"] <= self.max_session_tokens:
            return
        self._compress_session_history(session_id)

    def _compress_session_history(self, session_id: str) -> None:
        """
        Smart compression of session history using LLM when approaching token limits.
        Compresses BOTH conversation history AND fetched context into Mem0 memory.
        """
        session = self.sessions[session_id]

        logging.info(f"[Mem0] Smart compression triggered for session {session_id}. Current tokens: {session['token_count']}/{self.max_session_tokens}")

        context_dump = []

        fetched = session.get("fetched_context", {})

        if fetched.get("web_search"):
            context_dump.append("=== WEB SEARCH RESULTS ===")
            for item in fetched["web_search"]:
                context_dump.append(f"URL: {item.get('url', 'N/A')}")
                context_dump.append(f"Content: {item['content']}")
                if item.get('metadata'):
                    context_dump.append(f"Metadata: {item['metadata']}")
                context_dump.append("---")

        if fetched.get("js_scraping"):
            context_dump.append("\n=== SCRAPED CONTENT ===")
            for item in fetched["js_scraping"]:
                context_dump.append(f"URL: {item.get('url', 'N/A')}")
                context_dump.append(f"Content: {item['content']}")
                context_dump.append("---")

        messages_to_compress = session["recent_messages"][:-2] if len(session["recent_messages"]) > 2 else session["recent_messages"]

        if messages_to_compress:
            context_dump.append("\n=== CONVERSATION HISTORY ===")
            for msg in messages_to_compress:
                role = "User" if msg["role"] == "user" else "Assistant"
                context_dump.append(f"{role}: {msg['content']}")
                context_dump.append("---")

        full_context = "\n".join(context_dump)

        if not full_context.strip():
            logging.info(f"[Mem0] No content to compress for session {session_id}")
            return

        if self.llm_client:
            compressed_summary = self._smart_compress_with_llm(full_context, session_id)
        else:
            compressed_summary = self._fallback_compress(full_context, session_id)

        if not compressed_summary:
            logging.error(f"[Mem0] Failed to generate compression summary for session {session_id}")
            return

        chunk_index = session.get("compressed_chunk_count", 0) + 1
        try:
            self._store_compressed_chunk(session_id, compressed_summary, chunk_index, datetime.now(UTC))
            session["compressed_chunk_count"] = chunk_index
            session["has_compressed_chunks"] = True

            for source_type in session["fetched_context"]:
                session["fetched_context"][source_type] = []

            if len(session["recent_messages"]) > 2:
                kept_messages = session["recent_messages"][-2:]
                session["recent_messages"] = kept_messages
                session["message_count"] = len(kept_messages)

                session["token_count"] = sum(
                    msg.get("token_estimate", self.count_tokens(msg.get("content", "")))
                    for msg in kept_messages
                )
            else:
                pass

            logging.info(f"[Mem0] Smart compression completed for session {session_id}. Chunk #{chunk_index} stored. Tokens reduced to {session['token_count']}")

        except Exception as e:
            logging.error(f"[Mem0] Failed to store compressed chunk: {e}")

    def _smart_compress_with_llm(self, context_dump: str, session_id: str) -> Optional[str]:
        """
        Use gpt-5-chat-latest to intelligently compress the context.
        """
        try:
            prompt = """You are an expert context compressor for a financial research assistant. I am providing you with the full state of a financial research session, including conversation history, web search results, and scraped content.

Your task is to compress this into a single, comprehensive 'Memory Update' that preserves:

1. All specific financial figures, dates, tickers, and entity names found in the search results
2. The user's specific questions, preferences, and research objectives
3. The logical flow and progression of the investigation
4. Key findings and insights discovered
5. Any URLs that provided valuable information
6. Important relationships between different pieces of information

Discard:
- Repetitive boilerplate text
- Failed search attempts with no valuable information
- Redundant information that appears multiple times
- Navigation/UI elements from scraped content

The output must be a dense, factual summary suitable for restoring the agent's understanding of the current research state. Structure it clearly with sections if needed.

Context to compress:
"""

            response = self.llm_client.chat.completions.create(
                model="gpt-5-chat-latest",
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": context_dump[:50000]}
                ],
                max_tokens=4000,
                temperature=0.3
            )

            compressed = response.choices[0].message.content
            return f"[COMPRESSED MEMORY - Session {session_id}]\n{compressed}"

        except Exception as e:
            logging.error(f"[Mem0] LLM compression failed: {e}")
            return None

    def _fallback_compress(self, context_dump: str, session_id: str) -> str:
        """
        Fallback compression when LLM is not available.
        """
        lines = context_dump.split('\n')
        summary_lines = [f"[COMPRESSED MEMORY - Session {session_id} - Fallback]"]

        for i, line in enumerate(lines):
            if line.startswith("===") or i < 10:
                summary_lines.append(line)
            elif len("\n".join(summary_lines)) < self.max_compression_chars:
                if any(keyword in line.lower() for keyword in ['http', 'www', '$', '%', 'stock', 'price']):
                    summary_lines.append(line)

        summary = "\n".join(summary_lines)
        if len(summary) > self.max_compression_chars:
            summary = summary[:self.max_compression_chars] + "... (truncated)"

        return summary

    def _summarize_messages_for_mem0(self, messages: List[Dict], chunk_index: int) -> str:
        summary_lines = [f"[COMPRESSED CHUNK #{chunk_index}] Earlier conversation context:"]
        for msg in messages:
            role_label = "Assistant" if msg.get("role") == "assistant" else "User"
            formatted = msg.get("formatted") or msg.get("content", "")
            summary_lines.append(f"{role_label}: {formatted}")
            if sum(len(line) for line in summary_lines) > self.max_compression_chars:
                summary_lines.append("... (truncated)")
                break

        summary = "\n".join(summary_lines)
        if len(summary) > self.max_compression_chars:
            summary = summary[: self.max_compression_chars] + "..."
        return summary

    def _store_compressed_chunk(self, session_id: str, chunk_text: str, chunk_index: int, timestamp: datetime) -> None:
        metadata = {
            "session_id": session_id,
            "memory_type": "compressed_chunk",
            "chunk_sequence": chunk_index,
            "timestamp": timestamp.isoformat(),
        }
        self.client.add(
            messages=[{"role": "user", "content": chunk_text}],
            user_id=session_id,
            metadata=metadata,
        )
        session = self.sessions[session_id]
        session["mem0_operations"] += 1

    def _get_recent_conversation_entries(self, session_id: str) -> List[Dict]:
        session = self.sessions[session_id]
        return list(session["recent_messages"])

    def _get_compressed_chunks(self, session_id: str, query: Optional[str] = None) -> List[Dict]:
        session = self.sessions[session_id]
        try:
            if query:
                search_result = self.client.search(query=query, user_id=session_id, limit=5)
                memories = search_result.get('results', []) if isinstance(search_result, dict) else search_result
            else:
                get_all_result = self.client.get_all(user_id=session_id)
                memories = get_all_result.get('results', []) if isinstance(get_all_result, dict) else get_all_result
            session["mem0_operations"] += 1
        except Exception as e:
            logging.error(f"[Mem0] Failed to load compressed chunks: {e}")
            return []

        chunks: List[Dict] = []
        for memory in memories or []:
            metadata = memory.get('metadata') or {}
            if metadata.get('memory_type') != 'compressed_chunk':
                continue
            chunk_text = memory.get('memory') or memory.get('text') or memory.get('content')
            if not chunk_text:
                continue
            sequence = metadata.get('chunk_sequence', 0)
            chunks.append({
                "role": "user",
                "content": chunk_text,
                "sequence": sequence,
            })

        chunks.sort(key=lambda item: item.get('sequence', 0))
        return chunks

    def count_tokens(self, text: str) -> int:
        """
        Estimate token count for text.
        Note: Mem0 handles token management internally, but this is kept for compatibility.

        Args:
            text: Text to count tokens for

        Returns:
            Estimated token count
        """
        return len(text) // 4
