"""Security regression: the filesystem MCP server must stay disabled.

P0 Root A.1 — `@modelcontextprotocol/server-filesystem` exposes write-capable
tools (write_file, edit_file, create_directory, move_file, ...) rooted at /app.
A public, unauthenticated agent run must never be able to attach or execute
them, so the server is hard-disabled at the source: `mcp_server_config.json`.
The loader (mcp_client/mcp_manager.py:92) skips any server with disabled=True,
so this never spawns. This test fails loudly if anyone flips `disabled` back
to false or drops it.
"""
import json
from pathlib import Path

from django.test import SimpleTestCase

CONFIG_PATH = Path(__file__).resolve().parent.parent / "mcp_server_config.json"

# The tools exposed by @modelcontextprotocol/server-filesystem. Listed here so
# the intent (these write-capable tools must be unreachable) is explicit.
FILESYSTEM_TOOL_NAMES = frozenset({
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

DATA_SERVERS = ("sec-edgar", "yahoo-finance", "tradingview", "xbrl-taxonomy")


class FilesystemMcpDisabledTests(SimpleTestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.servers = self.config.get("mcpServers", {})

    def test_filesystem_server_is_disabled(self):
        """If the filesystem block exists, it must be disabled:true."""
        fs = self.servers.get("filesystem")
        if fs is not None:
            self.assertIs(
                fs.get("disabled"),
                True,
                "filesystem MCP server must have 'disabled': true",
            )

    def test_data_servers_remain_enabled(self):
        """Disabling filesystem must not collaterally disable data servers."""
        for name in DATA_SERVERS:
            with self.subTest(server=name):
                self.assertIn(name, self.servers)
                self.assertNotEqual(
                    self.servers[name].get("disabled", False),
                    True,
                    f"data server '{name}' must stay enabled",
                )
