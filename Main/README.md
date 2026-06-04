# Project Structure

This document provides comprehensive details about the Agentic FinSearch project structure.

---

## 1. Top-Level Layout

```markdown
Agentic-FinSearch/
├── Main/
│   ├── backend/               # Django backend with MCP agent orchestration
│   └── frontend/              # Browser extension (Webpack-bundled JS)
├── Docs/                      # Sphinx documentation & implementation plans
├── DevSummaries/              # Developer documentation (API docs, architecture references)
├── Deploy/                    # Deployment configuration & scripts
├── docker-compose.yml         # One-command deployment (backend)
└── CONTRIBUTING.md            # Contribution guidelines
```

---

## 2. Backend (`Main/backend/`)

### 2.1 Directory Structure

```markdown
backend/
├── django_config/              # Django project scaffolding
│   ├── asgi.py                 # ASGI entry-point (WebSockets & async workers)
│   ├── settings.py             # Development Django configuration
│   ├── settings_prod.py        # Production Django configuration
│   ├── urls.py                 # Global URL dispatcher
│   └── wsgi.py                 # WSGI entry-point (traditional sync servers)
├── api/                        # Django app — API endpoints
│   ├── views.py                # Browser extension endpoints (chat, streaming, context)
│   ├── openai_views.py         # OpenAI-compatible API (/v1/chat/completions)
│   ├── views_debug.py          # Debug/diagnostic endpoints
│   ├── models.py               # Database models
│   ├── apps.py                 # Django app configuration
│   └── utils/                  # Shared utilities
│       └── llm_debug_logger.py # LLM context debugging (enable via LLM_DEBUG_LOG=true)
├── datascraper/                # Core data processing & context management
│   ├── datascraper.py          # Agent response orchestration (thinking + research modes)
│   ├── research_engine.py      # Multi-step iterative research with streaming
│   ├── openai_search.py        # OpenAI Responses API web search integration
│   ├── unified_context_manager.py  # Session-based context (Django cache backend)
│   ├── context_integration.py  # Bridge between Django views and context manager
│   ├── url_tools.py            # URL scraping (requests → Playwright → LLM compression)
│   ├── models_config.py        # Model & provider configuration
│   └── preferred_links_manager.py  # User-curated preferred sources
├── planner/                    # Query planning & skill routing (v0.13.3+)
│   ├── planner.py              # Heuristic query analyzer → ExecutionPlan
│   ├── plan.py                 # ExecutionPlan dataclass
│   ├── registry.py             # Skill registry (auto-discovers skills)
│   └── skills/                 # Skill definitions
│       ├── base.py             # BaseSkill abstract class
│       ├── summarize_page.py   # Page summarization (no tools, 1 turn)
│       ├── stock_fundamentals.py   # Stock data queries
│       ├── options_analysis.py     # Options chain & volume analysis
│       ├── financial_statements.py # Earnings, revenue, balance sheet
│       ├── technical_analysis.py   # RSI, MACD, Bollinger Bands (TradingView)
│       └── web_research.py         # Fallback: all tools, 10 turns
├── mcp_client/                 # MCP client & agent orchestration
│   ├── agent.py                # OpenAI Agents SDK integration (tool filtering, MCP setup)
│   └── prompt_builder.py       # System prompt assembly from markdown files
├── mcp_server/                 # MCP server implementations
│   ├── yahoo_finance_server.py # Yahoo Finance tools (9 tools)
│   ├── tradingview/            # TradingView technical analysis tools (7 tools)
│   │   └── server.py
│   └── handlers/               # Modular tool handler architecture
├── prompts/                    # LLM prompt templates (markdown)
│   ├── core.md                 # Identity, rules, security prompt
│   ├── default_site.md         # Default site-agnostic behavior
│   └── sites/                  # Site-specific agent behaviors
│       ├── finance.yahoo.com.md
│       ├── sec.gov.md
│       └── tradingview.com.md
├── data/                       # Application data storage
│   ├── preferred_links.json    # JSON storage for preferred data sources
│   └── site_map.json           # URL route templates for resolve_url()
├── tests/                      # Test suite
│   ├── test_planner.py         # Planner unit tests
│   ├── test_planner_integration.py  # Planner integration tests
│   ├── test_skills.py          # Skill configuration tests
│   ├── test_research_engine.py # Research engine tests (21 tests)
│   └── test_openai_api.py      # OpenAI API endpoint tests
├── mcp_server_config.json      # MCP server configuration (4 servers)
├── pyproject.toml              # Python dependencies and project metadata
├── Dockerfile                  # Container build definition
├── entrypoint.sh               # Docker container entry script
├── gunicorn.conf.py            # Gunicorn server configuration (1200s timeout)
├── manage.py                   # Django CLI helper
└── .env.example                # Environment variable template
```

### 2.2 Key Components

**`api/views.py`** – Browser extension API endpoints:
- `/health/` - Service health check (returns version)
- `/get_chat_response/` - Thinking mode chat
- `/get_chat_response_stream/` - Thinking mode with SSE streaming
- `/get_adv_response/` - Research mode with web search
- `/get_adv_response_stream/` - Research mode with SSE streaming
- `/input_webtext/` - Accept pre-scraped page content from extension
- `/api/auto_scrape/` - Auto-scrape current URL for context
- `/clear_messages/` - Clear session conversation context
- `/get_source_urls/` - Retrieve sources used in research queries
- `/api/get_memory_stats/` - Session context statistics
- `/api/get_available_models/` - List available models

**`api/openai_views.py`** – OpenAI-compatible API (for testers & automation):
- `GET /v1/models` - List available models
- `POST /v1/chat/completions` - Chat completions with mode selection
- Bearer token authentication via `FINGPT_API_KEY` env var
- See `DevSummaries/api/API_DOCUMENTATION.md` for complete API reference

**`planner/`** – Query Planning System (v0.13.3+):
- Heuristic-based planner that analyzes queries and selects the best skill
- Each skill constrains available tools and max agent turns
- Zero-cost, zero-latency (no LLM call for planning)
- 6 skills: summarize_page, stock_fundamentals, options_analysis, financial_statements, technical_analysis, web_research (fallback)

**`datascraper/unified_context_manager.py`** – Session Context Manager:
- Cache-backed session storage (Django cache framework)
- Conversation history tracking with metadata
- Fetched context storage (web_search, js_scraping sources)
- Multi-worker safe via FileBasedCache (or Redis for scale)
- See `DevSummaries/context_engineering/UNIFIED_CONTEXT_DOCUMENTATION.md`

**`datascraper/research_engine.py`** – Multi-Step Research Engine:
- Query decomposition into sub-questions (numerical, qualitative, analytical)
- Parallel sub-question execution
- Gap detection and follow-up research
- Streaming synthesis with phase-by-phase status updates
- See `DevSummaries/deep_research/STREAMING_RESEARCH_ENGINE.md`

**`mcp_client/agent.py`** – Agent Orchestration:
- OpenAI Agents SDK integration
- Tool filtering (only expose tools allowed by the selected skill)
- MCP tool injection from Yahoo Finance, TradingView, SEC EDGAR servers
- Custom instruction override from planner skills

**`datascraper/models_config.py`** – Model Configuration:
- OpenAI, Google (Gemini), DeepSeek, Anthropic, and custom provider support
- Per-model capability flags (streaming, MCP support, tracing)
- Provider base URLs and API key mappings

---

## 3. Frontend (`Main/frontend/`)

### 3.1 Directory Structure

```markdown
frontend/
├── dist/              # Compiled bundle – served by extension (DO NOT EDIT)
├── node_modules/      # Local dependencies (auto-generated)
└── src/               # Authoritative frontend source code
    ├── main.js        # Extension bootstrapper
    ├── manifest.json  # Web Extension manifest (permissions, icons)
    ├── assets/        # Static assets (icons, images)
    └── modules/       # Feature-specific modules
        ├── api.js              # Backend API integration
        ├── config.js           # Configuration management
        ├── handlers.js         # Event handlers (incl. research phase status)
        ├── helpers.js          # Utility functions
        ├── sourcesCache.js     # Source data caching
        ├── ui.js               # UI state management
        ├── components/         # UI Components
        │   ├── chat.js         # Chat interface
        │   ├── header.js       # Header component
        │   ├── link_manager.js # Preferred links management
        │   ├── popup.js        # Extension popup
        │   └── settings_window.js # Settings interface
        └── styles/             # CSS modules
            ├── chat.css        # Chat styling
            ├── header.css      # Header styling
            ├── popup.css       # Popup styling
            ├── theme.css       # Theme variables and base styles
            └── windows.css     # Window/modal styling
```

### 3.2 Build System

| File                | Purpose                                          |
|---------------------|--------------------------------------------------|
| `webpack.config.js` | Webpack configuration - bundles ES modules       |
| `babel.config.json` | Babel transpilation configuration                |
| `build-css.js`      | CSS build script                                 |
| `check-dist.js`     | Build artifact verification                      |
| `package.json`      | npm scripts and dependencies                     |

**Build Commands:**
```bash
bun run build:css      # Build CSS only
bun run build          # Build with Webpack
bun run check          # Verify build artifacts
bun run build:full     # Complete build pipeline
```

**Note:** Bun must be installed and available in PATH. If not in PATH, source your shell configuration:
```bash
source ~/.bashrc  # Linux/WSL
source ~/.zshrc   # macOS with zsh
```

---

## 4. MCP Servers

The backend integrates 4 MCP servers configured in `Main/backend/mcp_server_config.json`:

| Server | Location | Tools | Description |
|--------|----------|-------|-------------|
| **Yahoo Finance** | `mcp_server/yahoo_finance_server.py` | 9 | Stock data, financials, options, earnings, news, analysis |
| **TradingView** | `mcp_server/tradingview/server.py` | 7 | Technical analysis, gainers/losers, candlestick patterns |
| **SEC EDGAR** | External (`sec_edgar_mcp`) | — | SEC filing data (10-K, 10-Q, etc.) |
| **Filesystem** | External (`@modelcontextprotocol/server-filesystem`) | — | File system access |

See `DevSummaries/api/API_DOCUMENTATION.md` for the complete tool reference.

---

## 5. Additional Components

### 5.1 Docker Compose Quick Start

All backend services can be launched with a single command:

```bash
docker compose up
```

The compose file builds the backend image from `Main/backend/Dockerfile` using `uv` for dependency management. Provide configuration in `Main/backend/.env` (copy from `Main/backend/.env.example`).

### 5.2 Managing Dependencies with `uv`

If you prefer to work outside Docker:

```bash
cd Main/backend
uv sync --python 3.12 --frozen          # Install dependencies from uv.lock
uv run playwright install chromium      # One-time browser install for Playwright tooling
uv run python manage.py runserver
```

### 5.3 Documentation

| Location | Description |
|----------|-------------|
| `Docs/` | Sphinx / ReadTheDocs source files and build output |
| `Docs/plans/` | Implementation plans and architecture designs |
| `DevSummaries/api/` | API documentation for testers |
| `DevSummaries/context_engineering/` | Context management system documentation |
| `DevSummaries/deep_research/` | Research engine architecture documentation |

---

## 6. Tech Stack

### Backend Stack
- **Django 4.2** – Web framework
- **OpenAI Agents SDK** – Agent orchestration with tool use
- **Playwright** – Browser automation for scraping SPAs (requires Chromium)
- **BeautifulSoup4** – Lightweight web scraping fallback
- **Gunicorn** – Production WSGI server (1200s timeout for deep research)

### LLM Providers
- **OpenAI API** – GPT models (FinSearch-Light)
- **Google Gemini API** – Gemini models (FinSearch default)
- **DeepSeek API** – Alternative provider
- **Anthropic API** – Claude integration
- **Custom endpoints** – Buffet-Agent (HuggingFace)

### Frontend Stack
- **Bun** – Fast JavaScript runtime and package manager
- **Webpack 5** – Module bundler
- **Babel** – JavaScript transpiler
- **KaTeX** – Mathematical notation rendering
- **Marked** – Markdown parsing

### Context & Session Management
- **UnifiedContextManager** – Cache-backed session storage
  - Django cache framework (FileBasedCache or Redis)
  - Conversation history + fetched context tracking
  - Multi-worker safe via shared cache
  - 1-hour session TTL with touch-on-access

### Query Planning
- **Planner + Skills** – Heuristic query routing
  - Zero-cost skill selection (no LLM call)
  - Tool filtering per skill
  - Configurable max turns per skill

---

## 7. Development Workflow

### Backend Development
```bash
cd Main/backend

# Ensure dependencies are installed for Python 3.12
uv sync --python 3.12 --frozen

# Install Chromium for Playwright (required for web scraping)
uv run playwright install chromium

# Run tests
uv run python -m pytest tests/ -v

# Start development server
uv run python manage.py runserver
```

### Frontend Development
```bash
cd Main/frontend

# Install Bun if not already installed
curl -fsSL https://bun.sh/install | bash

# Install dependencies with Bun
bun install

# Complete build
bun run build:full
```

**Package Management with Bun:**
- `bun install` – Install dependencies (creates `bun.lock`)
- `bun install --frozen-lockfile` – Install with frozen lockfile (CI/CD)
- `bun add <package>` – Add new dependency
- `bun remove <package>` – Remove dependency
- `bun update` – Update dependencies

---

## 8. API Endpoints

### OpenAI-Compatible API (in `api/openai_views.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | Chat completions (thinking/research modes) |

See `DevSummaries/api/API_DOCUMENTATION.md` for the complete API reference with examples.

### Browser Extension Endpoints (in `api/views.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health/` | GET | Service health check |
| `/get_chat_response/` | GET | Thinking mode chat |
| `/get_chat_response_stream/` | GET | Thinking mode with SSE streaming |
| `/get_adv_response/` | GET | Research mode with web search |
| `/get_adv_response_stream/` | GET | Research mode with SSE streaming |
| `/input_webtext/` | POST | Accept pre-scraped page content |
| `/api/auto_scrape/` | POST | Auto-scrape current URL |
| `/clear_messages/` | GET | Clear session conversation context |
| `/get_source_urls/` | GET | Get sources used in queries |
| `/api/get_preferred_urls/` | GET | Get preferred URLs list |
| `/api/add_preferred_url/` | POST | Add preferred URL |
| `/api/sync_preferred_urls/` | POST | Bulk sync preferred URLs |
| `/get_agent_response/` | GET | Agent-mode response |
| `/api/get_memory_stats/` | GET | Session context statistics |
| `/api/get_available_models/` | GET | List available models with config |

**Common Query Parameters:**
- `question` – User query
- `models` – Comma-separated model names (default: resolved from config)
- `current_url` – Current webpage URL for context
- `session_id` – Custom session identifier
- `user_timezone` – IANA timezone string
- `user_time` – ISO 8601 current time
- `preferred_links` – JSON array of preferred source URLs (research mode)

---

## 9. Environment Configuration

### Required API Keys (in `Main/backend/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes* | OpenAI API key (for FinSearch-Light and web search) |
| `GOOGLE_API_KEY` | Yes* | Google API key (for FinSearch default model) |
| `ANTHROPIC_API_KEY` | No | Anthropic API key |
| `DEEPSEEK_API_KEY` | No | DeepSeek API key |
| `BUFFET_AGENT_API_KEY` | No | Buffet-Agent API key |
| `FINGPT_API_KEY` | No | API authentication key (if not set, auth disabled) |
| `SEC_EDGAR_USER_AGENT` | No | User agent for SEC EDGAR API compliance |

*At least one LLM provider API key is required.

See `Main/backend/.env.example` for the full configuration template.

---

*For the full API reference, see `DevSummaries/api/API_DOCUMENTATION.md`.*
*For detailed architecture documentation, see the `DevSummaries/` directory.*
