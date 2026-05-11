Update Logs
===========

This page tracks the history of updates and new features for Agentic FinSearch.

Version 0.16.0
--------------

- **XBRL Validation Pipeline**: A three-stage *tagging → retrieval → matching* pipeline that checks numerical claims in agent responses against SEC XBRL filings and surfaces per-claim verdicts. See :doc:`xbrl_validation`.
- **Validate Button**: User-triggered, deterministic validation. Appears in the response toolbar only when the response emitted at least one supported claim; clicking it runs the pipeline and renders inline mismatch marks plus a ``Ground Truth`` section in the Sources popup.
- **Three Layer 1 axioms**: Balance-sheet equality (``A = L + E`` with ``TempEquity`` handling for filers with non-controlling interests), gross margin, and current ratio — evaluated against locally cached XBRL filings for AAPL, MSFT, TSLA, JPM, and BRK.
- **``Not Applicable`` verdict**: Deterministic detection of metrics that are undefined for a filer (e.g., current ratio on an unclassified balance sheet from a bank or REIT), distinct from ``Skipped``, so the audit log stays honest.
- **Ground Truth provenance**: The XBRL filing path used by each validation pass is bridged into the existing Sources popup via the claim registry, so users see exactly which filing was consulted per claim.
- **Installation pages regrouped**: All installation guides now live under a single :doc:`Installation <installation/index>` section in the navigation to keep the top-level toctree compact.

Version 0.13.3
--------------

- **Query Planner & Skills**: Heuristic-based skill routing that constrains MCP tools per query type for faster, more focused responses. Six skills: summarize_page, stock_fundamentals, options_analysis, financial_statements, technical_analysis, web_research.
- **Hallucination Mitigation**: Improved accuracy for financial data retrieval benchmarks.

Version 0.13.1
--------------

- **OpenAI-Compatible API**: The ``/v1/chat/completions`` API is now fully supported for external consumers.

Version 0.13.0
--------------

- **OpenAI-Compatible REST API**: New ``/v1/chat/completions`` and ``/v1/models`` endpoints with Bearer token authentication, rate limiting, and three modes (thinking, research, normal).
- **API Deployment Readiness**: Full production deployment support with CORS, rate limiting, health checks, and environment-based configuration.
- **Research Engine Refinements**: Improved streaming integration with the research engine for both API and frontend call paths.

Version 0.12.0
--------------

- **Deep Research Mode**: Multi-step research engine with ``QueryAnalyzer``, ``ResearchExecutor``, ``GapDetector``, and ``Synthesizer`` for complex financial queries.
- **Iterative Research Loop**: ``run_iterative_research`` orchestration that decomposes questions, executes sub-queries in parallel, detects gaps, and synthesizes results.
- **Research Config**: Configurable research parameters in ``models_config.py`` (planner model, research model, max iterations, parallel search).

Version 0.11.0
--------------

- **TradingView MCP Server**: Custom MCP server for fetching technical analysis data and market screener results.
- **Gemini Integration**: Google Gemini models (``gemini-3-flash-preview``) added as a foundation model provider with 1M token context support.
- **Prompt Architecture**: Site-specific prompt templates in ``prompts/sites/`` for Yahoo Finance, SEC EDGAR, and TradingView.
- **Enhanced MCP Prompt**: Improved Yahoo Finance MCP prompt engineering for more accurate data retrieval.

Version 0.10.1
--------------

- **Yahoo Finance MCP**: Wrote a custom Yahoo Finance MCP server. Agent may now fetch data from Yahoo Finance much more accurately and precisely.
- **Performance Improvements**: Enhanced the custom tree-based scraper to be more stable.
