# Agentic FinSearch API Documentation

This API provides an OpenAI-compatible interface to the Agentic FinSearch agent. Internal testers and automated workflows can interact with the agent using standard OpenAI client libraries.

**Version:** 0.16.0

## Base Configuration

- **Base URL**: `http://localhost:8000/v1` (Docker) or your deployed server URL
- **Authentication**: Bearer token via `Authorization: Bearer <key>`. The key is set via the `FINGPT_API_KEY` environment variable. If `FINGPT_API_KEY` is not set, authentication is disabled (development mode).
- **Rate Limiting**: Configurable via `API_RATE_LIMIT` in Django settings (default: `60/m`)
- **Compatibility**: Follows the [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat) specification with Agentic FinSearch extensions.

---

## Quick Start

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-fingpt-api-key"  # or "sk-dummy" if FINGPT_API_KEY is not set
)

# Ask about a stock
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "What is Apple's current P/E ratio?"}],
    extra_body={"mode": "thinking"}
)
print(response.choices[0].message.content)
```

---

## Endpoints

### 1. List Models

Retrieves the list of available models.

**Endpoint**: `GET /v1/models`

**Response**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "FinSearch",
      "object": "model",
      "created": 1740000000,
      "owned_by": "google"
    },
    {
      "id": "FinSearch-Light",
      "object": "model",
      "created": 1740000000,
      "owned_by": "openai"
    },
    {
      "id": "Buffet-Agent",
      "object": "model",
      "created": 1740000000,
      "owned_by": "buffet"
    }
  ]
}
```

#### Current Models

| Model ID | Provider | Underlying Model | Description |
|----------|----------|-------------------|-------------|
| `FinSearch` | Google | `gemini-3-flash-preview` | Default model. High context window (1M tokens). |
| `FinSearch-Light` | OpenAI | `gpt-5.1-chat-latest` | Fast and efficient. Supports streaming. |
| `Buffet-Agent` | Custom | Buffet-Agent | Custom fine-tuned agent. |

### 2. Chat Completions

Generates a response from the financial agent. Supports **Thinking** and **Research** modes.

**Endpoint**: `POST /v1/chat/completions`

#### Request Body

**Standard OpenAI Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model` | string | Yes | Model ID (e.g., `"FinSearch"`, `"FinSearch-Light"`). Use `GET /v1/models` to list options. |
| `messages` | array | Yes | Conversation history. Each message has `role` (`"system"`, `"user"`, `"assistant"`) and `content`. |
| `user` | string | No | Unique user identifier. If provided, enables session continuity across requests with the same user ID. |

**FinSearch Extensions** (pass in the root body, or via `extra_body` in the OpenAI Python client):

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `mode` | string | **Yes** | `"thinking"` or `"research"`. See [Modes](#modes) below. |
| `url` | string | No | A URL to scrape and add as page context before processing the query. |
| `user_timezone` | string | No | IANA timezone string (e.g., `"America/New_York"`). Used for market hours awareness. |
| `user_time` | string | No | ISO 8601 timestamp of the user's current time (e.g., `"2026-02-24T10:30:00"`). |
| `preferred_links` | array | No | List of full URLs to prioritize in research mode (e.g., `["https://reuters.com", "https://bloomberg.com"]`). |
| `search_domains` | array | No | List of domain names to scope research to (e.g., `["reuters.com", "sec.gov"]`). These are normalized to URLs and merged with `preferred_links`. |

#### Modes

- **`mode="thinking"`**: The agent uses its internal MCP tools (Yahoo Finance data, TradingView analysis, SEC EDGAR filings) to answer the query. Best for structured financial data retrieval: stock prices, financials, options, technical analysis.

- **`mode="research"`**: The agent performs web search to find and synthesize information from multiple sources. Best for qualitative questions, news, market analysis, and topics requiring current information from the open web.

#### Streaming

The `/v1/chat/completions` endpoint currently supports **synchronous responses only**. The `stream` parameter is not yet implemented on this endpoint.

For streaming responses (SSE), use the browser extension endpoints (`/get_chat_response_stream/` and `/get_adv_response_stream/`). Streaming support for the OpenAI-compatible API is planned for a future release.

#### Response

Standard OpenAI chat completion format with a FinSearch-specific `sources` extension:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1740000000,
  "model": "FinSearch",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Apple's current P/E ratio is 28.5..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 512,
    "completion_tokens": 128,
    "total_tokens": 640
  },
  "sources": [
    {"url": "https://finance.yahoo.com/quote/AAPL", "title": "AAPL Stock Quote"}
  ]
}
```

The `sources` array contains the information sources used to generate the response. In research mode, these are web search results. In thinking mode, these are the MCP tools that were called.

#### Error Responses

Errors follow the OpenAI error format:

```json
{
  "error": {
    "message": "mode is required. Valid values: 'thinking', 'research'",
    "type": "invalid_request_error"
  }
}
```

| Status Code | Type | Common Causes |
|-------------|------|---------------|
| 400 | `invalid_request_error` | Missing `messages`, missing `mode`, invalid JSON |
| 401 | `authentication_error` | Missing or invalid Bearer token |
| 404 | `invalid_request_error` | Model not found |
| 405 | Method not allowed | Using GET instead of POST |
| 429 | Rate limited | Too many requests |
| 500 | `server_error` | Internal processing error (check server logs) |

---

## Statelessness

The API is **stateless** by default. Each request:

1. Creates a fresh session context
2. Replays the `messages` array to reconstruct conversation history
3. Processes the last user message as the current query
4. Returns the response and discards the session

This means you **must** send the full conversation history in every request. This matches the OpenAI API behavior.

**Session continuity**: If you provide the same `user` ID across requests, the API uses a stable session identifier. This does not persist conversation history between requests (context is still reset), but it may be used for future features like user-level preferences.

---

## Usage Examples

### Python (openai library)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-fingpt-api-key"
)

# --- Thinking Mode: Financial Data Queries ---

# Stock fundamentals
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "What is Tesla's market cap and P/E ratio?"}],
    extra_body={"mode": "thinking"}
)
print(response.choices[0].message.content)

# Options analysis
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "What's the put/call ratio for SPY?"}],
    extra_body={"mode": "thinking"}
)

# Financial statements
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "Show me NVIDIA's revenue and EPS for the last 4 quarters"}],
    extra_body={"mode": "thinking"}
)

# Technical analysis
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "What's the RSI and MACD for BTC on Binance?"}],
    extra_body={"mode": "thinking"}
)

# Page context: scrape and analyze a URL
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "Summarize the key financial metrics from this page."}],
    extra_body={
        "mode": "thinking",
        "url": "https://finance.yahoo.com/quote/AAPL"
    }
)

# --- Research Mode: Web Search Queries ---

# Open research
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "What caused the recent crypto market volatility?"}],
    extra_body={"mode": "research"}
)
# Access sources
print(response.sources)  # List of URLs used

# Scoped research (limit to specific domains)
response = client.chat.completions.create(
    model="FinSearch",
    messages=[{"role": "user", "content": "Latest news on NVIDIA earnings"}],
    extra_body={
        "mode": "research",
        "search_domains": ["reuters.com", "bloomberg.com"]
    }
)

# --- Multi-turn Conversation ---

messages = [
    {"role": "user", "content": "What is Apple's current stock price?"},
    {"role": "assistant", "content": "Apple (AAPL) is currently trading at $185.50..."},
    {"role": "user", "content": "How does that compare to its 52-week high?"}
]

response = client.chat.completions.create(
    model="FinSearch",
    messages=messages,
    extra_body={"mode": "thinking"}
)
```

### cURL

**Thinking Mode:**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-fingpt-api-key" \
  -d '{
    "model": "FinSearch",
    "messages": [{"role": "user", "content": "What is AAPL stock price?"}],
    "mode": "thinking"
  }'
```

**Research Mode with Domain Scoping:**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-fingpt-api-key" \
  -d '{
    "model": "FinSearch",
    "messages": [{"role": "user", "content": "Latest news on NVIDIA?"}],
    "mode": "research",
    "search_domains": ["reuters.com", "sec.gov"]
  }'
```

**With URL Context:**
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-fingpt-api-key" \
  -d '{
    "model": "FinSearch",
    "messages": [{"role": "user", "content": "Summarize this article."}],
    "mode": "thinking",
    "url": "https://finance.yahoo.com/quote/AAPL"
  }'
```

---

## How Queries Are Processed

### Planner + Skills Architecture

When a query arrives in thinking mode, the backend uses a **Planner** to analyze the query and select the best **Skill** for handling it. Each skill constrains which MCP tools the agent can use and how many iterations it gets, resulting in faster and more focused responses.

| Skill | When It Activates | Tools Available | Max Turns |
|-------|-------------------|-----------------|-----------|
| **Summarize Page** | Pre-scraped page content + keywords like "summarize", "explain", "key points" | None (LLM-only) | 1 |
| **Stock Fundamentals** | Queries about stock price, market cap, P/E ratio, volume, beta | `get_stock_info`, `get_stock_history`, `calculate` | 5 |
| **Options Analysis** | Queries about options volume, put/call ratio, open interest, IV | `get_options_summary`, `get_options_chain`, `calculate` | 5 |
| **Financial Statements** | Queries about revenue, earnings, EPS, balance sheet, income statement | `get_stock_financials`, `get_earnings_info`, `calculate` | 5 |
| **Technical Analysis** | Queries about RSI, MACD, Bollinger Bands, moving averages, candlestick patterns | TradingView tools + `calculate` | 5 |
| **Web Research** (Fallback) | Any query that doesn't match a specific skill | All tools | 10 |

The planner is heuristic-based (no LLM call), so skill selection adds zero latency and zero cost.

---

## Available MCP Tools

These tools are available to the agent in thinking mode. The planner selects which subset to use based on the query.

### Yahoo Finance Tools

| Tool | Description |
|------|-------------|
| `get_stock_info` | Price, market cap, P/E, dividend yield, beta, 52-week range, shares outstanding |
| `get_stock_financials` | Income statement, balance sheet, cash flow statements |
| `get_stock_news` | Latest news articles for a stock or index |
| `get_stock_history` | Historical OHLCV price data (configurable period and interval) |
| `get_stock_analysis` | Analyst recommendations, consensus price targets, upgrades/downgrades |
| `get_earnings_info` | Earnings calendar, upcoming/past dates, EPS estimates, revenue growth projections |
| `get_options_chain` | Options chain (calls/puts) for a given expiration, or list available expirations |
| `get_options_summary` | Aggregated options data: total call/put volume, open interest, put/call ratio |
| `get_holders` | Major holders breakdown, top institutional holders, insider transactions |

### TradingView Tools

| Tool | Description |
|------|-------------|
| `get_coin_analysis` | Technical analysis indicators: RSI, MACD, Bollinger Bands, ADX, Stochastic, Moving Averages |
| `get_top_gainers` | Top performing assets by exchange and timeframe |
| `get_top_losers` | Worst performing assets by exchange and timeframe |
| `get_bollinger_scan` | Screen for overbought/oversold assets using Bollinger Bands |
| `get_rating_filter` | Filter assets by technical rating criteria |
| `get_consecutive_candles` | Find consecutive bullish/bearish candle patterns |
| `get_advanced_candle_pattern` | Detect advanced candlestick patterns (doji, hammer, engulfing, etc.) |

### Other Tools

| Tool | Description |
|------|-------------|
| `scrape_url` | Fetch and extract content from a URL (handles cookie consent, SPAs) |
| `resolve_url` | Build a URL from a route template (e.g., Yahoo Finance quote page for a ticker) |
| `calculate` | Perform mathematical calculations |

### SEC EDGAR

The agent also has access to SEC EDGAR filing data via the `sec-edgar` MCP server, enabling queries about company filings, 10-K, 10-Q, and other SEC documents.

---

## Health Check

**Endpoint**: `GET /health/`

Returns service status and version. No authentication required.

```json
{
  "status": "healthy",
  "version": "0.16.0"
}
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FINGPT_API_KEY` | No | API key for Bearer token authentication. If not set, auth is disabled. |
| `OPENAI_API_KEY` | Yes* | OpenAI API key (required for FinSearch-Light model and web search) |
| `GOOGLE_API_KEY` | Yes* | Google API key (required for FinSearch model) |
| `ANTHROPIC_API_KEY` | No | Anthropic API key (optional provider) |
| `DEEPSEEK_API_KEY` | No | DeepSeek API key (optional provider) |
| `BUFFET_AGENT_API_KEY` | No | Buffet-Agent API key (optional provider) |
| `SEC_EDGAR_USER_AGENT` | No | User agent string for SEC EDGAR API compliance |

*At least one LLM provider API key is required for the server to function.

---

## Running the Server

### Docker (Recommended)
```bash
# Copy environment template and add API keys
cp Main/backend/.env.example Main/backend/.env

# Start the server
docker compose up
```

The API will be available at `http://localhost:8000/v1`.

### Manual
```bash
cd Main/backend
uv sync --python 3.12 --frozen
uv run playwright install chromium
uv run python manage.py runserver
```
