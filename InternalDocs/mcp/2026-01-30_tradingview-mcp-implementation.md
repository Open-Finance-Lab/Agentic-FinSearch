# TradingView MCP Integration

**Date:** 2026-01-30 (Updated: 2026-02-02)

---

## Overview

Complete TradingView MCP integration providing 7 technical analysis tools for cryptocurrency and stock market data using **tradingview-ta** and **tradingview-screener** Python libraries. Clean modular architecture in `mcp_server/tradingview/`.

### Tools Implemented (All Working)

1. **get_coin_analysis** - Complete technical analysis with recommendation summaries
2. **get_top_gainers** - Top performing assets by exchange/timeframe
3. **get_top_losers** - Worst performing assets
4. **get_bollinger_scan** - Consolidation pattern detection (BBW < 0.05)
5. **get_rating_filter** - Filter by computed Bollinger Band position (-3 to +3)
6. **get_consecutive_candles** - Momentum pattern detection (placeholder)
7. **get_advanced_candle_pattern** - Multi-timeframe analysis (placeholder)

---

## Architecture

### File Structure

```
mcp_server/tradingview/
├── __init__.py
├── server.py                    # MCP server entry point (async, stdio transport)
├── scanner_api.py               # Core API layer using tradingview-ta/screener
├── validation.py                # Input validation layer
└── handlers/
    ├── __init__.py
    ├── base.py                  # Shared base handler with caching + scanner_api bridge
    ├── coin_analysis.py         # Primary tool
    ├── top_gainers.py
    ├── top_losers.py
    ├── bollinger_scan.py
    ├── rating_filter.py
    ├── consecutive_candles.py   # (TODO: implement scanner logic)
    └── advanced_candle_pattern.py  # (TODO: implement scanner logic)
```

### Key Components

**Scanner API Layer** (`scanner_api.py`) - :
- **Library Integration**: Uses `tradingview-ta` for individual symbol analysis, `tradingview-screener` for exchange-wide scans
- **get_coin_analysis()**: Calls `get_multiple_analysis()` with screener market + interval mapping
- **get_top_movers()**: Uses `Query().select().where().order_by()` fluent API with timeframe-specific column suffixes
- **get_bollinger_scan()**: Fetches BB components via screener, computes BBW = (upper - lower) / SMA20 in Python
- **get_rating_filter()**: Fetches BB data, computes -3 to +3 rating based on price position within bands
- **Field Mapping**: `FIELD_MAP` translates internal keys (RSI, MACD_signal) to TradingView column names (RSI, MACD.macd)
- **Bonus Data**: Adds `recommendation`, `oscillators_recommendation`, `moving_averages_recommendation` from tradingview-ta summary

**Validation Layer** (`validation.py`):
- Whitelisted exchanges: BINANCE, KUCOIN, BYBIT, BITGET, OKX, COINBASE, GATEIO, HUOBI, BITFINEX, KRAKEN, MEXC, GEMINI (crypto) | NASDAQ, NYSE, BIST, LSE, HKEX, TSE (stock)
- Timeframes: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1D, 1W, 1M
- Bollinger rating bounds: -3 to +3 (strictly enforced)
- Symbol format validation: 3-10 uppercase letters
- Limit validation: 1-100 range

**Base Handler** (`handlers/base.py`):
- Caching: TimedCache with 10-minute TTL (configurable)
- Field filtering: Extracts only 25 relevant technical fields
- Type enforcement: Float/int validation for numerical fields
- Large response warnings: Logs if >50 items returned
- Bridge: `_call_tradingview()` method calls `scanner_api.*()` functions via `asyncio.to_thread()`

**Server** (`server.py`):
- Registers all 7 tools with complete JSON schemas
- Structured error handling (validation_error, exchange_error, symbol_error, rate_limit, internal_error)
- Async stdio transport for MCP protocol

---

## Implementation Details

### Library Integration (tradingview-ta & tradingview-screener)

Uses two Python libraries that encapsulate the TradingView API:

1. **tradingview-ta** (`get_multiple_analysis`):
   - For individual symbol technical analysis
   - Returns structured `Analysis` objects with `.indicators`, `.summary`, `.oscillators`, `.moving_averages`
   - Handles screener market mapping (crypto/america/turkey)
   - Automatic interval conversion (1D → INTERVAL_1_DAY constant)

2. **tradingview-screener** (`Query` fluent API):
   - For exchange-wide screening queries
   - Pandas DataFrame output for easy filtering/sorting
   - Column suffix handling for timeframes (e.g., `close|60` for 1h close)
   - Chainable filters: `.where(col('exchange') == 'BINANCE', col('change').not_empty())`

**Key Mappings:**

```python
# Timeframe → tradingview-ta Interval
INTERVAL_MAP = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "1D": Interval.INTERVAL_1_DAY,
    # ...
}

# Timeframe → tradingview-screener column suffix
SCREENER_SUFFIX_MAP = {
    "1m": "|1", "5m": "|5", "1h": "|60", "1D": "", "1W": "|1W"
}

# Exchange → Screener market
get_market_from_exchange("BINANCE") → "crypto"
get_market_from_exchange("NASDAQ") → "america"
```

---

## Configuration

### Dependencies (pyproject.toml)

```toml
dependencies = [
    # ... other deps ...
    "tradingview-ta>=3.3.0",        # Individual symbol analysis
    "tradingview-screener>=3.0.0",  # Exchange-wide screening
]
```

### MCP Server Config

File: `mcp_server_config.json`

```json
{
  "tradingview": {
    "command": "python",
    "args": ["-m", "mcp_server.tradingview.server"],
    "disabled": false,
    "env": {
      "MCP_LOG_LEVEL": "INFO"
    }
  }
}
```

**Enable/Disable:** Set `"disabled": true/false` - no code changes needed

### Agent Integration

File: `mcp_client/agent.py`

Added TradingView section to DEFAULT_PROMPT with:
- Tool descriptions and use cases
- Bollinger rating scale (-3 to +3)
- Tool selection logic (technical indicators → TradingView)
- Supported exchanges and timeframes

### Site Map

File: `data/site_map.json`

Added routes:
- `tradingview_technicals` - Technical analysis URL pattern
- `tradingview_gainers` - Top gainers URL pattern
- `tradingview_losers` - Top losers URL pattern

### Preferred Links

File: `data/preferred_links.json`

Added: `https://www.tradingview.com`

---

## File Inventory

### New Files (13)

**Core Implementation:**
1. `mcp_server/tradingview/__init__.py`
2. `mcp_server/tradingview/server.py` - MCP server with stdio transport
3. `mcp_server/tradingview/scanner_api.py` - tradingview-ta/screener integration layer
4. `mcp_server/tradingview/validation.py` - Input validation
5. `mcp_server/tradingview/handlers/__init__.py`
6. `mcp_server/tradingview/handlers/base.py` - Shared handler with caching
7. `mcp_server/tradingview/handlers/coin_analysis.py`
8. `mcp_server/tradingview/handlers/top_gainers.py`
9. `mcp_server/tradingview/handlers/top_losers.py`
10. `mcp_server/tradingview/handlers/bollinger_scan.py`
11. `mcp_server/tradingview/handlers/rating_filter.py`
12. `mcp_server/tradingview/handlers/consecutive_candles.py` (placeholder)
13. `mcp_server/tradingview/handlers/advanced_candle_pattern.py` (placeholder)

### Modified Files (6)

1. `pyproject.toml` - Added tradingview-ta>=3.3.0, tradingview-screener>=3.0.0
2. `mcp_server_config.json` - Added TradingView server configuration
3. `mcp_client/agent.py` - Updated DEFAULT_PROMPT with TradingView instructions
4. `mcp_server/errors.py` - Added EXCHANGE_ERROR and SYMBOL_ERROR types
5. `data/site_map.json` - Added TradingView routes
6. `data/preferred_links.json` - Added TradingView URL

---

## Key Implementation Details

### Caching

- **Default TTL:** 10 minutes (600 seconds)
- **Cache Key:** MD5 hash of all parameters

### Numerical Accuracy

**Field Filtering:** Only 25 relevant technical fields extracted
- Price data: symbol, close, open, high, low, volume
- Bollinger Bands: BB_upper, BB_middle, BB_lower, BB_width, BB_rating
- Oscillators: RSI, MACD, Stochastic_K, ADX
- Moving Averages: SMA_20/50/200, EMA_20/50/200

**Type Enforcement:**
- Float fields: RSI, MACD, prices, moving averages
- Integer fields: BB_rating, volume_24h

**Range Validation:**
- Bollinger rating: -3 (very oversold) to +3 (very overbought)
- Prevents invalid ratings that could mislead trading decisions

### Error Handling

**Structured Errors:** All errors return JSON with error type, message, and details

**Error Types:**
- `validation_error` - Invalid input parameters (includes valid options)
- `exchange_error` - Exchange-specific errors (suggests alternatives)
- `symbol_error` - Invalid symbol for exchange
- `rate_limit` - Rate limit exceeded (includes retry_after)
- `internal_error` - Server error (logged with full traceback)

---

## Quick Reference

### Usage Examples

**Import Validation:**
```python
from mcp_server.tradingview.validation import validate_exchange
exchange = validate_exchange('binance', market_type='crypto')  # Returns: BINANCE
```

**Import Handler:**
```python
from mcp_server.tradingview.handlers.coin_analysis import GetCoinAnalysisHandler
handler = GetCoinAnalysisHandler()
```

**Access Tool Registry:**
```python
from mcp_server.tradingview.server import TOOL_HANDLERS
tools = list(TOOL_HANDLERS.keys())
# ['get_coin_analysis', 'get_top_gainers', 'get_top_losers', ...]
```

### Bollinger Rating Scale

- `-3`: Very oversold (strong buy signal)
- `-2`: Oversold
- `-1`: Slightly oversold
- `0`: Neutral
- `+1`: Slightly overbought
- `+2`: Overbought
- `+3`: Very overbought (strong sell signal)

---

### Pending Implementation

**Tools with placeholder handlers:**
6. `get_consecutive_candles` - Requires historical OHLC data fetching logic
7. `get_advanced_candle_pattern` - Requires multi-timeframe candlestick pattern recognition

**Next Steps:**
- Implement `get_consecutive_candles` using tradingview-screener's historical data API
- Implement `get_advanced_candle_pattern` with timeframe aggregation logic
- Add rate limiting to prevent TradingView API throttling

---

## For Future Dev

### Pattern to Follow

New MCP integrations should use this structure:
```
mcp_server/<name>/
├── __init__.py
├── server.py
├── validation.py
└── handlers/
    ├── __init__.py
    ├── base.py
    └── <tool_handlers>.py
```

### Agent Integration

- Add tool descriptions to `mcp_client/agent.py` DEFAULT_PROMPT
- Update tool selection logic for automatic tool routing
- Add routes to `data/site_map.json`
- Add URL to `data/preferred_links.json`