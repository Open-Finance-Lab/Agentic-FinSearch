Project Structure
=================

Top-Level Layout
----------------

.. code-block:: text

   Agentic-FinSearch/
   ├── .github/                      # CI/CD workflows
   ├── Benchmarks/                   # Benchmark documents and test results
   ├── Deploy/                       # Deployment configurations (Podman, Caddy)
   ├── InternalDocs/                 # Internal documentation (strategy, architecture, QA)
   ├── Docs/                         # Sphinx documentation (this site)
   ├── Main/
   │   ├── backend/                  # Django backend (uv-managed)
   │   └── frontend/                 # Browser extension (bun-managed)
   ├── docker-compose.yml            # Container orchestration
   ├── readthedocs.yml               # ReadTheDocs configuration
   ├── CONTRIBUTING.md               # Contribution guidelines
   └── README.md                     # Project overview


Backend Structure
-----------------

.. code-block:: text

   Main/backend/
   ├── django_config/                # Django project configuration
   │   ├── settings.py               # Development settings
   │   ├── settings_prod.py          # Production settings
   │   ├── urls.py                   # URL routing
   │   ├── wsgi.py                   # WSGI entry point
   │   └── asgi.py                   # ASGI entry point
   ├── api/                          # REST API layer
   │   ├── views.py                  # Main API endpoints
   │   ├── openai_views.py           # OpenAI-compatible API endpoints
   │   ├── middleware/               # CORS and custom middleware
   │   ├── utils/                    # API utility functions
   │   ├── apps.py                   # Django app configuration
   │   └── models.py                 # Database models
   ├── datascraper/                  # Data pipeline, RAG & research
   │   ├── datascraper.py            # Web scraping orchestration
   │   ├── research_engine.py        # Multi-step research engine
   │   ├── openai_search.py          # OpenAI search integration
   │   ├── models_config.py          # Model provider settings
   │   ├── playwright_tools.py       # Browser automation via Playwright
   │   ├── unified_context_manager.py # Session-based context tracking
   │   ├── context_integration.py    # Context system integration
   │   ├── preferred_links_manager.py # User link preferences
   │   ├── url_tools.py              # URL utilities
   │   ├── market_time.py            # Market timing utilities
   │   ├── numerical_validator.py    # Number validation
   │   └── quality_logger.py         # Quality logging
   ├── mcp_client/                   # MCP client integration
   │   ├── agent.py                  # Agent orchestration
   │   ├── mcp_manager.py            # MCP connection management
   │   ├── tool_wrapper.py           # Tool abstraction layer
   │   ├── prompt_builder.py         # Prompt generation
   │   └── apps.py                   # Django app configuration
   ├── mcp_server/                   # MCP server implementations
   │   ├── yahoo_finance_server.py   # Yahoo Finance MCP server
   │   ├── handlers/                 # MCP request handler modules
   │   ├── tradingview/              # TradingView MCP server
   │   ├── cache.py                  # Caching utilities
   │   ├── errors.py                 # Error definitions
   │   ├── executor.py               # Tool execution logic
   │   └── validation.py             # Input validation
   ├── prompts/                      # LLM prompt templates
   │   ├── core.md                   # Core system prompt
   │   ├── default_site.md           # Default site instructions
   │   └── sites/                    # Site-specific prompts
   │       ├── finance.yahoo.com.md  # Yahoo Finance context
   │       ├── sec.gov.md            # SEC EDGAR context
   │       └── tradingview.com.md    # TradingView context
   ├── data/                         # Static data files
   │   ├── preferred_links.json      # User-configured sources
   │   └── site_map.json             # Site structure mapping
   ├── tests/                        # Test suite
   ├── pyproject.toml                # Python project & dependencies
   ├── uv.lock                       # Locked dependency versions
   ├── Dockerfile                    # Production container image
   ├── entrypoint.sh                 # Container entry script
   ├── gunicorn.conf.py              # Gunicorn WSGI configuration
   ├── manage.py                     # Django CLI entry point
   ├── mcp_server_config.json        # MCP server definitions
   ├── .env.example                  # Environment template (dev)
   ├── .env.production.example       # Environment template (prod)
   └── Procfile                      # Heroku process definition


Backend Highlights
^^^^^^^^^^^^^^^^^^

* ``pyproject.toml`` + ``uv.lock`` manage Python dependencies via ``uv``.
* ``Dockerfile`` + ``entrypoint.sh`` build a production-ready container.
* ``gunicorn.conf.py`` configures the production WSGI server.
* ``django_config/settings.py`` vs ``settings_prod.py`` for environment separation.
* ``datascraper/`` contains the RAG pipeline, research engine, and context management logic.
* ``datascraper/research_engine.py`` implements the multi-step research pipeline (query decomposition, parallel execution, gap detection, synthesis).
* ``datascraper/unified_context_manager.py`` provides session-based context tracking with JSON structure.
* ``mcp_client/`` handles Model Context Protocol client connections and agent orchestration.
* ``mcp_server/`` contains standalone MCP server implementations (Yahoo Finance, TradingView).
* ``prompts/`` contains Markdown prompt templates, including site-specific context prompts.


Frontend Structure
------------------

.. code-block:: text

   Main/frontend/
   ├── src/
   │   ├── main.js                   # Extension entry point
   │   ├── manifest.json             # Chrome extension manifest (MV3)
   │   ├── modules/
   │   │   ├── api.js                # Backend API client
   │   │   ├── handlers.js           # Event handlers
   │   │   ├── helpers.js            # Utility functions
   │   │   ├── ui.js                 # UI state management
   │   │   ├── config.js             # Frontend configuration
   │   │   ├── backendConfig.js      # Backend URL settings
   │   │   ├── markdownRenderer.js   # Markdown parsing
   │   │   ├── sourcesCache.js       # Source caching logic
   │   │   ├── layoutState.js        # Layout persistence
   │   │   ├── components/
   │   │   │   ├── chat.js           # Chat interface
   │   │   │   ├── header.js         # Header component
   │   │   │   ├── popup.js          # Extension popup
   │   │   │   ├── settings_window.js # Settings UI
   │   │   │   └── link_manager.js   # Link management UI
   │   │   └── styles/
   │   │       ├── theme.css         # Theme variables
   │   │       ├── chat.css          # Chat styles
   │   │       ├── header.css        # Header styles
   │   │       ├── popup.css         # Popup styles
   │   │       └── windows.css       # Window/modal styles
   │   ├── assets/                   # Static assets (icons, images)
   │   └── vendor/
   │       └── marked.min.js         # Markdown library
   ├── dist/                         # Build output (load as extension)
   ├── node_modules/                 # bun dependencies
   ├── webpack.config.js             # Webpack bundler configuration
   ├── build-css.js                  # CSS build script
   ├── check-dist.js                 # Build verification script
   ├── package.json                  # Node dependencies & scripts
   ├── bun.lock                      # Locked bun dependencies
   ├── bunfig.toml                   # Bun configuration
   ├── babel.config.json             # Babel transpiler config
   └── Dockerfile.dev                # Development container


Frontend Highlights
^^^^^^^^^^^^^^^^^^^

* ``bun run build:full`` runs ``build-css.js`` then Webpack bundling.
* ``webpack.config.js`` handles JS bundling with Babel transpilation.
* ``dist/`` is the final output—load as unpacked extension in Chrome.
* ``src/modules/`` contains modular JavaScript organized by function.
* ``src/modules/styles/`` contains component-specific CSS files.
* ``check-dist.js`` verifies build artifacts are complete.


Docker Workflow
---------------

.. code-block:: bash

   cp Main/backend/.env.example Main/backend/.env
   docker compose up

The ``docker-compose.yml`` defines a single ``api`` service that builds and runs the backend on port 8000. Development volume mounts are included for hot-reload of backend code. An optional ``frontend`` service (activated with ``--profile dev``) runs ``bun run watch`` for frontend development.

Manual Backend Workflow
-----------------------

.. code-block:: bash

   cd Main/backend
   uv sync
   uv run python manage.py runserver

Manual Frontend Workflow
------------------------

.. code-block:: bash

   cd Main/frontend
   bun install
   bun run build:full
