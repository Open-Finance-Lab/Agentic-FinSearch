"""Yahoo Finance MCP Server with async support and clean architecture."""

import asyncio
import logging
from typing import List

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types

from mcp_server.handlers.base import ToolHandler, ToolContext
from mcp_server.handlers.stock_info import GetStockInfoHandler
from mcp_server.handlers.stock_financials import GetStockFinancialsHandler
from mcp_server.handlers.stock_news import GetStockNewsHandler
from mcp_server.handlers.stock_history import GetStockHistoryHandler
from mcp_server.handlers.stock_analysis import GetStockAnalysisHandler
from mcp_server.handlers.earnings_info import GetEarningsInfoHandler
from mcp_server.handlers.options_chain import GetOptionsChainHandler
from mcp_server.handlers.options_summary import GetOptionsSummaryHandler
from mcp_server.handlers.holders import GetHoldersHandler
from mcp_server.validation import validate_ticker, ValidationError
from mcp_server.errors import ErrorType, ToolError
from mcp_server.executor import get_executor


import os

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("MCP_LOG_LEVEL", "INFO").upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("yahoo-finance-server")

# Initialize the server
server = Server("yahoo-finance")

# Tool registry mapping tool names to handlers
TOOL_HANDLERS: dict[str, ToolHandler] = {
    "get_stock_info": GetStockInfoHandler(),
    "get_stock_financials": GetStockFinancialsHandler(),
    "get_stock_news": GetStockNewsHandler(),
    "get_stock_history": GetStockHistoryHandler(),
    "get_stock_analysis": GetStockAnalysisHandler(),
    "get_earnings_info": GetEarningsInfoHandler(),
    "get_options_chain": GetOptionsChainHandler(),
    "get_options_summary": GetOptionsSummaryHandler(),
    "get_holders": GetHoldersHandler(),
}


@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """List available tools."""
    return [
        types.Tool(
            name="get_stock_info",
            description="Get general information about a stock or market index, including current price, market cap, PE ratio, and dividend yield.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', 'MSFT'). For market indices use the ^ prefix (e.g., '^GSPC' for S&P 500, '^DJI' for Dow Jones, '^IXIC' for NASDAQ, '^VIX' for VIX). For futures use =F suffix (e.g., 'GC=F'), for forex use =X suffix (e.g., 'EURUSD=X')."
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_stock_financials",
            description="Get financial statements for a stock, including Income Statement, Balance Sheet, and Cash Flow.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', 'MSFT'). Note: financial statements are only available for individual stocks, not indices."
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_stock_news",
            description="Get the latest news articles for a specific stock or market index.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', '^GSPC' for S&P 500)."
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_stock_history",
            description="Get historical price data for a stock or market index. Returns columns: Open, High, Low, Close (actual unadjusted trading price), Adj Close (adjusted for splits/dividends), Volume. Use 'start' and 'end' for a specific date range, or 'period' for a relative window. When 'start' is provided, 'period' is ignored. USE THIS TOOL for: monthly closing prices (interval='1mo'), daily prices over date ranges, computing price trends, correlations, comparisons between tickers. Call once per ticker. For comparing two stocks, call this tool twice (once per ticker) with the same date range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', '^GSPC' for S&P 500, '^DJI' for Dow Jones)."
                    },
                    "start": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (e.g., '2025-09-01'). When provided, fetches data from this date instead of using 'period'. Always prefer explicit start/end dates over 'period' for precision."
                    },
                    "end": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format, inclusive (e.g., '2025-10-01' includes Oct 1 data). Optional; defaults to today if omitted."
                    },
                    "period": {
                        "type": "string",
                        "description": "The relative period to fetch data for (e.g., '1mo', '1y', 'ytd', 'max'). Ignored when 'start' is provided.",
                        "default": "1mo"
                    },
                    "interval": {
                        "type": "string",
                        "description": "The data interval: '1d' for daily prices, '1wk' for weekly, '1mo' for monthly closing prices. Use '1mo' when asked about monthly data.",
                        "default": "1d"
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_stock_analysis",
            description="Get analyst recommendations, consensus price targets (mean, median, high, low), and recent upgrades/downgrades for a stock.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', 'MSFT'). Note: analyst data is primarily available for individual stocks."
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_earnings_info",
            description="Get earnings calendar, upcoming/past earnings dates, EPS and revenue estimates, EPS trends, and growth estimates for a stock.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', 'MSFT'). Use this to find next earnings date, expected EPS, revenue estimates, and growth projections."
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_options_chain",
            description="Get options chain data for a stock. Without an expiration date, returns available expiration dates. With an expiration date, returns the full calls and puts chain for that date.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', 'MSFT')."
                    },
                    "expiration": {
                        "type": "string",
                        "description": "Optional expiration date (e.g., '2026-03-21'). If omitted, returns the list of available expiration dates instead."
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_options_summary",
            description="Get aggregated options activity summary for a stock: total call/put volume, open interest, and put/call ratio across the nearest expiration dates. Use this for questions about overall options activity, flow, or volume. For detailed strike-by-strike data, use get_options_chain instead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AVGO', 'AAPL', 'TSLA')."
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_holders",
            description="Get ownership data for a stock: major holders breakdown, top institutional holders, top mutual fund holders, and recent insider transactions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "The ticker symbol (e.g., 'AAPL', 'MSFT'). Holder data is only available for individual stocks."
                    }
                },
                "required": ["ticker"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> List[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Handle tool execution requests with structured error handling."""
    ticker = None

    try:
        # Validate arguments
        if not arguments:
            raise ValidationError("Missing arguments")

        # Validate ticker for all operations
        ticker = validate_ticker(arguments.get("ticker"))

        # Get handler for this tool
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            raise ValidationError(f"Unknown tool: {name}")

        # Create context and execute
        ctx = ToolContext(
            ticker=ticker,
            arguments=arguments,
            executor=get_executor()
        )

        logger.info(f"Executing {name} for {ticker}")
        return await handler.execute(ctx)

    except ValidationError as e:
        logger.warning(f"Validation error in {name}: {e}")
        return [ToolError(
            error_type=ErrorType.VALIDATION,
            message=str(e),
            ticker=ticker
        ).to_content()]

    except KeyError as e:
        logger.warning(f"Key error in {name} for {ticker}: {e}")
        return [ToolError(
            error_type=ErrorType.NOT_FOUND,
            message=f"Ticker {ticker} not found or has no data for requested field",
            ticker=ticker,
            details={"missing_key": str(e)}
        ).to_content()]

    except Exception as e:
        logger.error(
            f"Unexpected error in {name}",
            exc_info=True,
            extra={"ticker": ticker, "tool": name}
        )
        return [ToolError(
            error_type=ErrorType.INTERNAL,
            message="An internal error occurred. Please try again later.",
            ticker=ticker
        ).to_content()]


async def main():
    """Run the MCP server."""
    logger.info("Starting Yahoo Finance MCP server")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="yahoo-finance",
                server_version="0.3.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
