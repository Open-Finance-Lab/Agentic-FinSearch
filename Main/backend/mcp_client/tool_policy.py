"""Deny-by-default tool allow-list policy for the MCP agent stack.

Two layers of enforcement use this module:
  * the attach layer (``mcp_client.agent.create_fin_agent``) calls
    ``filter_to_allowed`` so only a skill's declared tools are attached; and
  * the exec layer (``mcp_client.mcp_manager.MCPClientManager.execute_tool``)
    calls ``is_allowed`` as a second line of defense BEFORE invoking an MCP
    tool, so a hallucinated/leaked tool name is refused even if it somehow
    got attached.

``DENY_ALWAYS`` is a belt-and-suspenders denylist of the 14
``@modelcontextprotocol/server-filesystem`` tool names: these read/write/
traverse the container filesystem and must NEVER be reachable from a public,
unauthenticated agent run, regardless of any allow-list.
"""
from typing import List


# The 14 tools exposed by @modelcontextprotocol/server-filesystem (rooted at
# /app in mcp_server_config.json). None of these may ever be allow-listed.
DENY_ALWAYS = frozenset({
    "read_file",
    "read_text_file",
    "read_media_file",
    "read_multiple_files",
    "write_file",
    "edit_file",
    "create_directory",
    "list_directory",
    "list_directory_with_sizes",
    "directory_tree",
    "move_file",
    "search_files",
    "get_file_info",
    "list_allowed_directories",
})


def is_allowed(name: str, allowed) -> bool:
    """True iff ``name`` is in the per-skill allow-list AND not denied always.

    ``allowed`` is the finite list/set of tool names the active skill declared.
    A name in DENY_ALWAYS is refused even when present in ``allowed``.
    """
    return name in allowed and name not in DENY_ALWAYS


def filter_to_allowed(tools, allowed) -> List:
    """Return only the tools whose ``.name`` passes :func:`is_allowed`."""
    return [t for t in tools if is_allowed(t.name, allowed)]
