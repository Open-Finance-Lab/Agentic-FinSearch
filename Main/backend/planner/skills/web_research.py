from typing import Optional, List
from .base import BaseSkill
from ._catalog import READ_ONLY_DATA_TOOLS


class WebResearchSkill(BaseSkill):
    """Fallback skill — current full-autonomy behavior."""

    @property
    def name(self) -> str:
        return "web_research"

    @property
    def tools_allowed(self) -> Optional[List[str]]:
        # Deny-by-default: the fallback skill returns an explicit finite list of
        # READ-ONLY data tools (never None). list() returns a fresh copy so a
        # caller cannot mutate the shared catalog.
        return list(READ_ONLY_DATA_TOOLS)

    @property
    def max_turns(self) -> int:
        return 10

    def matches(self, query: str, *, has_prescraped: bool, domain: str | None) -> float:
        return 0.1
