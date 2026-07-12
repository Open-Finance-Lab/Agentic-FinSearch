Overview
========

Agentic FinSearch is a web-based agent in finance, business, accounting domains to assist users in information retrieval and data analysis. When generating responses, it provides the information sources to help users evaluate the responses' quality.

Key Features
------------

- It is a powerful **information search-and-retrieval engine**: A search agent for websites (Yahoo Finance, Bloomberg, TradingView, CNBC, and more) and local files (SEC 10K, XBRL files (eXtensible Business Reporting Language)).
- **Integrated MCP Tools**: Built-in support for Model Context Protocol (MCP) servers, including **SEC-EDGAR** for company filings, **Yahoo Finance** for real-time market data, **TradingView** for crypto technical analysis, and an **XBRL Taxonomy** server that grounds tagging in the official US-GAAP taxonomy.
- **XBRL Claim Validation**: A deterministic, user-triggered pipeline that checks the numerical claims in agent responses against SEC XBRL filings, with per-claim verdicts and filing provenance (see :doc:`xbrl_validation`).
- **News→Signals Pipeline**: A standalone heartbeat service (``Heartbeat/``) that ingests financial news feeds and publishes dated, deterministic news-sentiment signals for a Dow-30 watchlist, served over ``GET /api/signals/news/``.
- **Discord Concierge**: A Discord chat adapter (``Concierge/``) that brings the same agent into Discord servers.
- **Deep Research Mode**: Multi-step research engine that decomposes complex queries into sub-questions, executes them in parallel, detects coverage gaps, and synthesizes comprehensive answers.
- Also a powerful **answer engine**: an answer agent that performs open search (open-domain search plus local file search) to quickly locate relevant financial information from various sources.
- For generated responses, users may perform quick **source verification**, ensuring reliability and accuracy.

.. image:: _static/images/agent_mcp_interaction.jpg
   :alt: Web agent interaction diagram
   :align: left
   :width: 800px

**NO DIRECT TRADING ADVICE**

Motivation
----------

Most of the current LLM-powered agents are general-purpose. They do not have a built-in knowledge base, meaning they cannot be personalized for users that don't possess strong coding skills. The few available agents that can parse a local knowledge base or have a built-in knowledge base are not popularized and far from the mainstream. FinGPT Agents aim to change this by providing a **user-friendly interface** with a **local** knowledge base that can be **easily customized**, and generate insights incorporating both **open-domain information** and **local information**. 

For students, imagine how much time may be saved from reviewing for an exam, if all you need to do is to copy all your notes into a local folder, and the agent will generate a tailored, personalized exam preparation plan for you, be it a crib sheet or a comprehensive study guide. For professionals, imagine how much time may be saved from reviewing a large number of documents, if all you need to do is to copy all your documents into a local folder, and the agent will generate a tailored, personalized report for you. 

*With FinGPT Agents, we aim to level up the general public's knowledge in finance, accounting, and business.*

Road Map
--------

.. image:: _static/images/current_roadmap.png
   :alt: FinGPT Agents current roadmap
   :align: left
   :width: 800px

Current roadmap as of early 2026.

