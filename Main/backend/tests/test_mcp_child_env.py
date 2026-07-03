"""Security regression: MCP stdio children get an allow-listed env, not ours.

Root G hygiene (2026-06-29 audit follow-up). mcp_client/mcp_manager.py used to
seed every stdio child with `os.environ.copy()`, so sec-edgar-mcp and any
npx-fetched server inherited OPENAI_API_KEY / DJANGO_SECRET_KEY / Redis + Mem0
credentials it never needed — one `os.environ` dump in a compromised (or
merely chatty) dependency would exfiltrate every backend secret. The spawn
path now starts from the MCP SDK's safe base (get_default_environment():
HOME/LOGNAME/PATH/SHELL/TERM/USER on posix) and layers on only what the
server's config block declares: the byte-identical ${VAR} interpolation loop,
the optional "inheritEnv" passthrough, and the MCP_LOG_LEVEL injection.

Four layers of pinning, so a revert cannot slip through quietly:
  1. behavioral   — capture StdioServerParameters via a fake stdio_client and
                    prove a poisoned parent env does not reach the child;
  2. structural   — `os.environ.copy(` must not reappear in non-comment lines
                    of mcp_manager.py;
  3. config       — no server's "inheritEnv" may name a secret-shaped var
                    (the runtime passthrough is deliberately dumb; hygiene is
                    enforced here, at review time);
  4. real spawn   — the xbrl-taxonomy server (offline, bundled JSON) must
                    initialize and answer a tool call under the allow-listed
                    env, proving the base set is sufficient for a real child.
"""
import asyncio
import io
import json
import os
import re
import sys
import tokenize
from pathlib import Path

import pytest
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS

from mcp_client import mcp_manager as mcp_manager_module
from mcp_client.mcp_manager import MCPClientManager

BACKEND_DIR = Path(__file__).resolve().parent.parent
MANAGER_SOURCE = BACKEND_DIR / "mcp_client" / "mcp_manager.py"
CONFIG_PATH = BACKEND_DIR / "mcp_server_config.json"

# Representative secrets the backend actually holds at runtime (see
# django_config settings / deploy env). Values are sentinels, never real.
POISON_SECRETS = {
    "OPENAI_API_KEY": "sk-poison-openai-000",
    "ANTHROPIC_API_KEY": "sk-ant-poison-000",
    "DJANGO_SECRET_KEY": "poison-django-secret",
    "AWS_SECRET_ACCESS_KEY": "poison-aws-secret",
    "REDIS_PASSWORD": "poison-redis-pass",
    "MEM0_API_KEY": "poison-mem0-key",
    # Deliberately secret-UNshaped: proves the fix is an allow-list, not a
    # denylist of scary-looking names.
    "TOTALLY_INNOCENT_PARENT_VAR": "still-must-not-leak",
}

# Var names a server config may legitimately inherit must not look like any
# of these. Mirrors the leak_detector's notion of credential material.
SECRET_NAME_PATTERN = re.compile(
    r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|AUTH|COOKIE|SESSION)",
    re.IGNORECASE,
)


class _FakeTransport:
    """Stands in for stdio_client's context manager; never spawns anything."""

    async def __aenter__(self):
        return (object(), object())

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Stands in for ClientSession; initialize() is the only call the spawn
    path makes before registering the session."""

    def __init__(self, read_stream, write_stream):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None


async def _capture_spawn_env(monkeypatch, server_name, server_config, *, verbose):
    """Run the real _connect_server spawn path, capturing the
    StdioServerParameters it hands to stdio_client."""
    captured = {}

    def fake_stdio_client(server_params):
        captured["params"] = server_params
        return _FakeTransport()

    monkeypatch.setattr(mcp_manager_module, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(mcp_manager_module, "ClientSession", _FakeSession)

    manager = MCPClientManager(verbose=verbose, printer=lambda _msg: None)
    try:
        await manager._connect_server(server_name, server_config)
    finally:
        await manager.exit_stack.aclose()
    return captured["params"]


async def test_child_env_is_allowlisted_not_inherited(monkeypatch):
    """Poisoned parent env must not reach the child; ${VAR} interpolation,
    PATH/HOME and MCP_LOG_LEVEL must survive the allow-listing."""
    for name, value in POISON_SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", os.environ.get("HOME", "/tmp/fake-home"))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "FinGPT Test test@example.com")

    # Use the SHIPPED sec-edgar block so the test tracks the real config's
    # ${SEC_EDGAR_USER_AGENT} interpolation, not a synthetic lookalike.
    servers = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["mcpServers"]
    sec_edgar = servers["sec-edgar"]

    params = await _capture_spawn_env(
        monkeypatch, "sec-edgar", sec_edgar, verbose=True
    )
    child_env = params.env

    for name in POISON_SECRETS:
        assert name not in child_env, f"secret {name} leaked into child env"

    # Config-declared values arrive interpolated, not as literal ${...}.
    assert child_env["SEC_EDGAR_USER_AGENT"] == "FinGPT Test test@example.com"
    assert child_env["SEC_EDGAR_RATE_LIMIT"] == "8"

    # The safe base survives — a child that cannot see PATH/HOME cannot run.
    assert child_env["PATH"] == os.environ["PATH"]
    assert child_env["HOME"] == os.environ["HOME"]

    # Log-level injection is untouched by the allow-listing (verbose=True).
    assert child_env["MCP_LOG_LEVEL"] == "INFO"

    # Strict upper bound: nothing outside base ∪ config-env ∪ MCP_LOG_LEVEL.
    allowed = (
        set(DEFAULT_INHERITED_ENV_VARS)
        | set(sec_edgar.get("env", {}))
        | {"MCP_LOG_LEVEL"}
    )
    assert set(child_env) <= allowed, sorted(set(child_env) - allowed)


async def test_quiet_manager_still_injects_warning_level(monkeypatch):
    """verbose=False must keep producing MCP_LOG_LEVEL=WARNING for children."""
    params = await _capture_spawn_env(
        monkeypatch,
        "yahoo-finance",
        {"command": "python", "args": ["-m", "mcp_server.yahoo_finance_server"]},
        verbose=False,
    )
    assert params.env["MCP_LOG_LEVEL"] == "WARNING"


async def test_inherit_env_passthrough_is_optin_and_exact(monkeypatch):
    """"inheritEnv" copies exactly the named parent vars — set names arrive,
    unset names are silently skipped, nothing else rides along."""
    monkeypatch.setenv("FINGPT_TEST_PROXY", "http://proxy.internal:3128")
    monkeypatch.delenv("FINGPT_TEST_UNSET", raising=False)

    params = await _capture_spawn_env(
        monkeypatch,
        "synthetic",
        {
            "command": "python",
            "args": ["-m", "mcp_server.yahoo_finance_server"],
            "inheritEnv": ["FINGPT_TEST_PROXY", "FINGPT_TEST_UNSET"],
        },
        verbose=False,
    )
    assert params.env["FINGPT_TEST_PROXY"] == "http://proxy.internal:3128"
    assert "FINGPT_TEST_UNSET" not in params.env


def _source_without_comments(path: Path) -> str:
    """Return the file's source with every #-comment removed (tokenizer-based,
    so '#' inside string literals does not truncate lines)."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    comment_starts = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            comment_starts[tok.start[0]] = tok.start[1]
    stripped = [
        line[: comment_starts[lineno]] if lineno in comment_starts else line
        for lineno, line in enumerate(lines, 1)
    ]
    return "\n".join(stripped)


def test_manager_source_never_copies_full_environ():
    """Structural sentinel: the wholesale-inheritance idiom must not return to
    mcp_manager.py outside of comments, in any whitespace disguise."""
    code = _source_without_comments(MANAGER_SOURCE)
    condensed = "".join(code.split())
    assert "environ.copy(" not in condensed, (
        "os.environ.copy() is back in mcp_manager.py — children would inherit "
        "every backend secret again"
    )
    assert "dict(os.environ)" not in condensed, (
        "dict(os.environ) is os.environ.copy() in a trenchcoat"
    )
    # Positive pin: the spawn path must still build from the SDK's safe base.
    assert "get_default_environment(" in condensed, (
        "spawn path no longer starts from get_default_environment() — "
        "verify the child env is still allow-listed"
    )


def test_config_inherit_env_names_are_not_secret_shaped():
    """Config sentinel: "inheritEnv" is a passthrough with no runtime
    filtering, so no server may use it to smuggle a secret-shaped var."""
    servers = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["mcpServers"]
    assert servers, "mcp_server_config.json lost its mcpServers block"
    for server_name, server_config in servers.items():
        inherit = server_config.get("inheritEnv", [])
        assert isinstance(inherit, list), (
            f"{server_name}: inheritEnv must be a list of var names"
        )
        for name in inherit:
            assert isinstance(name, str)
            assert not SECRET_NAME_PATTERN.search(name), (
                f"{server_name}: inheritEnv entry {name!r} is secret-shaped; "
                "pass it via the interpolated 'env' block only if the child "
                "genuinely needs it, and justify it in review"
            )


async def test_real_child_spawns_under_allowlisted_env(monkeypatch):
    """Real-spawn smoke: the xbrl-taxonomy server (offline — bundled
    us_gaap_2026.json, no network) must initialize, list tools and answer a
    tool call with ONLY the allow-listed env, proving the base set is
    sufficient for an actual child process."""
    # Poison the parent even here: the child must come up fine WITHOUT them.
    for name, value in POISON_SECRETS.items():
        monkeypatch.setenv(name, value)
    # `python -m mcp_server.xbrl.server` resolves the package via cwd.
    monkeypatch.chdir(BACKEND_DIR)

    manager = MCPClientManager(verbose=False, printer=lambda _msg: None)
    config = {
        # sys.executable == this venv's python, so the child sees the same
        # installed `mcp` + `mcp_server` without needing PYTHONPATH leaks.
        "command": sys.executable,
        "args": ["-m", "mcp_server.xbrl.server"],
    }

    async def _smoke():
        # Enter and exit the stack inside ONE task: anyio cancel scopes in
        # stdio_client refuse to close from a different task, and wait_for
        # wraps its awaitable in a new task.
        try:
            await manager._connect_server("xbrl-taxonomy", config)
            session = manager.sessions["xbrl-taxonomy"]
            tools = await session.list_tools()
            result = await session.call_tool(
                "validate_xbrl_tag", {"tag_name": "Assets"}
            )
            return tools, result
        finally:
            await manager.exit_stack.aclose()

    tools, result = await asyncio.wait_for(_smoke(), timeout=90)

    tool_names = {tool.name for tool in tools.tools}
    assert "lookup_xbrl_tags" in tool_names
    assert "validate_xbrl_tag" in tool_names

    text = "".join(
        item.text for item in result.content if getattr(item, "type", "") == "text"
    )
    assert "VALID" in text and "Assets" in text
