import re
from typing import Optional, List
from .base import BaseSkill

_PATTERNS = [
    r"\b(stock|share) price\b",
    r"\bmarket cap(italization)?\b",
    r"\b(PE|P/E) ratio\b",
    r"\bdividend (yield|rate)\b",
    r"\b52[- ]?week (high|low|range)\b",
    r"\bhow (is|are) .{1,20} (doing|trading|performing)\b",
    r"\bcurrent (price|value|quote)\b",
    r"\bprice (of|for)\b",
    r"\bquote (for|of)\b",
    r"^(?!.*options?)\b.*\bvolume\b",
    r"\bbeta\b",
    r"\bEPS\b",
    r"\bearnings (date|estimate|surprise)\b",
    r"\bshares outstanding\b",
    r"\bfloat\b.*\bshares\b",
    r"\b(day|intraday) (range|high|low)\b",
    r"\bpre[- ]?market\b",
    r"\bafter[- ]?hours?\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


class StockFundamentalsSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "stock_fundamentals"

    @property
    def tools_allowed(self) -> Optional[List[str]]:
        return ["get_stock_info", "get_stock_financials", "get_stock_history", "get_earnings_info", "calculate"]

    @property
    def max_turns(self) -> int:
        return 5

    def matches(self, query: str, *, has_prescraped: bool, domain: str | None) -> float:
        for p in _COMPILED:
            if p.search(query):
                return 0.8
        return 0.0
