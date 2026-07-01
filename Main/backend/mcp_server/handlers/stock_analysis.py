"""Handler for get_stock_analysis tool."""

import asyncio
import json
import logging
from typing import List

import mcp.types as types

from mcp_server.handlers.base import ToolHandler, ToolContext
from mcp_server.handlers.stock_info import get_ticker
from mcp_server.executor import run_in_executor


logger = logging.getLogger(__name__)


class GetStockAnalysisHandler(ToolHandler):
    """Handler for get_stock_analysis tool."""

    @staticmethod
    def _safe_dict(df):
        """Convert DataFrame/dict to a JSON-safe dict.

        Stringifies index and column labels so pd.Timestamp keys
        don't crash json.dumps (which only applies default= to values, not keys).
        """
        if df is None:
            return {}
        if hasattr(df, 'empty') and df.empty:
            return {}
        if hasattr(df, 'to_dict'):
            df = df.copy()
            df.index = df.index.astype(str)
            df.columns = df.columns.astype(str)
            return df.to_dict()
        if isinstance(df, dict):
            return df
        return {}

    async def execute(self, ctx: ToolContext) -> List[types.TextContent]:
        """Execute get_stock_analysis tool.

        Args:
            ctx: Tool execution context

        Returns:
            List containing analyst recommendations and estimates as JSON
        """
        stock = await get_ticker(ctx.ticker)

        # Get all analysis data in parallel
        recommendations, recommendations_summary, upgrades_downgrades, price_targets = await asyncio.gather(
            run_in_executor(lambda: stock.recommendations),
            run_in_executor(lambda: stock.recommendations_summary),
            run_in_executor(lambda: stock.upgrades_downgrades),
            run_in_executor(lambda: stock.analyst_price_targets),
        )

        analysis = {
            "recommendations": self._safe_dict(recommendations),
            "recommendations_summary": self._safe_dict(recommendations_summary),
            "upgrades_downgrades": self._safe_dict(upgrades_downgrades),
            "analyst_price_targets": self._safe_dict(price_targets),
        }

        return [types.TextContent(
            type="text",
            text=json.dumps(analysis, indent=2, default=str)
        )]
