MCP Tools Integration
=====================

Agentic FinSearch leverages the **Model Context Protocol (MCP)** to provide advanced financial capabilities through specialized tools. MCP servers are defined in ``Main/backend/mcp_server_config.json`` and managed by the ``MCPClientManager`` class.

Available MCP Servers
---------------------

1. **SEC-EDGAR Server**
   - **Purpose**: Access official SEC filings (10-K, 10-Q, 8-K) and XBRL company facts.
   - **Tools** (from the external ``sec-edgar-mcp`` package): company lookup (``get_cik_by_ticker``, ``get_company_info``, ``search_companies``, ``get_company_facts``), filings (``get_recent_filings``, ``get_filing_content``, ``get_filing_sections``, ``analyze_8k``), financials (``get_financials``, ``get_segment_data``, ``get_key_metrics``, ``compare_periods``, ``discover_company_metrics``, ``get_xbrl_concepts``, ``discover_xbrl_concepts``), insider activity (``get_insider_transactions``, ``get_insider_summary``, ``get_form4_details``, ``analyze_form4_transactions``, ``analyze_insider_sentiment``), and ``get_recommended_tools``.
   - **Automatic Activation**: Triggered when you ask questions about company filings or historical data.
   - **Transport**: Stdio (``python -m sec_edgar_mcp.server``)

2. **Yahoo Finance Server**
   - **Purpose**: Real-time market data and historical price analysis.
   - **Tools**: ``get_stock_info``, ``get_stock_financials``, ``get_stock_news``, ``get_stock_history``, ``get_stock_analysis``, ``get_earnings_info``, ``get_options_chain``, ``get_options_summary``, ``get_holders``.
   - **Automatic Activation**: Used for stock price queries and basic market research.
   - **Transport**: Stdio (custom server in ``mcp_server/yahoo_finance_server.py``)

3. **TradingView Server**
   - **Purpose**: Technical analysis and screeners for **cryptocurrencies** (crypto exchanges only).
   - **Tools**: ``get_coin_analysis``, ``get_top_gainers``, ``get_top_losers``, ``get_bollinger_scan``, ``get_rating_filter``, ``get_consecutive_candles``, ``get_advanced_candle_pattern``.
   - **Automatic Activation**: Used for crypto technical-analysis questions and market screening.
   - **Transport**: Stdio (custom server in ``mcp_server/tradingview/``)

4. **XBRL Taxonomy Server**
   - **Purpose**: Ground XBRL tagging in the official US-GAAP 2026 taxonomy.
   - **Tools**: ``lookup_xbrl_tags`` (natural-language taxonomy search), ``validate_xbrl_tag`` (does this tag exist?), ``query_xbrl_filing`` (reported values for a tag in a bundled filing).
   - **Automatic Activation**: Backs Stage 1 of the :doc:`XBRL validation pipeline <xbrl_validation>` and taxonomy questions.
   - **Transport**: Stdio (custom server in ``mcp_server/xbrl/``)

.. note::
   A generic **Filesystem server** (``@modelcontextprotocol/server-filesystem``)
   exists in the configuration but is **disabled**, and all of its tools sit
   on a permanent deny-list in ``mcp_client/tool_policy.py`` — they are never
   reachable regardless of configuration.

Architecture
------------

The MCP system consists of two layers:

**MCP Client** (``mcp_client/``):

- ``mcp_manager.py``: Manages connections to all configured MCP servers, supports both Stdio and SSE transports.
- ``agent.py``: Creates the financial agent with MCP tools dynamically loaded and wrapped as callable functions.
- ``tool_wrapper.py``: Converts MCP tool schemas into Python callables compatible with the OpenAI Agents SDK.
- ``tool_policy.py``: Deny-by-default tool policy — tools reach the agent only through explicit allow-lists, enforced when tools are attached **and** again at execution time; the filesystem server's tools are permanently denied.

**MCP Servers** (``mcp_server/``):

- ``yahoo_finance_server.py``: Custom Yahoo Finance server using ``yfinance``.
- ``tradingview/``: Custom TradingView server using ``tradingview-ta`` and ``tradingview-screener``.
- ``xbrl/``: XBRL taxonomy server (US-GAAP 2026 taxonomy search and validation) plus bundled sample filings.
- ``handlers/``: Shared handler modules for MCP request processing.
- ``cache.py``, ``errors.py``, ``executor.py``, ``validation.py``: Shared infrastructure.

How to Enable
-------------

MCP tools are enabled by default. On startup the agent connects to every server not marked ``"disabled": true`` in ``mcp_server_config.json``; a tool must additionally be on the active allow-list (``mcp_client/tool_policy.py``) to reach the agent.

Ensure your ``.env`` file in ``Main/backend/`` is properly configured:

.. code-block:: bash

   # Required for OpenAI-based agent orchestration
   OPENAI_API_KEY=your_key_here

   # SEC-EDGAR requires a user agent string
   SEC_EDGAR_USER_AGENT="YourName (your.email@example.com)"

Configuration
-------------

MCP servers are configured in ``Main/backend/mcp_server_config.json``. Each entry specifies:

- **transport**: ``stdio`` or ``sse``
- **command** / **args**: The command to launch the server
- **env**: Environment variables passed to the server process

.. code-block:: json

   {
     "servers": {
       "yahoo-finance": {
         "transport": "stdio",
         "command": "python",
         "args": ["-m", "mcp_server.yahoo_finance_server"],
         "enabled": true
       }
     }
   }

To add a custom MCP server, add a new entry to this file and restart the backend.

Using MCP Tools
---------------

You don't need to manually activate tools. Simply ask questions like:

- *"What were Apple's key risk factors in their latest 10-K?"* → SEC-EDGAR
- *"Show me the current price and volume for NVDA."* → Yahoo Finance
- *"What are the technical indicators for BTC-USD?"* → TradingView (crypto)
- *"Compare the revenue growth of Tesla and Rivian from their last three filings."* → SEC-EDGAR

The agent will automatically determine which MCP tool is best suited to answer your query.

Troubleshooting
---------------

**MCP tools not connecting:**

- Check the terminal output for MCP connection errors on startup.
- Verify the required packages are installed (``sec-edgar-mcp``, ``yfinance``, ``tradingview-ta``).
- Ensure environment variables (``SEC_EDGAR_USER_AGENT``) are set correctly.

**Slow MCP responses:**

- SEC-EDGAR queries may take 10-30 seconds depending on filing size.
- Yahoo Finance and TradingView are typically faster (2-5 seconds).
- Monitor the backend logs for detailed timing information.
