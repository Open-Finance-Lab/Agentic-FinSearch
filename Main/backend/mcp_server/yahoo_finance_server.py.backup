import asyncio
import json
import logging
import sys
from typing import Any, List

import yfinance as yf
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yahoo-finance-server")

# Initialize the server
server = Server("yahoo-finance")

@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """List available tools."""
    return [
        types.Tool(
            name="get_stock_info",
            description="Get general information about a stock, including current price, market cap, PE ratio, and dividend yield.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "The stock ticker symbol (e.g., 'AAPL', 'MSFT')."}
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
                    "ticker": {"type": "string", "description": "The stock ticker symbol."}
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_stock_news",
            description="Get the latest news articles for a specific stock.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "The stock ticker symbol."}
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_stock_history",
            description="Get historical price data for a stock.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "The stock ticker symbol."},
                    "period": {"type": "string", "description": "The period to fetch data for (e.g., '1mo', '1y', 'ytd', 'max').", "default": "1mo"},
                    "interval": {"type": "string", "description": "The data interval (e.g., '1d', '1wk', '1mo').", "default": "1d"}
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_stock_analysis",
            description="Get analyst recommendations, price targets, and earnings estimates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "The stock ticker symbol."}
                },
                "required": ["ticker"],
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> List[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Handle tool execution requests."""
    if not arguments:
        return [types.TextContent(type="text", text="Missing arguments")]

    ticker = arguments.get("ticker", "").upper()
    if not ticker:
        return [types.TextContent(type="text", text="Missing ticker symbol")]

    try:
        stock = yf.Ticker(ticker)
        
        if name == "get_stock_info":
            info = stock.info
            relevant_keys = [
                'longName', 'symbol', 'currentPrice', 'marketCap', 'trailingPE', 
                'forwardPE', 'dividendYield', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow',
                'averageVolume', 'sector', 'industry', 'longBusinessSummary'
            ]
            filtered_info = {k: info.get(k) for k in relevant_keys if k in info}
            return [types.TextContent(type="text", text=json.dumps(filtered_info, indent=2))]

        elif name == "get_stock_financials":
            financials = {
                "income_statement": stock.income_stmt.to_dict() if not stock.income_stmt.empty else {},
                "balance_sheet": stock.balance_sheet.to_dict() if not stock.balance_sheet.empty else {},
                "cash_flow": stock.cashflow.to_dict() if not stock.cashflow.empty else {}
            }
            return [types.TextContent(type="text", text=json.dumps(financials, indent=2, default=str))]

        elif name == "get_stock_news":
            news = stock.news
            return [types.TextContent(type="text", text=json.dumps(news, indent=2))]

        elif name == "get_stock_history":
            period = arguments.get("period", "1mo")
            interval = arguments.get("interval", "1d")
            history = stock.history(period=period, interval=interval)
            
            if history.empty:
                return [types.TextContent(type="text", text=f"No historical data found for {ticker}")]
            
            return [types.TextContent(type="text", text=history.to_json(orient="table"))]

        elif name == "get_stock_analysis":
            analysis = {
                "recommendations": stock.recommendations.to_dict() if stock.recommendations is not None and not stock.recommendations.empty else {},
                "recommendations_summary": stock.recommendations_summary.to_dict() if stock.recommendations_summary is not None and not stock.recommendations_summary.empty else {},
                "upgrades_downgrades": stock.upgrades_downgrades.to_dict() if stock.upgrades_downgrades is not None and not stock.upgrades_downgrades.empty else {}
            }
            return [types.TextContent(type="text", text=json.dumps(analysis, indent=2, default=str))]

        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        logger.error(f"Error in {name} for {ticker}: {e}")
        return [types.TextContent(type="text", text=f"Error: {str(e)}")]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="yahoo-finance",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
