"""Handler for get_stock_info tool."""

import json
import logging
from typing import List

import mcp.types as types
import yfinance as yf

from mcp_server.handlers.base import ToolHandler, ToolContext
from mcp_server.executor import run_in_executor
from mcp_server.cache import TimedCache


logger = logging.getLogger(__name__)

# Cache for Ticker objects (30s TTL — short to ensure near-real-time data)
_ticker_cache = TimedCache(ttl_seconds=30)


async def get_ticker(symbol: str) -> yf.Ticker:
    """Get Ticker object with caching.

    Args:
        symbol: Stock ticker symbol

    Returns:
        yfinance Ticker object
    """
    cached = _ticker_cache.get(symbol)
    if cached:
        logger.debug(f"Cache hit for {symbol}")
        return cached

    logger.debug(f"Cache miss for {symbol}")
    ticker = await run_in_executor(yf.Ticker, symbol)
    _ticker_cache.set(symbol, ticker)
    return ticker


class GetStockInfoHandler(ToolHandler):
    """Handler for get_stock_info tool."""

    # Keys relevant for stocks
    STOCK_KEYS = [
        'longName', 'symbol', 'currentPrice', 'marketCap', 'trailingPE',
        'forwardPE', 'dividendYield', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow',
        'averageVolume', 'sector', 'industry', 'longBusinessSummary',
        # Real-time market data (essential for daily change, range, turnover)
        'regularMarketDayHigh', 'regularMarketDayLow', 'regularMarketOpen',
        'regularMarketVolume', 'regularMarketPreviousClose',
        'regularMarketChangePercent', 'sharesOutstanding',
        # Key statistics (beta, EPS, earnings date, dividend details)
        'beta', 'trailingEps', 'forwardEps',
        'earningsDate', 'mostRecentQuarter',
        'forwardAnnualDividendRate', 'forwardAnnualDividendYield',
        'exDividendDate', 'payoutRatio',
    ]

    # Keys relevant for indices (^GSPC, ^DJI, etc.) and broader instruments
    INDEX_KEYS = [
        'shortName', 'symbol', 'regularMarketPrice', 'regularMarketDayHigh',
        'regularMarketDayLow', 'regularMarketOpen', 'regularMarketVolume',
        'regularMarketPreviousClose', 'regularMarketChange',
        'regularMarketChangePercent', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow',
        'fiftyDayAverage', 'twoHundredDayAverage'
    ]

    # ETF proxies for major indices when direct index data is unavailable
    INDEX_ETF_PROXIES = {
        '^GSPC': 'SPY',   # S&P 500
        '^DJI': 'DIA',    # Dow Jones Industrial Average
        '^IXIC': 'QQQ',   # NASDAQ Composite
        '^RUT': 'IWM',    # Russell 2000
    }

    async def execute(self, ctx: ToolContext) -> List[types.TextContent]:
        """Execute get_stock_info tool.

        For indices, attempts direct data first, then falls back to ETF proxies
        if the index data is incomplete (common due to Yahoo Finance licensing).

        Args:
            ctx: Tool execution context

        Returns:
            List containing stock information as JSON
        """
        stock = await get_ticker(ctx.ticker)
        info = await run_in_executor(lambda: stock.info)

        # Use index-specific keys for tickers with ^ or . prefix
        is_index = ctx.ticker.startswith('^') or ctx.ticker.startswith('.')
        keys = self.INDEX_KEYS if is_index else self.STOCK_KEYS
        filtered_info = {k: info.get(k) for k in keys if k in info}

        # --- ETF proxy fallback for indices with incomplete data ---
        if is_index and (not filtered_info or 'regularMarketPrice' not in filtered_info):
            etf_proxy = self.INDEX_ETF_PROXIES.get(ctx.ticker.upper())
            if etf_proxy:
                logger.info(f"Index {ctx.ticker} returned incomplete data, trying ETF proxy {etf_proxy}")
                try:
                    etf_stock = await get_ticker(etf_proxy)
                    etf_info = await run_in_executor(lambda: etf_stock.info)
                    etf_filtered = {k: etf_info.get(k) for k in self.INDEX_KEYS if k in etf_info}
                    if etf_filtered and 'regularMarketPrice' in etf_filtered:
                        etf_filtered['_note'] = (
                            f"Data sourced from ETF proxy {etf_proxy} because direct index data "
                            f"for {ctx.ticker} was unavailable. ETF prices closely track but are "
                            f"not identical to the underlying index."
                        )
                        etf_filtered['_proxy_etf'] = etf_proxy
                        etf_filtered['symbol'] = ctx.ticker
                        return [types.TextContent(
                            type="text",
                            text=json.dumps(etf_filtered, indent=2)
                        )]
                except Exception as e:
                    logger.warning(f"ETF proxy {etf_proxy} also failed: {e}")

        if not filtered_info:
            return [types.TextContent(
                type="text",
                text=f"No information found for ticker {ctx.ticker}"
            )]

        return [types.TextContent(
            type="text",
            text=json.dumps(filtered_info, indent=2)
        )]
