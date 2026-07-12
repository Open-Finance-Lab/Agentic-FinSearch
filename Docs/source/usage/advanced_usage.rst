
.. admonition:: When to use this section
   :class: note

   Please make sure the search agent is successfully installed
   and running before testing out any of the examples below!

.. note::
   As a reminder, the search agent currently does NOT work on **Brave** browser.

Multi-Model Support
-------------------

Agentic FinSearch supports multiple foundation models.

Available Models
~~~~~~~~~~~~~~~~

**Supported Models:**

.. list-table::
   :widths: 20 15 30 35
   :header-rows: 1

   * - Model ID
     - Provider
     - Underlying Model
     - Description
   * - ``FinGPT``
     - Google
     - ``gemini-3-flash-preview``
     - Default model. 1M token context window. Best for comprehensive analysis.
   * - ``FinGPT-Light``
     - OpenAI
     - ``gpt-5.1-chat-latest``
     - Fast and efficient. 128K token context window.
   * - ``Buffet-Agent``
     - Custom
     - Hugging Face endpoint
     - Fine-tuned financial model with specialized knowledge.

Switching Models
~~~~~~~~~~~~~~~~

1. Click the **Settings** button in the agent popup.
2. Select your preferred model from the dropdown.
3. The agent will use the selected model for all subsequent queries.

MCP (Model Context Protocol) Integration
----------------------------------------

MCP enables advanced agent capabilities through tool integration.

MCP Features
~~~~~~~~~~~~

- **Yahoo Finance MCP**: Directly fetches real-time market data, stock prices, and company profiles via the ``yfinance`` API.
- **SEC-EDGAR MCP**: Enables the agent to directly access SEC filings like 10-K, 10-Q, 8-K and extract financial data from them.
- **TradingView MCP**: Fetches technical analysis indicators, oscillators, moving averages, and market screener data.
- **XBRL Taxonomy MCP**: Retrieval-then-select over the FASB US-GAAP taxonomy (``lookup_xbrl_tags``, ``validate_xbrl_tag``, ``query_xbrl_filing``) — backs Stage 1 of the :doc:`XBRL validation pipeline <../xbrl_validation>`.

Deep Research Mode
------------------

For complex financial questions that require synthesizing information from multiple sources, the agent offers a **Deep Research Mode**.

How It Works
~~~~~~~~~~~~

1. **Query Decomposition**: The agent's ``QueryAnalyzer`` breaks your question into typed sub-questions (numerical, qualitative, analytical).
2. **Parallel Execution**: The ``ResearchExecutor`` routes each sub-question to the best source — MCP tools for numerical data, web search for qualitative context.
3. **Gap Detection**: The ``GapDetector`` identifies any missing information and triggers follow-up searches.
4. **Synthesis**: The ``Synthesizer`` combines all findings into a coherent, well-sourced response.

To use deep research mode, select **Research** in the mode dropdown next to the prompt box, or pass ``"mode": "research"`` via the API.

.. note::
   Research mode typically takes 15-90 seconds depending on query complexity. The agent performs multiple parallel searches and synthesizes the results.

Custom URL Preferences
----------------------

Configure preferred financial websites for the agent to search.

Setting Preferred URLs
~~~~~~~~~~~~~~~~~~~~~~

1. Open **Settings**
2. Navigate to **Preferred Links**
3. Add URLs of trusted financial sources
4. Save your preferences

The agent will prioritize these sources when answering in **Research** mode.

Example preferred URLs:

- ``https://www.sec.gov/edgar``
- ``https://investor.apple.com``
- ``https://www.federalreserve.gov``

Advanced Query Techniques
-------------------------

Query Modes
~~~~~~~~~~~

**Thinking mode (default):**
- Works from the current page's scraped context and calls MCP tools for live financial data
- Faster responses
- Best for page-specific and targeted financial questions

**Research mode:**
- Runs the deep-research pipeline across the open domain plus your Preferred links
- More comprehensive, multi-source responses
- Best for broad research and analysis

Effective Prompting
~~~~~~~~~~~~~~~~~~~

For best results:

1. **Be Specific**: "What was Apple's Q3 2024 revenue?" vs "Tell me about Apple"
2. **Request Sources**: Add "with sources" to get citations
3. **Compare Data**: "Compare Tesla's P/E ratio to industry average"
4. **Time-bound Queries**: Include dates for historical data

Monitoring Agent Activity
-------------------------

Real-time Logs
~~~~~~~~~~~~~~

If you are running the agent locally, monitor the agent's search and scraping activity:

1. Inside your terminal and / or browser Dev Tool (console), watch for:
   
   - MCP status
   - URLs being scraped
   - Search queries executed
   - Model API calls
   - Citations if using Research mode
   - Error messages

Debug Mode
~~~~~~~~~~

For troubleshooting:

.. code-block:: bash

   # Set debug environment variable
   export DJANGO_DEBUG=True
   
   # Then restart the backend server via docker, uv or python

Performance Optimization
------------------------

Context Management
~~~~~~~~~~~~~~~~~~

Agentic FinSearch tracks each session with the **Unified Context Manager** —
session-scoped conversation history, scraped page content, and research
findings held in a structured JSON form. See :doc:`memory_system` for
details.

**How it works:**

- **Session-Based**: The agent maintains the full conversation history for the current session.
- **Storage & Expiry**: Session state lives in the Django cache (file-based in development, Redis in production) and expires after **1 hour** of inactivity.
- **Session Isolation**: Each browser tab/session maintains its own isolated context.

.. note::
   Each browser tab maintains its own conversation context. Refreshing the page starts a new session unless a custom session ID is used.

Troubleshooting Advanced Features
---------------------------------

Common Issues
~~~~~~~~~~~~~

**MCP features not working:**

- Confirm the API key for your selected model's provider is valid: ``FinGPT``
  → ``GOOGLE_API_KEY``, ``FinGPT-Light`` → ``OPENAI_API_KEY``,
  ``Buffet-Agent`` → ``BUFFET_AGENT_API_KEY``.
- Monitor terminal for MCP-related errors. If errors directly from the MCPs exist, contact Felix via Discord or WeChat.

**Slow responses with Research mode:**

- Reduce number of preferred URLs
- Check internet connection
- Research mode searches multiple sources in parallel and synthesizes results, which typically takes 15-90 seconds
