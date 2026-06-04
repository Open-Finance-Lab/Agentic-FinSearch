MCP Tools Integration
=====================

Agentic FinSearch leverages the **Model Context Protocol (MCP)** to provide advanced financial capabilities through specialized tools. MCP servers are defined in ``Main/backend/mcp_server_config.json`` and managed by the ``MCPClientManager`` class.

Available MCP Servers
---------------------

1. **SEC-EDGAR Server**
   - **Purpose**: Access official SEC filings (10-K, 10-Q, 8-K).
   - **Features**: Search by ticker, retrieve filing summaries, and extract financial statements.
   - **Automatic Activation**: Triggered when you ask questions about company filings or historical data.
   - **Transport**: Stdio (``python -m sec_edgar_mcp.server``)

2. **Yahoo Finance Server**
   - **Purpose**: Real-time market data and historical price analysis.
   - **Features**: Get current stock prices, volume, company profiles, and financial metrics.
   - **Automatic Activation**: Used for stock price queries and basic market research.
   - **Transport**: Stdio (custom server in ``mcp_server/yahoo_finance_server.py``)

3. **TradingView Server**
   - **Purpose**: Technical analysis and market screener data.
   - **Features**: Fetch technical indicators, oscillators, moving averages, and run screener queries across markets.
   - **Automatic Activation**: Used for technical analysis questions and market screening.
   - **Transport**: Stdio (custom server in ``mcp_server/tradingview/``)

4. **Filesystem Server**
   - **Purpose**: Read and search local files within the application directory.
   - **Features**: Access data files, configuration, and local resources.
   - **Transport**: Stdio (``npx @modelcontextprotocol/server-filesystem``)

Architecture
------------

The MCP system consists of two layers:

**MCP Client** (``mcp_client/``):

- ``mcp_manager.py``: Manages connections to all configured MCP servers, supports both Stdio and SSE transports.
- ``agent.py``: Creates the financial agent with MCP tools dynamically loaded and wrapped as callable functions.
- ``tool_wrapper.py``: Converts MCP tool schemas into Python callables compatible with the OpenAI Agents SDK.

**MCP Servers** (``mcp_server/``):

- ``yahoo_finance_server.py``: Custom Yahoo Finance server using ``yfinance``.
- ``tradingview/``: Custom TradingView server using ``tradingview-ta`` and ``tradingview-screener``.
- ``handlers/``: Shared handler modules for MCP request processing.
- ``cache.py``, ``errors.py``, ``executor.py``, ``validation.py``: Shared infrastructure.

How to Enable
-------------

MCP tools are enabled by default. The agent automatically connects to all servers defined in ``mcp_server_config.json`` on startup.

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
- *"What are the technical indicators for TSLA?"* → TradingView
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
