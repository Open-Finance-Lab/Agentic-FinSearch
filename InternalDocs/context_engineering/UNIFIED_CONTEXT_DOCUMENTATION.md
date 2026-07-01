# Unified Context Management System Documentation

## Overview

The Unified Context Management System provides session-based conversation tracking with Django cache backend for multi-worker support. It maintains conversation history, fetched context from multiple sources (web search, page scraping), and metadata in a structured JSON format.

**Key Architecture Principle**: Clean separation between prompt assembly (PromptBuilder) and session state (UnifiedContextManager).

## Core Design Principles

1. **Cache-Backed Sessions**: All context stored in Django's cache framework, enabling session sharing across gunicorn workers
2. **Multi-Worker Safe**: FileBasedCache (production: Redis) ensures sessions persist across process boundaries
3. **Explicit Context Tracking**: Every piece of information (user message, assistant response, web search result, page content) is explicitly stored with metadata
4. **JSON-First**: Internal representation is structured JSON, making it debuggable and exportable
5. **Separation of Concerns**: PromptBuilder assembles prompts from markdown files + context; UnifiedContextManager manages session state only

## Architecture

### Component Responsibilities

**`UnifiedContextManager`** (`datascraper/unified_context_manager.py`)
- Session state management via Django cache
- Conversation history tracking
- Fetched context storage (web_search, js_scraping)
- Session metadata (mode, token_count, message_count)
- **Does NOT** handle: time context, URL context, identity prompt (moved to PromptBuilder)

**`PromptBuilder`** (`mcp_client/prompt_builder.py`)
- Assembles final system prompt from markdown files
- Loads `prompts/core.md` (identity, rules, security)
- Matches site-specific skills (`prompts/sites/*.md`) based on domain
- Injects time context and URL context
- Assembly order: core → site skill OR default_site → USER CONTEXT → TIME CONTEXT → session override

**`ContextIntegration`** (`datascraper/context_integration.py`)
- Bridge layer between Django views and UnifiedContextManager
- Handles request parsing (session ID extraction, mode determination)

### Cache Backend Configuration

**Production Setup** (`django_config/settings.py`):
```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": os.getenv("CACHE_FILE_PATH", "/tmp/fingpt_cache"),
        "TIMEOUT": 3600,  # 1 hour TTL
        "OPTIONS": {"MAX_ENTRIES": 500},
    }
}
```

**Multi-Worker Deployment**:
- Gunicorn runs with `--workers 2+`, each worker is a separate process
- FileBasedCache uses shared filesystem for session persistence
- Docker volume `session_cache:/tmp/fingpt_cache` ensures persistence across container restarts
- For production scale: migrate to Redis by changing `BACKEND` to `RedisCache` and `LOCATION` to `redis://redis:6379/0`

### JSON Context Structure

```json
{
  "system_prompt": "",  // Session-level override only (identity in core.md)

  "metadata": {
    "session_id": "session_1770154099669_4sju1cyqu",
    "timestamp": "2026-02-03T12:00:00+00:00",
    "mode": "normal|thinking|research",
    "current_url": "https://finance.yahoo.com/quote/AAPL",
    "user_timezone": "America/New_York",
    "user_time": "2026-02-03T07:00:00",
    "token_count": 1500,
    "message_count": 5
  },

  "fetched_context": {
    "web_search": [
      {
        "source_type": "web_search",
        "content": "Search result content...",
        "url": "https://source.url",
        "timestamp": "2026-02-03T12:05:00+00:00",
        "extracted_data": {
          "title": "Page Title",
          "site_name": "Source Site"
        }
      }
    ],
    "js_scraping": [
      {
        "source_type": "js_scraping",
        "content": "Content scraped from the user's current page...",
        "url": "https://current.page",
        "timestamp": "2026-02-03T12:10:00+00:00"
      }
    ]
  },

  "conversation_history": [
    {
      "role": "user",
      "content": "User's question",
      "timestamp": "2026-02-03T12:01:00+00:00"
    },
    {
      "role": "assistant",
      "content": "Assistant's response",
      "timestamp": "2026-02-03T12:01:30+00:00",
      "metadata": {
        "model": "gpt-4o-mini",
        "sources_used": [...],
        "tools_used": ["web_search"],
        "response_time_ms": 1250
      }
    }
  ]
}
```

## Prompt Assembly Flow

### Complete Prompt Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                     LLM API Call                            │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │
┌─────────────────────────────┴───────────────────────────────┐
│                  Final Messages List                        │
│  [System Prompt, Fetched Content, Conversation History]     │
└─────────────────────────────┬───────────────────────────────┘
                              │
        ┌─────────────────────┴──────────────────────┐
        │                                            │
┌───────▼──────────┐                     ┌───────────▼──────────┐
│  PromptBuilder   │                     │ UnifiedContextManager│
│  .build()        │                     │ .get_formatted_      │
│                  │                     │  messages_for_api()  │
├──────────────────┤                     ├──────────────────────┤
│ • core.md        │                     │ • Fetched content    │
│ • Site skill OR  │                     │   (web_search,       │
│   default_site   │                     │    js_scraping)      │
│ • USER CONTEXT   │                     │ • Conversation       │
│   (URL, domain)  │                     │   history            │
│ • TIME CONTEXT   │                     │ • Session prompt     │
│ • Session        │                     │   override (if any)  │
│   override       │                     │                      │
└──────────────────┘                     └──────────────────────┘
```

### Agent Path (MCP Tools Enabled)
```python
# In mcp_client/agent.py
prompt_builder = PromptBuilder()
system_prompt = prompt_builder.build(
    current_url=current_url,
    system_prompt=ucm.get_full_context(session_id)["system_prompt"],
    user_timezone=timezone,
    user_time=user_time
)

messages = ucm.get_formatted_messages_for_api(session_id)
# Messages already prefixed: [SYSTEM MESSAGE], [USER MESSAGE], [ASSISTANT MESSAGE]
```

### Research Path (OpenAI Search)
```python
# In datascraper/openai_search.py
# PromptBuilder NOT used here
# Time/URL context injected directly in create_responses_api_search_async()
messages = context_mgr.get_formatted_messages_for_api(session_id)
# + additional time/URL context formatting
```

## Usage Examples

### 1. Standard Chat Flow (Thinking Mode)

```python
from datascraper.unified_context_manager import get_context_manager, ContextMode

context_mgr = get_context_manager()

# 1. Update metadata
context_mgr.update_metadata(
    session_id=session_id,
    mode=ContextMode.THINKING,
    current_url=current_url,
    user_timezone="America/New_York",
    user_time="2026-02-03T12:00:00Z"
)

# 2. Add user message
context_mgr.add_user_message(session_id, question)

# 3. Get formatted messages (includes fetched context + history)
messages = context_mgr.get_formatted_messages_for_api(session_id)

# 4. Build full prompt (agent path)
from mcp_client.prompt_builder import PromptBuilder
prompt_builder = PromptBuilder()
system_prompt = prompt_builder.build(
    current_url=current_url,
    system_prompt=context_mgr.get_full_context(session_id)["system_prompt"],
    user_timezone="America/New_York",
    user_time="2026-02-03T12:00:00Z"
)

# 5. Call LLM
response = llm_client.generate(system_prompt, messages)

# 6. Store assistant response
context_mgr.add_assistant_message(
    session_id=session_id,
    content=response,
    model="gpt-4o-mini",
    sources_used=[{"title": "...", "url": "..."}],
    tools_used=["web_search"],
    response_time_ms=850
)
```

### 2. Adding Fetched Context

```python
# Web search results
context_mgr.add_fetched_context(
    session_id=session_id,
    source_type="web_search",
    content="Search result text...",
    url="https://example.com",
    extracted_data={"title": "Page Title", "site_name": "Example"}
)

# Page scraping (from browser extension)
context_mgr.add_fetched_context(
    session_id=session_id,
    source_type="js_scraping",
    content="Main content of current page...",
    url=current_url
)
```

### 3. Session Management

```python
# Get session metadata
metadata = context_mgr.get_session_metadata(session_id)
print(metadata.mode)  # ContextMode.RESEARCH
print(metadata.token_count)  # 5000

# Set session-level system prompt override
context_mgr.set_system_prompt(
    session_id,
    "Additional instructions for this session..."
)

# Get session stats
stats = context_mgr.get_session_stats(session_id)
# {
#   "mode": "research",
#   "message_count": 10,
#   "token_count": 5000,
#   "fetched_context_counts": {"web_search": 3, "js_scraping": 1},
#   "total_fetched_items": 4
# }

# Clear fetched context (preserve conversation)
context_mgr.clear_fetched_context(session_id, source_type="js_scraping")

# Clear conversation history (preserve fetched context)
context_mgr.clear_conversation_history(session_id)

# Delete entire session
context_mgr.clear_session(session_id)
```

## API Endpoints

| Endpoint | Method | Description | Context Operations |
|----------|--------|-------------|-------------------|
| `/get_chat_response/` | GET | Standard chat (Thinking Mode) | Updates metadata, adds user message, adds assistant response |
| `/get_chat_response_stream/` | GET | Streaming chat | Same as above, streaming response |
| `/get_adv_response/` | GET | Research Mode with web search | Performs search, adds to `fetched_context["web_search"]` |
| `/get_adv_response_stream/` | GET | Streaming research | Same as above, streaming |
| `/get_agent_response/` | POST | MCP agent-enabled chat | Uses PromptBuilder for prompt assembly |
| `/input_webtext/` | POST | Ingest scraped page content | Adds to `fetched_context["js_scraping"]` |
| `/clear_messages/` | POST | Clear conversation history | Calls `clear_conversation_history()` |
| `/api/get_memory_stats/` | GET | Get session statistics | Returns token_count, message_count, fetched_context_counts |
| `/api/auto_scrape/` | POST | Auto-scrape current page | Scrapes page, adds to js_scraping |

## Implementation Details

### Cache-Backed Session Pattern

**Every mutating operation follows**: `_load_session()` → modify → `_save_session()`

```python
def add_user_message(self, session_id: str, content: str, timestamp: Optional[str] = None) -> None:
    # 1. Load from cache (or create new)
    session = self._load_session(session_id)

    # 2. Modify
    message = ConversationMessage(
        role="user",
        content=content,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat()
    )
    session["conversation_history"].append(message)
    session["metadata"].message_count += 1
    session["metadata"].token_count += self._estimate_tokens(content)

    # 3. Save back to cache
    self._save_session(session_id, session)
```

### TTL and Eviction

- **TTL**: 3600 seconds (1 hour) per session
- **Touch on Access**: Every `_load_session()` resets TTL via `cache.set(key, session, self.session_ttl)`
- **Max Entries**: Configured in `settings.CACHES["default"]["OPTIONS"]["MAX_ENTRIES"]` (500)
- **Automatic Cleanup**: Django's cache backend handles eviction based on TTL and max entries

### Token Estimation

Rough estimation: `len(text) // 4` (~4 chars per token). Used for basic tracking in `metadata.token_count`.

### Data Classes

```python
@dataclass
class ContextMetadata:
    session_id: str
    timestamp: str
    mode: ContextMode  # Enum: RESEARCH, THINKING, NORMAL
    current_url: Optional[str] = None
    user_timezone: Optional[str] = None
    user_time: Optional[str] = None
    token_count: int = 0
    message_count: int = 0

@dataclass
class ConversationMessage:
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: str
    metadata: Optional[MessageMetadata] = None

@dataclass
class FetchedContextItem:
    source_type: Literal["web_search", "js_scraping"]
    content: str
    url: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extracted_data: Optional[Dict[str, Any]] = None
```

## Debugging

### LLM Debug Logger

Enable full context logging with environment variable:

```bash
LLM_DEBUG_LOG=true
LLM_DEBUG_VERBOSE=true  # Optional: include extra metadata
```

**Logs** (`api/utils/llm_debug_logger.py`):
- Dumps full context sent to LLM before each API call
- Includes: call site, model, provider, messages, stream flag, extra metadata
- Output: Docker terminal (stderr)

**Call Sites**:
1. `datascraper.py:_create_response_sync` / `_create_response_stream`
2. `datascraper.py:_create_agent_response_async` / `create_agent_response_stream`
3. `openai_search.py:create_responses_api_search_async`

## Production Deployment

### Docker Setup

**Dockerfile**: Creates `/tmp/fingpt_cache` directory
```dockerfile
RUN mkdir -p /tmp/fingpt_cache
```

**entrypoint.sh**: Ensures cache directory exists
```bash
mkdir -p "${CACHE_FILE_PATH:-/tmp/fingpt_cache}"
```

**docker-compose.yml**: Named volume for persistence
```yaml
volumes:
  - session_cache:/tmp/fingpt_cache

volumes:
  session_cache:
    driver: local
```

### Redis Migration (Later)

**Step 1**: Add Redis service to `docker-compose.yml`
```yaml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis_data:/data
    networks:
      - fingpt_network

volumes:
  redis_data:
    driver: local
```

**Step 2**: Update `.env.production`
```bash
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
CACHE_LOCATION=redis://redis:6379/0
```

**Step 3**: Update `settings.py` (already configured to read from env)
```python
CACHES = {
    "default": {
        "BACKEND": os.getenv(
            "CACHE_BACKEND",
            "django.core.cache.backends.filebased.FileBasedCache"
        ),
        "LOCATION": os.getenv("CACHE_LOCATION", "/tmp/fingpt_cache"),
        "TIMEOUT": 3600,
        "OPTIONS": {"MAX_ENTRIES": 500},
    }
}
```

## Future Enhancements

1. **Context Compression**: Implement summarization when `token_count` exceeds model limits (current: no compression)
2. **Analytics Dashboard**: Visualize conversation patterns, token usage, source utilization
3. **Session Persistence**: Optional PostgreSQL backend for long-term conversation archival
