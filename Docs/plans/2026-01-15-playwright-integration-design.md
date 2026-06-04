# Playwright Integration Design for THINKING Mode

**Date**: 2026-01-15
**Status**: Approved
**Scope**: THINKING mode only (Research mode unchanged)

## Overview

Integrate Playwright browser automation into the agent's backend to enable dynamic navigation on Yahoo Finance for non-numerical content (news articles, analyst opinions, sentiment analysis).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     THINKING MODE                           │
├─────────────────────────────────────────────────────────────┤
│  User Query                                                 │
│      ↓                                                      │
│  System Prompt (routing logic)                              │
│      ↓                                                      │
│  ┌──────────────────┐    ┌─────────────────────────────┐   │
│  │ Yahoo Finance MCP│    │ Playwright Tools            │   │
│  │ (numerical data) │    │ (news, dynamic content)     │   │
│  │                  │    │                             │   │
│  │ • get_stock_price│    │ • navigate_to_url           │   │
│  │ • get_financials │    │ • click_element             │   │
│  │ • get_metrics    │    │ • extract_page_content      │   │
│  └──────────────────┘    └─────────────────────────────┘   │
│           ↓                          ↓                      │
│      Structured JSON           Extracted Text               │
│           ↓                          ↓                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │           Unified Context Manager                    │   │
│  │         (conversation + fetched context)             │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**Key principle**: Tools work in parallel when needed. If a query only requires one type of data, only that pipeline runs.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Tool routing | Prompt engineering | Simple, maintainable, no extra code |
| Integration | Direct Python | Not using Playwright MCP (that's for Claude Code) |
| Sessions | Ephemeral per-request | Stateless, reliable, no cleanup needed |
| Capabilities | Read-only navigation | Safe, sufficient for news/content extraction |
| Tool exposure | Multiple granular tools | Flexibility for multi-step navigation |

## Playwright Module

**New file**: `Main/backend/datascraper/playwright_tools.py`

### PlaywrightBrowser Class

```python
class PlaywrightBrowser:
    """Ephemeral browser manager - launches per-request, closes after."""

    def __init__(self):
        self.browser = None
        self.page = None

    def __enter__(self):
        # Launch headless Chromium
        # Return self for context manager usage

    def __exit__(self, ...):
        # Close browser, cleanup
```

### Function Tools

| Tool | Purpose | Returns |
|------|---------|---------|
| `navigate_to_url(url: str)` | Opens URL in browser, waits for load | Page title + status |
| `click_element(selector: str)` | Clicks element matching CSS/text selector | New page content summary |
| `extract_page_content()` | Extracts main text from current page | Cleaned text content |

### Error Handling

- Timeout after 30 seconds
- Graceful fallback if element not found
- Always close browser in finally block

## System Prompt Routing Logic

Add to THINKING mode system prompt:

```markdown
## Tool Selection Guidelines

You have access to two types of tools for Yahoo Finance data:

### Yahoo Finance MCP Tools (for numerical data)
Use these for:
- Stock prices, quotes, historical data
- Financial metrics (P/E ratio, market cap, EPS, revenue)
- Technical indicators
- Earnings data, dividends

### Playwright Browser Tools (for content/news)
Use these for:
- News articles and headlines
- Analyst opinions and commentary
- Sentiment analysis of written content
- Any dynamic content requiring clicking into pages

### Decision Logic
1. **Numerical query** → Yahoo Finance MCP first
   - If MCP fails or returns no data → fallback to Playwright
   - Example: "What's AAPL's P/E ratio?"

2. **Content query** → Playwright only
   Example: "What are the latest news about Tesla?"

3. **Hybrid query** → Use both in parallel, then synthesize
   Example: "Is NVDA overvalued given recent news sentiment?"

### Fallback Rule
For numerical data, always try Yahoo Finance MCP first. If it fails,
errors, or returns incomplete data, use Playwright to navigate to
the relevant Yahoo Finance page and scrape the numbers directly.
```

## Docker Configuration

**Dockerfile additions:**

```dockerfile
# Install system dependencies for Playwright Chromium
RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# After pip/uv install dependencies
RUN playwright install chromium --with-deps
```

**Key considerations:**
- `--with-deps` installs all Chromium system dependencies
- Headless mode required (no display in container)
- `--no-sandbox` flag needed for containerized Chromium
- Only install Chromium (not Firefox/WebKit) to keep image size reasonable

## Implementation Checklist

| # | File | Action | Description |
|---|------|--------|-------------|
| 1 | `pyproject.toml` | Modify | Add `playwright>=1.40.0,<2` |
| 2 | `datascraper/playwright_tools.py` | Create | `PlaywrightBrowser` class + 3 function tools |
| 3 | `datascraper/datascraper.py` | Modify | Import and register Playwright tools |
| 4 | `Dockerfile` | Modify | Add Playwright system deps + browser install |
| 5 | System prompt config | Modify | Add tool routing logic with fallback |

## Out of Scope

The following are explicitly NOT modified:
- Research mode
- `web_search` tool
- `openai_search.py`
- Any existing MCP server configurations

## Context Integration

Playwright-extracted content stored in UnifiedContextManager:

```python
context_mgr.add_fetched_context(
    session_id=session_id,
    source_type="playwright_scraping",
    content=extracted_text,
    url=current_url
)
```
