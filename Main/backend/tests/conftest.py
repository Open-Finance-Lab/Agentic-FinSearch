"""Shared pytest fixtures / import-path fixes.

`tests/mcp_server/__init__.py` is an unused stub package that shadows the
real `mcp_server` when pytest adds `tests/` to sys.path before the backend
root. Force the backend dir to the front, then eagerly import the real
`mcp_server.xbrl.parser` so it's cached before any test module imports
`axioms.resolver`.
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
CORE_PROMPT_PATH = BACKEND_DIR / "prompts" / "core.md"

_BACKEND = str(BACKEND_DIR)
while _BACKEND in sys.path:
    sys.path.remove(_BACKEND)
sys.path.insert(0, _BACKEND)

import mcp_server.xbrl.parser  # noqa: F401,E402 — populate sys.modules


@pytest.fixture(scope="session")
def core_prompt() -> str:
    return CORE_PROMPT_PATH.read_text(encoding="utf-8")
