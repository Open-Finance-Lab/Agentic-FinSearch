Memory and Context System
=========================

Agentic FinSearch tracks every conversation with a single, session-scoped
context system: the ``UnifiedContextManager`` in
``Main/backend/datascraper/unified_context_manager.py``.

How It Works
------------

- **Session-Based**: Each browser tab (and each API caller) gets its own
  isolated context. See `Session Isolation`_ below.
- **Full History**: The complete conversation history for the current session
  — user messages, assistant responses, and their metadata (model used,
  sources, tool calls) — is retained and replayed to the model on every turn.
- **Fetched Context**: Scraped page content and search results are stored
  alongside the conversation (each item capped at 10,000 characters) and
  injected into the model's context for the session.
- **Modes**: Each request runs in one of three context modes — ``normal``,
  ``thinking``, or ``research`` — chosen by the endpoint handling the request
  (or an explicit ``mode`` parameter).
- **Storage & Expiry**: Session state lives in the Django cache — a local
  file cache in development, Redis in production — and expires after **1
  hour** of inactivity.

Session Isolation
-----------------

- **Cookie-Rooted Keys**: Conversation keys are rooted in a per-browser ID
  stored inside the **signed session cookie** (``fingpt_sessionid``), so a
  caller can never read or poison another caller's history by guessing a
  session ID.
- **Tab Isolation**: The extension keeps a per-tab session ID (browser
  ``sessionStorage``) that is namespaced *under* the cookie root — each tab
  gets its own conversation, and it still cannot cross to another browser.
- **API Isolation**: OpenAI-compatible API requests with a ``user`` parameter
  get a per-user session; requests without one get a unique ephemeral
  session.
- **Manual Clearing**: Use the **Clear** button to reset the current session's
  conversation history while optionally preserving scraped web content.
