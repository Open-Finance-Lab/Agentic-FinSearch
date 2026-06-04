"""Structured error handling for Yahoo Finance MCP server."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import mcp.types as types


class ErrorType(Enum):
    """Classification of error types."""
    VALIDATION = "validation_error"
    NOT_FOUND = "not_found"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network_error"
    INTERNAL = "internal_error"
    EXCHANGE_ERROR = "exchange_error"
    SYMBOL_ERROR = "symbol_error"


@dataclass
class ToolError:
    """Structured error response for tool calls."""

    error_type: ErrorType
    message: str
    ticker: Optional[str] = None
    details: Optional[dict] = None

    def to_content(self) -> types.TextContent:
        """Convert to MCP TextContent.

        Returns:
            TextContent with JSON-formatted error
        """
        error_data = {
            "error": self.error_type.value,
            "message": self.message
        }

        if self.ticker:
            error_data["ticker"] = self.ticker

        if self.details:
            error_data["details"] = self.details

        return types.TextContent(
            type="text",
            text=json.dumps(error_data, indent=2)
        )
