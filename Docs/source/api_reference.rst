API Reference
=============

This document specifies the Agentic FinSearch REST API: the OpenAI-compatible ``/v1`` endpoints plus the extension, XBRL-validation, and news-signals endpoints. The ``/v1`` API is **synchronous** (no streaming); the extension chat endpoints also offer Server-Sent-Events streaming variants. Unless noted otherwise, request and response bodies are JSON.

.. contents:: Table of Contents
   :depth: 3
   :local:

---

Connection
----------

Base URL
~~~~~~~~

The API is served by a Django backend. In production it sits behind a Caddy
reverse proxy that terminates TLS on the standard HTTPS port (443) — port
8000 is bound to loopback only and is not reachable from the internet.

**Production** (Fedora droplet at ``134.122.1.153``, IPv4 only):

.. code-block:: text

   https://agenticfinsearch.org

**Local development:**

.. code-block:: text

   http://localhost:8000

All endpoint paths below are relative to this base URL.

Authentication
~~~~~~~~~~~~~~

The **OpenAI-compatible endpoints** (``/v1/models``, ``/v1/chat/completions``) use **Bearer token** authentication.

.. code-block:: text

   Authorization: Bearer <FINGPT_API_KEY>

- The API key is set via the ``FINGPT_API_KEY`` environment variable on the server.
- If ``FINGPT_API_KEY`` is **not set**, ``/v1/*`` authentication is disabled (development mode). In production the server sets ``REQUIRE_FINGPT_API_KEY=True``, which **fails closed**: a missing key makes ``/v1/*`` return ``503`` instead of silently accepting unauthenticated requests.
- When authentication is enabled, every ``/v1/*`` request must include the ``Authorization`` header.

.. note::
   The extension and utility endpoints documented below now require the same
   ``Authorization: Bearer <FINGPT_API_KEY>`` header as ``/v1/*`` whenever the
   server is key-configured (dev-open / prod-fail-closed, exactly as described
   above). Two endpoints stay exempt: ``/health/`` (the unauthenticated
   liveness probe) and ``/api/axioms/xbrl/<filename>/`` (fetched by a plain
   browser download that cannot attach a header — protected instead by rate
   limiting and an opaque, server-chosen filename). Per-client rate limiting
   and cookie-rooted session isolation remain in force on top of the bearer
   gate. Because a publicly distributed extension bundle is extractable, the
   shared key is a **coarse gate** against drive-by API abuse, not per-user
   authentication; per-user attribution is deferred to the identity/login
   system.

**Error responses (401):**

.. code-block:: json

   {
     "error": {
       "message": "Missing Authorization header. Use: Authorization: Bearer <api_key>",
       "type": "authentication_error"
     }
   }

.. code-block:: json

   {
     "error": {
       "message": "Invalid API key",
       "type": "authentication_error"
     }
   }

Rate Limiting
~~~~~~~~~~~~~

Default: **600 requests per hour** per client (configurable via ``API_RATE_LIMIT`` env var).

Format: ``<count>/<period>`` where period is ``s`` (second), ``m`` (minute), ``h`` (hour), or ``d`` (day).

CORS
~~~~

CORS restrictions only apply to **browser-based** requests. HTTP clients (``curl``, ``requests``, ``httpx``, Postman) are unaffected.

---

Endpoints
---------

Health Check
~~~~~~~~~~~~

Check if the backend is running. Does **not** require authentication.

.. list-table::
   :widths: 15 85

   * - **Method**
     - ``GET``
   * - **Path**
     - ``/health/``
   * - **Auth**
     - Not required

**Response (200):**

.. code-block:: json

   {
     "status": "healthy",
     "service": "fingpt-backend",
     "timestamp": "2026-02-22T12:00:00.000000",
     "version": "0.16.0",
     "using_unified_context": true
   }

**Example:**

.. code-block:: bash

   curl https://agenticfinsearch.org/health/

---

List Models
~~~~~~~~~~~

Returns all available models in OpenAI-compatible format.

.. list-table::
   :widths: 15 85

   * - **Method**
     - ``GET``
   * - **Path**
     - ``/v1/models``
   * - **Auth**
     - Required (when ``FINGPT_API_KEY`` is set)

**Response (200):**

.. code-block:: json

   {
     "object": "list",
     "data": [
       {
         "id": "FinGPT",
         "object": "model",
         "created": 1740000000,
         "owned_by": "google",
         "permission": [],
         "root": "FinGPT",
         "parent": null
       },
       {
         "id": "FinGPT-Light",
         "object": "model",
         "created": 1740000000,
         "owned_by": "openai",
         "permission": [],
         "root": "FinGPT-Light",
         "parent": null
       },
       {
         "id": "Buffet-Agent",
         "object": "model",
         "created": 1740000000,
         "owned_by": "buffet",
         "permission": [],
         "root": "Buffet-Agent",
         "parent": null
       }
     ]
   }

**Response fields:**

.. list-table::
   :widths: 20 15 65
   :header-rows: 1

   * - Field
     - Type
     - Description
   * - ``object``
     - string
     - Always ``"list"``.
   * - ``data``
     - array
     - Array of model objects.
   * - ``data[].id``
     - string
     - Model identifier. Use this value in the ``model`` field of chat completion requests.
   * - ``data[].owned_by``
     - string
     - Provider name: ``"google"``, ``"openai"``, or ``"buffet"``.

**Example:**

.. code-block:: bash

   curl -H "Authorization: Bearer $API_KEY" \
        https://agenticfinsearch.org/v1/models

**Error responses:**

- ``401``: Authentication error (see `Authentication`_).
- ``405``: Wrong HTTP method (must be ``GET``).

---

Chat Completions
~~~~~~~~~~~~~~~~

Generate a chat completion. This is the primary endpoint for interacting with the agent.

.. list-table::
   :widths: 15 85

   * - **Method**
     - ``POST``
   * - **Path**
     - ``/v1/chat/completions``
   * - **Auth**
     - Required (when ``FINGPT_API_KEY`` is set)
   * - **Content-Type**
     - ``application/json``

Request Body
^^^^^^^^^^^^

.. list-table::
   :widths: 20 10 10 60
   :header-rows: 1

   * - Field
     - Type
     - Required
     - Description
   * - ``messages``
     - array
     - Yes
     - Array of message objects (see `Message Format`_ below). Must contain at least one message. The last message should be the user's current question.
   * - ``mode``
     - string
     - Yes
     - Agent mode. One of: ``"thinking"``, ``"research"``, ``"normal"``. See `Modes`_ below.
   * - ``model``
     - string
     - No
     - Model ID from ``/v1/models``. Default: ``"FinGPT"``. Must be an exact match (case-sensitive).
   * - ``url``
     - string
     - No
     - A URL to scrape and inject as page context before generating the response. Used for site-specific analysis (e.g., analyzing a Yahoo Finance stock page).
   * - ``search_domains``
     - array
     - No
     - List of domain strings to scope research to (research mode only). Bare domains like ``"reuters.com"`` are auto-prefixed with ``https://``. Merged into ``preferred_links``.
   * - ``preferred_links``
     - array
     - No
     - List of full URLs to prioritize in research (research mode only).
   * - ``user_timezone``
     - string
     - No
     - IANA timezone string (e.g., ``"America/New_York"``). Helps the agent give time-aware responses.
   * - ``user_time``
     - string
     - No
     - ISO 8601 timestamp of the user's current time (e.g., ``"2026-02-22T10:30:00-05:00"``).
   * - ``user``
     - string
     - No
     - An opaque user identifier. When provided, the session ID is derived from it (``api_user_<user>``). When absent, each request gets a unique session.

Message Format
^^^^^^^^^^^^^^

Each element of the ``messages`` array is an object:

.. list-table::
   :widths: 15 10 75
   :header-rows: 1

   * - Field
     - Type
     - Description
   * - ``role``
     - string
     - One of ``"system"``, ``"user"``, ``"assistant"``.
   * - ``content``
     - string
     - The message text.

The API processes messages in order:

1. ``system`` messages set the system prompt (last one wins).
2. ``user`` and ``assistant`` messages populate conversation history.
3. The **last** message in the array is treated as the current prompt and must be a ``user`` message for the agent to generate a response.

Modes
^^^^^

.. list-table::
   :widths: 15 85
   :header-rows: 1

   * - Mode
     - Behavior
   * - ``thinking``
     - **Agentic mode.** The agent uses MCP tools (SEC-EDGAR, Yahoo Finance) to gather data before responding. Best for specific financial questions. ``sources`` in the response will list MCP tools used (e.g., ``get_stock_info``, ``sec_full_text_search``).
   * - ``research``
     - **Deep research mode.** The agent decomposes the question into sub-queries, performs parallel web searches, synthesizes a comprehensive answer. Best for broad research questions. ``sources`` in the response will list web URLs used. Supports ``search_domains`` and ``preferred_links`` to scope research.
   * - ``normal``
     - **Direct mode.** The agent responds using its training data and any injected page context (``url`` parameter) without performing web searches or using MCP tools.

Response Body
^^^^^^^^^^^^^

The response follows the **OpenAI chat completion format** with Agentic FinSearch extensions.

.. code-block:: json

   {
     "id": "chatcmpl-a1b2c3d4e5f6...",
     "object": "chat.completion",
     "created": 1740000000,
     "model": "FinGPT",
     "choices": [
       {
         "index": 0,
         "message": {
           "role": "assistant",
           "content": "The agent's response text..."
         },
         "finish_reason": "stop"
       }
     ],
     "usage": {
       "prompt_tokens": 150,
       "completion_tokens": 200,
       "total_tokens": 350
     },
     "sources": []
   }

**Response fields:**

.. list-table::
   :widths: 25 10 65
   :header-rows: 1

   * - Field
     - Type
     - Description
   * - ``id``
     - string
     - Unique completion ID, prefixed with ``chatcmpl-``.
   * - ``object``
     - string
     - Always ``"chat.completion"``.
   * - ``created``
     - integer
     - Unix timestamp of when the response was generated.
   * - ``model``
     - string
     - The model ID used.
   * - ``choices``
     - array
     - Always contains exactly **one** choice (index 0).
   * - ``choices[0].message.role``
     - string
     - Always ``"assistant"``.
   * - ``choices[0].message.content``
     - string
     - The generated response text (Markdown-formatted).
   * - ``choices[0].finish_reason``
     - string
     - Always ``"stop"``.
   * - ``usage.prompt_tokens``
     - integer
     - Approximate prompt token count from the context manager.
   * - ``usage.completion_tokens``
     - integer
     - Approximate completion tokens (``len(content) // 4``).
   * - ``usage.total_tokens``
     - integer
     - Sum of ``prompt_tokens`` and ``completion_tokens``.
   * - ``sources``
     - array
     - **Agentic FinSearch extension.** List of source objects. Structure varies by mode (see below).

Sources Format
^^^^^^^^^^^^^^

The ``sources`` array structure depends on the mode used.

**Thinking mode sources** (MCP tool calls):

.. code-block:: json

   [
     {
       "type": "tool",
       "tool_name": "get_stock_info",
       "symbol": "AAPL",
       "call_id": "call_abc123"
     }
   ]

**Research mode sources** (web search results):

.. code-block:: json

   [
     {
       "url": "https://reuters.com/markets/article-xyz",
       "title": "Reuters Article Title"
     }
   ]

**Normal mode**: ``sources`` is typically an empty array ``[]``.

Error Responses
^^^^^^^^^^^^^^^

All errors follow this format:

.. code-block:: json

   {
     "error": {
       "message": "Human-readable error description",
       "type": "error_type_string"
     }
   }

.. list-table::
   :widths: 10 25 65
   :header-rows: 1

   * - Code
     - Type
     - Cause
   * - 400
     - ``invalid_request_error``
     - Missing ``messages``, missing ``mode``, invalid ``mode`` value, or malformed JSON body.
   * - 401
     - ``authentication_error``
     - Missing/invalid ``Authorization`` header or API key.
   * - 404
     - ``invalid_request_error``
     - Model ID does not exist (use ``GET /v1/models`` to list valid IDs).
   * - 405
     - (plain)
     - Wrong HTTP method (e.g., ``GET`` on ``/v1/chat/completions``).
   * - 500
     - ``server_error``
     - Internal error. The ``message`` field will be generic (no stack traces are exposed). Check server logs.

---

Available Models
~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 20 15 30 35
   :header-rows: 1

   * - Model ID
     - Provider
     - Underlying Model
     - Description
   * - ``FinGPT``
     - google
     - ``gemini-3-flash-preview``
     - Default model. 1M token context. No streaming.
   * - ``FinGPT-Light``
     - openai
     - ``gpt-5.1-chat-latest``
     - Faster, lighter. 128k token context.
   * - ``Buffet-Agent``
     - buffet
     - Custom (Hugging Face endpoint)
     - Fine-tuned financial model.
   * - ``FinSearch-Trader``
     - google
     - ``gemini-3-flash-preview``
     - Professional trader persona. Direct path only — never uses MCP tools or web search in any mode (deterministic, backtest-safe).

Models support both ``thinking`` (MCP) and ``research`` (deep search) modes, except the *direct* models (``Buffet-Agent``, ``FinSearch-Trader``), which answer with a single model call in every mode — no tools are ever attached.

---

Extension & Utility Endpoints
-----------------------------

These endpoints back the Chrome extension and other first-party surfaces.
They require the ``Authorization: Bearer <FINGPT_API_KEY>`` header when the
server is key-configured (see the note under `Authentication`_) — except
``/health/`` and ``/api/axioms/xbrl/<filename>/``, which stay unauthenticated.
Each client is additionally rate-limited, and conversations are isolated via
the signed ``fingpt_sessionid`` cookie. Callers may pass an optional
``session_id`` (query string or JSON body) to select a sub-conversation
*under their own* cookie root — it can never address another browser's history.

.. list-table::
   :widths: 12 34 54
   :header-rows: 1

   * - Method
     - Path
     - Purpose
   * - GET/POST
     - ``/get_chat_response/``
     - Thinking-mode answer (synchronous)
   * - GET/POST
     - ``/get_chat_response_stream/``
     - Thinking-mode answer (SSE stream)
   * - GET/POST
     - ``/get_adv_response/``
     - Research-mode answer (synchronous)
   * - GET/POST
     - ``/get_adv_response_stream/``
     - Research-mode answer (SSE stream)
   * - POST
     - ``/input_webtext/``
     - Add scraped page text to the session context
   * - POST
     - ``/api/auto_scrape/``
     - Server-side scrape of the active page (SSRF-guarded)
   * - GET
     - ``/get_source_urls/``
     - Sources for a query
   * - POST
     - ``/clear_messages/``
     - Clear the session conversation
   * - GET
     - ``/api/get_preferred_urls/``
     - Read stored Preferred links
   * - POST
     - ``/api/sync_preferred_urls/``
     - Store Preferred links
   * - GET
     - ``/api/get_available_models/``
     - Model metadata for the Settings dropdown
   * - GET/POST
     - ``/log_question/``
     - Telemetry logging
   * - POST
     - ``/api/axioms/validate/``
     - Run XBRL validation over a session's recorded claims
   * - GET
     - ``/api/axioms/has_claims/``
     - Does this session have validatable claims?
   * - GET
     - ``/api/axioms/xbrl/<filename>/``
     - Serve a bundled XBRL filing (Sources popup) — *unauthenticated* browser download (see the note under `Authentication`_)
   * - GET
     - ``/api/signals/news/``
     - Latest news→sentiment signals artifact
   * - GET
     - ``/api/news/items/``
     - Raw news stories from the newest Heartbeat batch

All share the ``API_RATE_LIMIT`` budget (``429 {"error": "rate_limited"}``
when exceeded). The chat endpoints can also return ``503 {"error": "busy"}``
when the agent concurrency or daily budget cap is hit.

Chat (Thinking / Research)
~~~~~~~~~~~~~~~~~~~~~~~~~~

``/get_chat_response/`` (Thinking mode) and ``/get_adv_response/`` (Research
mode) accept the same core parameters — query string on GET, JSON body on
POST (the body wins when both are present):

.. list-table::
   :widths: 25 10 65
   :header-rows: 1

   * - Field
     - Required
     - Description
   * - ``question``
     - Yes
     - The user's prompt.
   * - ``models``
     - No
     - Comma-separated model IDs (the extension always sends the model
       chosen in Settings).
   * - ``current_url``
     - No
     - Active page URL, used for context and site-specific prompts.
   * - ``preferred_links``
     - No
     - Research mode only: JSON-encoded array of URLs to prioritize.
   * - ``session_id``
     - No
     - Sub-conversation selector (namespaced under the session cookie).
   * - ``user_timezone`` / ``user_time``
     - No
     - IANA timezone / ISO 8601 timestamp for time-aware answers.

**Response (200):** ``resp`` maps each requested model ID to its response
text. Thinking mode adds ``has_axiom_claims`` (drives the Validate button);
Research mode adds ``used_sources`` (objects with ``url``/``title``/
``snippet``) and ``used_urls``. Both include ``context_stats`` (session id,
mode, message and token counts).

**Streaming variants** (``…_stream/``) return ``text/event-stream``: a
``connected`` event, ``{"status": {…}}`` progress frames,
``{"content": "…", "done": false}`` chunks, and a final ``{"done": true, …}``
frame carrying ``wrapped_content``, ``used_sources``, ``used_urls``, and
``context_stats``. Errors mid-stream arrive as
``{"error": "…", "done": true}``.

Context & Preferences
~~~~~~~~~~~~~~~~~~~~~

``POST /input_webtext/`` — body ``{"textContent": "…", "currentUrl": "…"}``
(``textContent`` required). Appends scraped page text to the session
context. Returns ``{"status": "success", "session_id": …,
"context_stats": {…}}``; ``400`` when ``textContent`` is missing.

``POST /api/auto_scrape/`` — body ``{"current_url": "…"}``. Server-side
scrape of the active page, skipped when already scraped
(``{"status": "skipped", "reason": "already_scraped"}``). Target URLs are
checked against the SSRF egress policy first: blocked targets return
``400 {"error": "URL refused by security policy"}``.

``POST /clear_messages/`` — query parameter ``preserve_web``
(``"true"``/``"false"``, default ``"false"``). Clears the session
conversation, optionally keeping scraped web content.

``GET /get_source_urls/`` — query parameters ``query``, ``current_url``.
Returns ``{"resp": [{"url", "title", "snippet"}, …]}``.

``GET /api/get_preferred_urls/`` → ``{"urls": […]}``.
``POST /api/sync_preferred_urls/`` with ``{"urls": […]}`` →
``{"status": "success", "synced": <count>}``.

``GET /api/get_available_models/`` → ``{"models": [{"id", "provider",
"description", "supports_mcp", "supports_advanced", "display_name"}, …]}``.

``GET|POST /log_question/`` — fire-and-forget telemetry (``question``,
``button``, ``current_url``); always returns ``{"status": "success"}``.

XBRL Validation Endpoints
~~~~~~~~~~~~~~~~~~~~~~~~~

These back the per-response **Validate** button (see :doc:`xbrl_validation`).

``GET /api/axioms/has_claims/`` — keyed off the session cookie. Returns
``{"session_id": …, "has_claims": bool, "count": n}``.

``POST /api/axioms/validate/`` — body ``{"session_id": "…"}`` (falls back to
the cookie-derived session). Runs the deterministic Layer-1 proof over every
claim recorded for the session:

.. code-block:: json

   {
     "session_id": "…",
     "claims": [
       {
         "ratio": "gross_margin",
         "ticker": "AAPL",
         "period": "2023-09-30",
         "claimed_value": 0.441,
         "status": "VERIFIED",
         "expected": 0.4413,
         "actual": 0.441,
         "variance_pct": 0.07,
         "formula": "(Revenue - COGS) / Revenue",
         "xbrl_source": "…",
         "message": "…"
       }
     ],
     "summary": {"total": 1, "VERIFIED": 1, "FAILED": 0, "SKIPPED": 0,
                 "NOT_APPLICABLE": 0, "ERROR": 0}
   }

Per-claim ``status`` is one of ``VERIFIED``, ``FAILED``, ``SKIPPED``,
``NOT_APPLICABLE``, ``ERROR``.

``GET /api/axioms/xbrl/<filename>/`` — serves a bundled SEC XBRL filing as
``application/xml`` for the Sources popup. ``filename`` must match
``<ticker>-<yyyymmdd>.xml`` exactly; anything else (including path-traversal
attempts) returns ``404``.

News Signals
~~~~~~~~~~~~

``GET /api/signals/news/`` serves the latest **news→sentiment signals
artifact** produced by the Heartbeat pipeline (``Heartbeat/``). This is an
integration surface for external consumers (e.g., trading-research stacks);
the browser extension does not call it.

**Query parameters:**

- ``as_of=YYYY-MM-DD`` — point-in-time read: returns the newest artifact
  dated on or before that day. Malformed values return
  ``400 {"error": "bad_as_of"}``.
- ``tickers=AAPL,MSFT`` — filter the ``signals`` map to those symbols.

**Response (200):** the artifact JSON (schema:
``Heartbeat/schemas/signals-v2.schema.json``) minus internal provenance
fields, plus a computed ``staleness_hours``. Key fields: ``schema_version``
(always ``2`` — artifacts predating the 2026-07-14 field rename are
normalized at the boundary), ``profile``, ``generated_at``, ``window_hours``,
``watchlist``, ``status`` (``ok`` | ``degraded``), ``status_reason``,
``news_overview``, ``diagnostics``, and ``signals`` — a map of ticker →
``{sentiment, sentiment_score, rationale, headline, source, url, published,
guid, n_articles}`` with ``sentiment_score`` in ``[-1, 1]``.

``404 {"error": "no_signals"}`` when no artifact exists yet. Responses carry
an ``ETag`` validator and ``Cache-Control: public, max-age=300``.
``Last-Modified`` is sent only on unfiltered responses — ``tickers=``-filtered
variants are ETag-only, so conditional requests for them must use
``If-None-Match``.

News Items
~~~~~~~~~~

``GET /api/news/items/`` serves the **raw news stories** of the newest
Heartbeat batch — the corpus the signals above are derived from, before any
LLM scoring. Like ``/api/signals/news/`` it is an integration surface for
external consumers; the browser extension does not call it.

**Query parameters:**

- ``limit=N`` — how many stories to return, newest first. Clamped to
  ``[1, 200]``; defaults to ``50`` when absent. A non-integer value returns
  ``400 {"error": "bad_limit"}``.

There is no ``as_of`` here: this endpoint always reads the single newest
batch.

**Response (200):** ``{schema_version, items, count, batch}``, where ``batch``
names the source file and each entry of ``items`` is
``{guid, headline, url, source, published, description, tickers,
editorial_score}``. ``published`` is epoch seconds.

.. note::

   ``editorial_score`` is the pipeline's newsworthiness score — the gate that
   decides which stories become sentiment candidates
   (``SIGNALS_MIN_EDITORIAL_SCORE``). It is unrelated to the ``[-1, 1]``
   ``sentiment_score`` served by ``/api/signals/news/``.

``404 {"error": "no_items"}`` when no batch exists, or when the newest one is
unreadable or validates to zero stories — the endpoint never falls back to an
older batch, and never returns a ``500``. Responses carry an ``ETag`` and
``Cache-Control: public, max-age=300``. ``Last-Modified`` is sent only on the
default-limit variant — an explicit ``?limit`` slices the batch differently,
so those variants are ETag-only and conditional requests for them must use
``If-None-Match``.

---

Usage Examples
--------------

All examples use ``curl``. Replace ``agenticfinsearch.org`` with the droplet IP/domain and ``$API_KEY`` with the actual key.

Health Check
~~~~~~~~~~~~

.. code-block:: bash

   curl https://agenticfinsearch.org/health/

List Models
~~~~~~~~~~~

.. code-block:: bash

   curl -H "Authorization: Bearer $API_KEY" \
        https://agenticfinsearch.org/v1/models

Thinking Mode (MCP Tools)
~~~~~~~~~~~~~~~~~~~~~~~~~~

Ask a specific financial question. The agent uses SEC-EDGAR and Yahoo Finance MCP tools to fetch data.

.. code-block:: bash

   curl -X POST https://agenticfinsearch.org/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $API_KEY" \
     -d '{
       "model": "FinGPT",
       "mode": "thinking",
       "messages": [
         {"role": "user", "content": "What is the current price and P/E ratio of AAPL?"}
       ]
     }'

Research Mode (Deep Search)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Ask a broad research question. The agent searches the web and synthesizes an answer.

.. code-block:: bash

   curl -X POST https://agenticfinsearch.org/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $API_KEY" \
     -d '{
       "model": "FinGPT",
       "mode": "research",
       "messages": [
         {"role": "user", "content": "What are the key risks facing the US banking sector in 2026?"}
       ],
       "search_domains": ["reuters.com", "bloomberg.com", "wsj.com"],
       "preferred_links": ["https://www.federalreserve.gov"]
     }'

Research Mode with Domain Scoping
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   curl -X POST https://agenticfinsearch.org/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $API_KEY" \
     -d '{
       "model": "FinGPT-Light",
       "mode": "research",
       "messages": [
         {"role": "user", "content": "Summarize recent SEC enforcement actions in crypto."}
       ],
       "search_domains": ["sec.gov"],
       "user_timezone": "America/New_York",
       "user_time": "2026-02-22T10:00:00-05:00"
     }'

With URL Context (Page Analysis)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Inject a page's content before asking a question about it.

.. code-block:: bash

   curl -X POST https://agenticfinsearch.org/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $API_KEY" \
     -d '{
       "model": "FinGPT",
       "mode": "thinking",
       "url": "https://finance.yahoo.com/quote/MSFT/",
       "messages": [
         {"role": "user", "content": "Analyze this stock page and summarize the key metrics."}
       ]
     }'

Multi-Turn Conversation
~~~~~~~~~~~~~~~~~~~~~~~

Pass full conversation history. The API is stateless — include all prior turns each time.

.. code-block:: bash

   curl -X POST https://agenticfinsearch.org/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $API_KEY" \
     -d '{
       "model": "FinGPT",
       "mode": "thinking",
       "messages": [
         {"role": "user", "content": "What is AAPL trading at?"},
         {"role": "assistant", "content": "Apple (AAPL) is currently trading at $195.50."},
         {"role": "user", "content": "How does that compare to its 52-week high?"}
       ]
     }'

Normal Mode (No Tools / No Search)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   curl -X POST https://agenticfinsearch.org/v1/chat/completions \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $API_KEY" \
     -d '{
       "model": "FinGPT",
       "mode": "normal",
       "messages": [
         {"role": "user", "content": "Explain what a P/E ratio is."}
       ]
     }'

---

Python Benchmarking Quick Start
-------------------------------

Below is a complete, copy-paste-ready Python script for benchmarking the API. It tests all three modes and measures response time.

.. code-block:: python

   """Agentic FinSearch API Benchmark Script."""
   import requests
   import time
   import json

   BASE_URL = "https://agenticfinsearch.org"
   API_KEY = "<YOUR_API_KEY>"  # omit Authorization header if auth is disabled

   HEADERS = {
       "Content-Type": "application/json",
       "Authorization": f"Bearer {API_KEY}",
   }


   def call_completions(mode: str, question: str, model: str = "FinGPT", **kwargs) -> dict:
       """Send a chat completion request and return (response_dict, elapsed_seconds)."""
       payload = {
           "model": model,
           "mode": mode,
           "messages": [{"role": "user", "content": question}],
           **kwargs,
       }

       start = time.time()
       resp = requests.post(
           f"{BASE_URL}/v1/chat/completions",
           headers=HEADERS,
           json=payload,
           timeout=120,
       )
       elapsed = time.time() - start

       resp.raise_for_status()
       data = resp.json()
       return data, elapsed


   def test_health():
       """Verify the server is running."""
       resp = requests.get(f"{BASE_URL}/health/", timeout=10)
       assert resp.status_code == 200
       data = resp.json()
       assert data["status"] == "healthy"
       print(f"[PASS] Health check: {data['version']}")


   def test_models():
       """Verify the models endpoint returns expected models."""
       resp = requests.get(f"{BASE_URL}/v1/models", headers=HEADERS, timeout=10)
       assert resp.status_code == 200
       data = resp.json()
       model_ids = [m["id"] for m in data["data"]]
       assert "FinGPT" in model_ids
       assert "FinGPT-Light" in model_ids
       print(f"[PASS] Models: {model_ids}")


   def test_thinking_mode():
       """Benchmark thinking mode (MCP tools)."""
       data, elapsed = call_completions(
           mode="thinking",
           question="What is the current price of AAPL?",
       )
       content = data["choices"][0]["message"]["content"]
       sources = data["sources"]
       print(f"[PASS] Thinking mode ({elapsed:.1f}s)")
       print(f"  Response length: {len(content)} chars")
       print(f"  Sources: {json.dumps(sources, indent=2)}")
       assert len(content) > 0
       return elapsed


   def test_research_mode():
       """Benchmark research mode (deep search)."""
       data, elapsed = call_completions(
           mode="research",
           question="What are analysts saying about NVIDIA earnings?",
           search_domains=["reuters.com", "cnbc.com"],
       )
       content = data["choices"][0]["message"]["content"]
       sources = data["sources"]
       print(f"[PASS] Research mode ({elapsed:.1f}s)")
       print(f"  Response length: {len(content)} chars")
       print(f"  Sources: {len(sources)} URLs")
       for s in sources[:3]:
           print(f"    - {s.get('url', s.get('title', 'N/A'))}")
       assert len(content) > 0
       return elapsed


   def test_normal_mode():
       """Benchmark normal mode (no tools, no search)."""
       data, elapsed = call_completions(
           mode="normal",
           question="Explain what a dividend yield is.",
       )
       content = data["choices"][0]["message"]["content"]
       print(f"[PASS] Normal mode ({elapsed:.1f}s)")
       print(f"  Response length: {len(content)} chars")
       assert len(content) > 0
       return elapsed


   def test_error_handling():
       """Verify the API returns proper errors for bad requests."""
       # Missing mode
       resp = requests.post(
           f"{BASE_URL}/v1/chat/completions",
           headers=HEADERS,
           json={"model": "FinGPT", "messages": [{"role": "user", "content": "test"}]},
           timeout=30,
       )
       assert resp.status_code == 400
       assert "mode is required" in resp.json()["error"]["message"]

       # Invalid model
       resp = requests.post(
           f"{BASE_URL}/v1/chat/completions",
           headers=HEADERS,
           json={
               "model": "nonexistent",
               "mode": "thinking",
               "messages": [{"role": "user", "content": "test"}],
           },
           timeout=30,
       )
       assert resp.status_code == 404

       # Empty messages
       resp = requests.post(
           f"{BASE_URL}/v1/chat/completions",
           headers=HEADERS,
           json={"model": "FinGPT", "mode": "thinking", "messages": []},
           timeout=30,
       )
       assert resp.status_code == 400

       print("[PASS] Error handling: all validation errors returned correctly")


   if __name__ == "__main__":
       print("=" * 60)
       print("Agentic FinSearch API Benchmark")
       print("=" * 60)

       test_health()
       test_models()
       test_error_handling()

       timings = {}
       timings["thinking"] = test_thinking_mode()
       timings["research"] = test_research_mode()
       timings["normal"] = test_normal_mode()

       print("\n" + "=" * 60)
       print("Timing Summary")
       print("=" * 60)
       for mode, t in timings.items():
           print(f"  {mode:12s}: {t:.1f}s")
       print(f"  {'TOTAL':12s}: {sum(timings.values()):.1f}s")

---

Behavioral Notes
----------------

.. _v1-statelessness:

Statelessness
~~~~~~~~~~~~~

The **/v1 API is fully stateless**: each request creates a fresh session context, so the client must send the full ``messages`` array every time. The extension endpoints are the opposite — they are session-scoped via the signed ``fingpt_sessionid`` cookie and keep conversation history server-side until the session expires (1 hour idle) or is cleared.

Response Times
~~~~~~~~~~~~~~

- **Thinking mode**: 5-30 seconds (depends on number of MCP tool calls).
- **Research mode**: 15-90 seconds (depends on search depth, number of sub-queries).
- **Normal mode**: 2-10 seconds.

Set ``timeout`` accordingly in your HTTP client (recommended: **120 seconds**).

Token Usage
~~~~~~~~~~~

The ``usage`` field provides **approximate** token counts. ``prompt_tokens`` comes from the context manager's internal counter. ``completion_tokens`` is estimated as ``len(response_text) // 4``. These are useful for relative benchmarking but are not exact billing-grade counts.

URL Scraping
~~~~~~~~~~~~

When a ``url`` is provided, the backend scrapes it using Playwright (headless browser). The scraped content is injected into the agent's context before response generation. This adds 2-5 seconds to the response time.

Error Safety
~~~~~~~~~~~~

The API **never** exposes internal error details (stack traces, file paths) to clients. All 500 errors return a generic message. Full error details are logged server-side only.
