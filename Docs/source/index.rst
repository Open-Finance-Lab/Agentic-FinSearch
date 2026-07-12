Agentic FinSearch
==================

**Agentic FinSearch** is a powerful browser extension that combines financial information retrieval with advanced AI capabilities. It provides real-time access to financial data, documents, and insights through an intuitive chat interface.

.. toctree::
   :maxdepth: 3
   :caption: Contents:

   introduction
   updates
   installation/index
   usage/index
   xbrl_validation
   api_reference
   mcp_tools
   project_structure
   case_studies
   code_of_conduct

.. note::
   This documentation covers version 0.16.0 of Agentic FinSearch.

   **New in 0.16.0:**

   - **XBRL Validation Pipeline**: A three-stage *tagging → retrieval → matching* pipeline that checks numerical claims in agent responses against SEC XBRL filings and surfaces per-claim verdicts (Verified, Mismatch, Skipped, Not Applicable). See :doc:`xbrl_validation`.
   - **Validate button**: Lazy, user-triggered certification. The button appears whenever the most recent response emitted a ratio claim; clicking it runs a deterministic check and inserts inline mismatch marks plus a Ground Truth section in the Sources popup.
   - **Three Layer 1 axioms**: Balance-sheet equality (``A = L + E``), gross margin, and current ratio — resolved against vendored SEC ``companyfacts`` for three registrants (Apple, Microsoft, Tesla) across their reported filing history, via an as-of-aware local truth layer. A cloud-hosted **SEC XBRL Filing Tree** is in development to extend coverage to the full registrant universe.

   **New in 0.13.3:**

   - **Query Planner & Skills**: Heuristic-based skill routing constrains tools per query for faster, more focused responses.
   - **Hallucination Mitigation**: Improved accuracy for financial data retrieval and synthesis.

   **New in 0.13.1:**

   - **API Now Supported**: The OpenAI-compatible API may now be used. For details, please check the API Reference.

   **New in 0.13.0:**

   - **Deep Research Mode**: Multi-step research engine with query decomposition, parallel execution, gap detection, and synthesis.
   - **OpenAI-Compatible API**: RESTful ``/v1/chat/completions`` endpoint for programmatic access.
   - **TradingView MCP**: Real-time technical analysis and screener data via Model Context Protocol.
   - **Gemini Integration**: Google Gemini models supported as foundation model providers.

Features
--------

- **Multi-Model Support**: Choose between OpenAI, Google Gemini, DeepSeek, Anthropic (Claude), and custom fine-tuned models
- **MCP Support**: Model Context Protocol integration for enhanced agent capabilities
- **Browser Extension**: Seamless integration with major financial websites
- **Real-time Web Scraping**: Extract and analyze content from financial websites

Quick Start (Docker)
--------------------

The simplest way to get started is using Docker:

1. Clone the repository and navigate to the project root.
2. Copy ``Main/backend/.env.example`` to ``Main/backend/.env`` and add your API keys.
3. Start the application:

   .. code-block:: bash

      docker compose up --build

4. Load the extension in your browser from the ``Main/frontend/dist`` directory.
5. Navigate to any supported financial website and start chatting!

Manual Installation
-------------------

For developers who wish to run or modify the agent outside of Docker, please refer to:

* :doc:`installation/manual_install`: Detailed steps for manual backend setup using ``uv``.
* :doc:`project_structure`: Overview of the codebase and frontend build process.
