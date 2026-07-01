"""Handler for get_stock_financials tool."""

import asyncio
import json
import logging
from typing import List

import mcp.types as types

from mcp_server.handlers.base import ToolHandler, ToolContext
from mcp_server.handlers.stock_info import get_ticker
from mcp_server.executor import run_in_executor


logger = logging.getLogger(__name__)


def _safe_financials_to_dict(df) -> dict:
    """Convert financial DataFrame to dict, stringifying Timestamp index/columns."""
    if df is None or df.empty:
        return {}
    df = df.copy()
    df.index = df.index.astype(str)
    df.columns = df.columns.astype(str)
    return df.to_dict()


class GetStockFinancialsHandler(ToolHandler):
    """Handler for get_stock_financials tool."""

    async def execute(self, ctx: ToolContext) -> List[types.TextContent]:
        """Execute get_stock_financials tool.

        Args:
            ctx: Tool execution context

        Returns:
            List containing financial statements as JSON
        """
        stock = await get_ticker(ctx.ticker)

        # Get all financial statements in parallel
        income_stmt, balance_sheet, cashflow = await asyncio.gather(
            run_in_executor(lambda: stock.income_stmt),
            run_in_executor(lambda: stock.balance_sheet),
            run_in_executor(lambda: stock.cashflow),
        )

        financials = {
            "income_statement": _safe_financials_to_dict(income_stmt),
            "balance_sheet": _safe_financials_to_dict(balance_sheet),
            "cash_flow": _safe_financials_to_dict(cashflow),
        }

        # If all statements are empty, return a clear message (e.g. for indices)
        if all(v == {} for v in financials.values()):
            return [types.TextContent(
                type="text",
                text=f"No financial statements available for {ctx.ticker}. "
                     "Financial data is only available for individual stocks, not indices or funds."
            )]

        return [types.TextContent(
            type="text",
            text=json.dumps(financials, indent=2, default=str)
        )]
