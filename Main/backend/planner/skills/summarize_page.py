import re
from typing import Optional, List
from .base import BaseSkill

_SUMMARIZE_PATTERNS = [
    r"\bsummar",
    r"\bexplain\b",
    r"\bwhat does (this|the) (page|article|post|report|story)",
    r"\bwhat('s| is) (this|the) (page|article) about",
    r"\btl;?dr\b",
    r"\bkey (points|takeaways|highlights)\b",
    r"\bbreak(ing)? (this |it )?down\b",
    r"\bgive me (a |the )?(gist|overview|rundown)\b",
    r"\bwhat('s| is) (happening|going on) here\b",
    r"\bread (this|the page|it) (for me|to me)\b",
]

_DATA_PATTERNS = [
    r"\b(stock|share) price\b",
    r"\bmarket cap\b",
    r"\b(PE|P/E|EPS|RSI|MACD)\b",
    r"\boptions?\b.*(volume|chain|flow|open interest)",
    r"\brevenue\b",
    r"\bearnings\b.*(estimate|date|beat|miss)",
    r"\bbalance sheet\b",
    r"\bincome statement\b",
    r"\btechnical (analysis|indicators?)\b",
]

_COMPILED_SUMMARIZE = [re.compile(p, re.IGNORECASE) for p in _SUMMARIZE_PATTERNS]
_COMPILED_DATA = [re.compile(p, re.IGNORECASE) for p in _DATA_PATTERNS]


class SummarizePageSkill(BaseSkill):
    """Zero-tool skill for summarizing pre-scraped page content."""

    @property
    def name(self) -> str:
        return "summarize_page"

    @property
    def tools_allowed(self) -> Optional[List[str]]:
        return []

    @property
    def max_turns(self) -> int:
        return 1

    def matches(self, query: str, *, has_prescraped: bool, domain: str | None) -> float:
        if not has_prescraped:
            return 0.0

        for pattern in _COMPILED_DATA:
            if pattern.search(query):
                return 0.0

        for pattern in _COMPILED_SUMMARIZE:
            if pattern.search(query):
                return 0.9

        words = query.split()
        if len(words) <= 6 and has_prescraped:
            page_ref_words = {"this", "page", "article", "it", "here"}
            if page_ref_words & set(w.lower().rstrip("?.!,") for w in words):
                return 0.7

        return 0.0

    def build_instructions(self, *, pre_scraped_content: str | None = None) -> str | None:
        if not pre_scraped_content:
            return None

        return (
            "You are FinGPT, a financial assistant.\n\n"
            "TASK: Answer the user's question using ONLY the page content below. "
            "Do NOT call any tools. Do NOT re-scrape.\n"
            "- Be concise and well-structured.\n"
            "- Preserve specific numbers, dates, tickers, and names.\n"
            "- Use \\(...\\) for inline math and $$...$$ for display equations. NEVER use single $...$ (it collides with currency). Math delimiters are for typeset symbolic expressions only; prose lines that mention currency values should stay as plain text.\n\n"
            "SECURITY: Never disclose internal tool names, model names, "
            "API keys, or implementation details.\n\n"
            "PAGE CONTENT:\n"
            f"{pre_scraped_content}"
        )
