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

The API is served by a Django backend on port **8000**.

**Production** (Fedora droplet at ``134.122.1.153``, IPv4 only):

.. code-block:: text

   https://agenticfinsearch.org:8000

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
   The extension and utility endpoints documented below do **not** currently
   require an API key. They are protected by per-client rate limiting and
   cookie-rooted session isolation. Extending bearer authentication to these
   endpoints is tracked on the security roadmap.

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

   curl https://agenticfinsearch.org:8000/health/

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
        https://agenticfinsearch.org:8000/v1/models

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

All models support both ``thinking`` (MCP) and ``research`` (deep search) modes.

---

Usage Examples
--------------

All examples use ``curl``. Replace ``agenticfinsearch.org`` with the droplet IP/domain and ``$API_KEY`` with the actual key.

Health Check
~~~~~~~~~~~~

.. code-block:: bash

   curl https://agenticfinsearch.org:8000/health/

List Models
~~~~~~~~~~~

.. code-block:: bash

   curl -H "Authorization: Bearer $API_KEY" \
        https://agenticfinsearch.org:8000/v1/models

Thinking Mode (MCP Tools)
~~~~~~~~~~~~~~~~~~~~~~~~~~

Ask a specific financial question. The agent uses SEC-EDGAR and Yahoo Finance MCP tools to fetch data.

.. code-block:: bash

   curl -X POST https://agenticfinsearch.org:8000/v1/chat/completions \
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

   curl -X POST https://agenticfinsearch.org:8000/v1/chat/completions \
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

   curl -X POST https://agenticfinsearch.org:8000/v1/chat/completions \
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

   curl -X POST https://agenticfinsearch.org:8000/v1/chat/completions \
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

   curl -X POST https://agenticfinsearch.org:8000/v1/chat/completions \
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

   curl -X POST https://agenticfinsearch.org:8000/v1/chat/completions \
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

   BASE_URL = "https://agenticfinsearch.org:8000"
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

Statelessness
~~~~~~~~~~~~~

The API is **fully stateless**. Each request creates a fresh session context. To maintain conversation history, the client must send the full ``messages`` array with every request.

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
