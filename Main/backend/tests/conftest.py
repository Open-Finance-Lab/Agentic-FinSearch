"""Shared pytest fixtures / import-path fixes.

`tests/mcp_server/__init__.py` is an unused stub package that shadows the
real `mcp_server` when pytest adds `tests/` to sys.path before the backend
root. Force the backend dir to the front, then eagerly import the real
`mcp_server.xbrl.parser` so it's cached before any test module imports
`axioms.resolver`.
"""
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
CORE_PROMPT_PATH = BACKEND_DIR / "prompts" / "core.md"

_BACKEND = str(BACKEND_DIR)
while _BACKEND in sys.path:
    sys.path.remove(_BACKEND)
sys.path.insert(0, _BACKEND)

# Configure Django once so django.test.SimpleTestCase / RequestFactory / override_settings
# work under bare pytest (this repo has no pytest-django). SimpleTestCase needs no DB
# (settings.DATABASES is {}), so plain django.setup() is sufficient for the security suite.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "django_config.settings")
import django  # noqa: E402

django.setup()

import mcp_server.xbrl.parser  # noqa: F401,E402 — populate sys.modules


@pytest.fixture(scope="session")
def core_prompt() -> str:
    return CORE_PROMPT_PATH.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _hermetic_fingpt_api_key(monkeypatch):
    """Keep every test hermetic with respect to FINGPT_API_KEY.

    django.setup() above runs settings' load_dotenv(Main/backend/.env), so a key
    set in a developer's .env becomes a process-wide os.environ value. api.auth
    now gates the extension/axiom/agent views on that key, so an ambient value
    would 401 the many pre-existing tests that invoke those views without an
    Authorization header. Clear it by default so those tests hit the dev-open
    path. The bearer-auth tests set it explicitly via monkeypatch.setenv; because
    that lands on the same MonkeyPatch instance as this delenv and the two unwind
    together (LIFO) at teardown, the per-test setenv wins within the test.
    """
    monkeypatch.delenv("FINGPT_API_KEY", raising=False)
