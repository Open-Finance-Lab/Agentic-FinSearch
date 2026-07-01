"""Handler for get_stock_news tool."""

import json
import logging
from typing import List

import mcp.types as types

from mcp_server.handlers.base import ToolHandler, ToolContext
from mcp_server.handlers.stock_info import get_ticker
from mcp_server.executor import run_in_executor


logger = logging.getLogger(__name__)


class GetStockNewsHandler(ToolHandler):
    """Handler for get_stock_news tool."""

    async def execute(self, ctx: ToolContext) -> List[types.TextContent]:
        """Execute get_stock_news tool.

        Args:
            ctx: Tool execution context

        Returns:
            List containing news articles as JSON
        """
        stock = await get_ticker(ctx.ticker)
        news = await run_in_executor(lambda: stock.news)

        if not news:
            return [types.TextContent(
                type="text",
                text=f"No recent news found for {ctx.ticker}"
            )]

        return [types.TextContent(
            type="text",
            text=json.dumps(news, indent=2)
        )]
