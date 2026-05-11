"""XBRL Taxonomy Lookup MCP Server.

Provides tools for searching US-GAAP XBRL taxonomy tags and validating
tag names against the official FASB 2026 taxonomy.
"""

import asyncio
import json
import logging
import os
from typing import List

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types

from pathlib import Path

from mcp_server.xbrl.parser import query_facts
from mcp_server.xbrl.search import search_tags, validate_tag, get_tag_info


logging.basicConfig(
    level=getattr(logging, os.getenv("MCP_LOG_LEVEL", "INFO").upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("xbrl-server")

server = Server("xbrl-taxonomy")


@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    """List available XBRL tools."""
    return [
        types.Tool(
            name="lookup_xbrl_tags",
            description=(
                "Search the official US-GAAP 2026 XBRL taxonomy for tag names matching "
                "a natural language description. Returns top candidates ranked by relevance. "
                "Use this to find the correct XBRL tag for a financial concept — NEVER "
                "invent tag names from memory."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural language description of the financial concept. "
                            "Include specific keywords for better results. "
                            "Examples: 'effective income tax rate continuing operations', "
                            "'debt instrument face amount', 'revenue from contract with customer'"
                        ),
                    },
                    "type_filter": {
                        "type": "string",
                        "description": (
                            "Optional: filter by data type. Values: monetary, percent, "
                            "shares, perShare, string, boolean, date, integer, pure"
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 10, max 25)",
                    },
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="validate_xbrl_tag",
            description=(
                "Check if an XBRL tag name exists in the official US-GAAP 2026 taxonomy. "
                "Use this to verify a tag before including it in output."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tag_name": {
                        "type": "string",
                        "description": "The XBRL tag name to validate (e.g., 'DebtInstrumentFaceAmount')",
                    },
                },
                "required": ["tag_name"],
            },
        ),
        types.Tool(
            name="query_xbrl_filing",
            description=(
                "Query a company's XBRL filing for the reported value of a specific "
                "XBRL tag. Returns all matching values with their reporting periods. "
                "Use this to verify financial claims against the original filing data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": (
                            "Company name or ticker symbol (e.g., 'apple', 'AAPL', "
                            "'microsoft', 'MSFT', 'tesla', 'TSLA')"
                        ),
                    },
                    "tag_name": {
                        "type": "string",
                        "description": (
                            "XBRL tag name to look up (e.g., "
                            "'EffectiveIncomeTaxRateContinuingOperations', "
                            "'RevenueFromContractWithCustomerExcludingAssessedTax'). "
                            "Use lookup_xbrl_tags first to find the correct tag name."
                        ),
                    },
                },
                "required": ["company", "tag_name"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> List[types.TextContent]:
    """Execute an XBRL tool."""
    arguments = arguments or {}

    if name == "lookup_xbrl_tags":
        query = arguments.get("query", "")
        type_filter = arguments.get("type_filter")
        top_k = min(arguments.get("top_k", 10), 25)

        if not query:
            return [types.TextContent(type="text", text="Error: query is required")]

        results = search_tags(query, top_k=top_k, type_filter=type_filter)

        if not results:
            return [
                types.TextContent(
                    type="text",
                    text=f"No XBRL tags found for query: '{query}'. Try different keywords.",
                )
            ]

        lines = [f"Found {len(results)} matching XBRL tags for '{query}':\n"]
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. {r['name']}  (type={r['type']}, period={r['period']}, "
                f"relevance={r['score']}/{r['coverage']})"
            )

        return [types.TextContent(type="text", text="\n".join(lines))]

    elif name == "validate_xbrl_tag":
        tag_name = arguments.get("tag_name", "")
        if not tag_name:
            return [types.TextContent(type="text", text="Error: tag_name is required")]

        info = get_tag_info(tag_name)
        if info:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"VALID: '{tag_name}' exists in the US-GAAP 2026 taxonomy.\n"
                        f"Type: {info['type']}, Period: {info['period']}"
                    ),
                )
            ]
        else:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"INVALID: '{tag_name}' does NOT exist in the US-GAAP 2026 taxonomy. "
                        "Use lookup_xbrl_tags to find the correct tag name."
                    ),
                )
            ]

    elif name == "query_xbrl_filing":
        company = arguments.get("company", "")
        tag_name = arguments.get("tag_name", "")

        if not company or not tag_name:
            return [types.TextContent(type="text", text="Error: both 'company' and 'tag_name' are required")]

        filings_dir = Path(__file__).parent / "filings"
        results = query_facts(company, tag_name, filings_dir)

        if not results:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"NOT FOUND: No values for tag '{tag_name}' in filings for '{company}'. "
                        "Check that the company name/ticker is correct and the tag name is valid "
                        "(use lookup_xbrl_tags to find correct tag names)."
                    ),
                )
            ]

        lines = [f"Found {len(results)} value(s) for '{tag_name}' in {company}'s filing:\n"]
        for i, r in enumerate(results, 1):
            period = r["period_start"] or ""
            if period:
                period = f"{period} to {r['period_end']}"
            else:
                period = f"as of {r['period_end']}"
            dim_note = " [dimensional breakdown]" if r["has_dimensions"] else ""
            lines.append(
                f"{i}. Value: {r['value']}  (unit={r['unit']}, period={period}){dim_note}"
            )

        return [types.TextContent(type="text", text="\n".join(lines))]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="xbrl-taxonomy",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
