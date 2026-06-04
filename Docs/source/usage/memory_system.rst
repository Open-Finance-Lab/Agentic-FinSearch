Memory and Context System
=========================

Agentic FinSearch employs a two-tier context management system to handle complex, long-running financial research sessions. The active mode is configured via the ``CONTEXT_MANAGER_MODE`` environment variable.

Unified Context Manager (Default)
----------------------------------

The default context system is the ``UnifiedContextManager`` in ``Main/backend/datascraper/unified_context_manager.py``. It provides fast, session-based context tracking using an in-memory JSON structure.

- **Session-Based**: Each browser tab or API session maintains its own isolated context.
- **Full History**: Maintains the complete conversation history for the current session.
- **JSON Structure**: Stores context as structured JSON for efficient retrieval and token counting.
- **No External Dependencies**: Runs entirely in-process with no external services required.

Mem0 Context Manager (Optional)
--------------------------------

For sessions that require long-term memory beyond the context window, the ``Mem0ContextManager`` in ``Main/backend/datascraper/mem0_context_manager.py`` provides LLM-based summarization and vectorized memory storage.

To enable, set ``CONTEXT_MANAGER_MODE=mem0`` in your ``.env`` file along with ``MEM0_API_KEY``.

Smart Compression
-----------------

When using the Mem0 context manager, conversations that exceed **100,000 tokens** (approximately 75,000 words) trigger **Smart Compression**:

1. **Fact Extraction**: The agent identifies critical pieces of information — financial figures, company names, specific research goals, and important URLs.
2. **Boilerplate Removal**: Redundant text, UI navigation artifacts, and failed search attempts are discarded.
3. **Memory Storage**: Extracted facts are stored in a vectorized memory database.
4. **Context Restoration**: Relevant memories are automatically re-injected into the context when they pertain to your current query.

Compression behavior is configurable via environment variables:

- ``MEM0_CONTEXT_TOKEN_LIMIT``: Token threshold before compression triggers (default: 100,000).
- ``MEM0_COMPRESSION_TARGET_RATIO``: Target compression ratio (default: 0.7).
- ``MEM0_COMPRESSION_MAX_CHARS``: Maximum characters per compression chunk (default: 4,000).

Session Isolation
-----------------

- **Tab Isolation**: Each browser tab maintains its own unique session.
- **API Isolation**: Each API request with a different ``user`` parameter gets a separate session (``api_user_<user>``). Requests without a ``user`` parameter get a unique ephemeral session.
- **Privacy**: Context is isolated per session and is not shared across different users or tabs unless explicitly configured.
- **Manual Clearing**: Use the **Clear** button to reset the current session's conversation history while optionally preserving scraped web content.
