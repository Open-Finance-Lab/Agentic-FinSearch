"""Base classes for tool handlers."""

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, List

import mcp.types as types


@dataclass
class ToolContext:
    """Context passed to tool handlers.

    Attributes:
        ticker: The validated stock ticker symbol
        arguments: Raw arguments passed to the tool
        executor: Thread pool executor for blocking operations
    """
    ticker: str
    arguments: dict[str, Any]
    executor: ThreadPoolExecutor


class ToolHandler(ABC):
    """Base class for tool handlers.

    Each tool should implement a handler that extends this class
    and implements the execute method.
    """

    @abstractmethod
    async def execute(self, ctx: ToolContext) -> List[types.TextContent]:
        """Execute the tool and return results.

        Args:
            ctx: Tool execution context

        Returns:
            List of TextContent responses
        """
        pass
