"""§PR-A.4 — event-loop ownership: the last asyncio.get_event_loop() sites.

Python 3.14 turns get_event_loop()'s implicit get-or-create into an error,
so runtime code must declare its loop ownership explicitly: borrow the
current loop with get_running_loop() inside coroutines, or own a private
loop with asyncio.run() at sync entry points. These tests pin both
replacements and structurally guard against the deprecated idiom returning.
"""
import ast
import asyncio
import os
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
_EXCLUDED_PARTS = {"tests", ".venv", "__pycache__", "node_modules"}


def _runtime_python_files():
    # os.walk so excluded trees are pruned before descent: rglob("*.py")
    # would enumerate all ~10k files under .venv only to discard them,
    # which costs ~a minute per run on drvfs/9p-mounted checkouts (WSL).
    for dirpath, dirnames, filenames in os.walk(BACKEND_DIR):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_PARTS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _binds_get_event_loop(node):
    # Both spellings: `<anything>.get_event_loop` (module attribute, however
    # aliased) and `from asyncio[.events] import get_event_loop` (a bare-Name
    # call site the Attribute match can't see, so flag the import itself).
    if isinstance(node, ast.Attribute):
        return node.attr == "get_event_loop"
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "asyncio" and any(
            alias.name == "get_event_loop" for alias in node.names)
    return False


def test_no_get_event_loop_in_runtime_code():
    """Structural sentinel (§PR-A.4): asyncio.get_event_loop() must not
    return to runtime code. Its get-or-create semantics become a hard error
    on Python 3.14, and every legitimate use here is either
    get_running_loop() (inside a coroutine) or asyncio.run() (sync entry
    point). AST-based so comments discussing the old idiom stay legal."""
    offenders = []
    for path in _runtime_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _binds_get_event_loop(node):
                offenders.append(
                    f"{path.relative_to(BACKEND_DIR)}:{node.lineno}")
    assert offenders == [], (
        "get_event_loop() call sites found (implicit loop creation breaks "
        "on Python 3.14): " + ", ".join(offenders)
    )


@pytest.mark.parametrize("snippet", [
    "import asyncio\nloop = asyncio.get_event_loop()\n",
    "import asyncio as aio\nloop = aio.get_event_loop()\n",
    "from asyncio import get_event_loop\nloop = get_event_loop()\n",
    "from asyncio.events import get_event_loop\n",
])
def test_sentinel_catches_every_spelling(snippet):
    # Guards the guard: each way of reaching get_event_loop must trip the
    # predicate, or the sentinel silently stops covering that spelling.
    assert any(_binds_get_event_loop(n) for n in ast.walk(ast.parse(snippet)))


async def test_connect_to_servers_captures_running_loop(tmp_path):
    """The manager's cross-thread bridge (run_async_from_sync →
    run_coroutine_threadsafe) needs the exact loop the manager's tasks run
    on; connect_to_servers must capture the running loop, never a policy
    fallback."""
    from mcp_client.mcp_manager import MCPClientManager

    mgr = MCPClientManager(config_path=str(tmp_path / "absent.json"),
                           verbose=False)
    try:
        await mgr.connect_to_servers()
        assert mgr._loop is asyncio.get_running_loop()
    finally:
        await mgr.cleanup()


def test_sync_search_wrapper_runs_coroutine_from_sync_context(
        monkeypatch, recwarn):
    """The wrapper's one job: run the async search to completion from a
    plain sync context (Django WSGI worker thread) and hand back its
    result — without the 'no current event loop' DeprecationWarning the
    old get_event_loop() path emitted from the main thread."""
    from datascraper import openai_search

    async def fake_search(*args, **kwargs):
        return ("answer", [{"url": "https://example.com"}])

    monkeypatch.setattr(
        openai_search, "create_responses_api_search_async", fake_search)
    monkeypatch.setattr(openai_search, "async_client", object())
    result = openai_search.create_responses_api_search("q", [])
    assert result == ("answer", [{"url": "https://example.com"}])
    assert [w for w in recwarn.list
            if "event loop" in str(w.message)] == []


async def test_sync_search_wrapper_refuses_running_loop(monkeypatch):
    """Called from async context, the sync wrapper must fail fast with a
    clear contract error pointing at the async API. The old code path
    raised TypeError from run_coroutine_threadsafe(Task) — and would have
    deadlocked on .result() had it gotten further. The guard must also
    fire BEFORE the coroutine is built, so no abandoned-coroutine
    RuntimeWarning leaks into suite output."""
    from datascraper import openai_search

    async def fake_search(*args, **kwargs):  # pragma: no cover — never runs
        return ("unreachable", [])

    monkeypatch.setattr(
        openai_search, "create_responses_api_search_async", fake_search)
    monkeypatch.setattr(openai_search, "async_client", object())
    with pytest.raises(RuntimeError,
                       match="create_responses_api_search_async"):
        openai_search.create_responses_api_search("q", [])
