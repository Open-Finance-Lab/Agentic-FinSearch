import asyncio
import json
import logging
import os
import shutil
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.sse import sse_client
from mcp.types import Tool as MCPTool, CallToolResult

logger = logging.getLogger(__name__)

class MCPClientManager:
    """
    Manages connections to multiple MCP servers and aggregates their tools.
    """
    def __init__(
        self,
        config_path: Optional[str] = None,
        *,
        verbose: bool = True,
        printer: Optional[Callable[[str], None]] = None,
    ):
        self.exit_stack = AsyncExitStack()
        self.sessions: Dict[str, ClientSession] = {}
        self.tools_map: Dict[str, str] = {}
        self.verbose = verbose
        self._printer = printer or print

        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = Path(__file__).resolve().parent.parent / "mcp_server_config.json"

        self._stop_event = asyncio.Event()
        self._connection_task: Optional[asyncio.Task] = None
        self._servers_ready = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _log(self, message: str, *, force: bool = False):
        """Print debug output only when verbose or forced."""
        if self.verbose or force:
            self._printer(message)

    async def load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            self._log(f"[MCP DEBUG] Config file not found at {self.config_path}", force=True)
            logger.warning(f"MCP config file not found at {self.config_path}")
            return {}
        
        try:
            self._log(f"[MCP DEBUG] Loading config from {self.config_path}")
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self._log(f"[MCP DEBUG] Failed to load MCP config: {e}", force=True)
            logger.error(f"Failed to load MCP config: {e}")
            return {}

    async def connect_to_servers(self):
        """Starts the background task to connect to all enabled servers."""
        if self._connection_task and not self._connection_task.done():
             self._log("[MCP DEBUG] MCP servers already connected or connecting.")
             await self._servers_ready.wait()
             return

        self._log("[MCP DEBUG] Starting MCP server connection loop...")

        # Captured for run_async_from_sync's cross-thread submissions; must
        # be the loop this coroutine (and _run_servers) runs on, so borrow
        # the running loop — never a policy get-or-create.
        self._loop = asyncio.get_running_loop()

        self._stop_event.clear()
        self._servers_ready.clear()
        self._connection_task = asyncio.create_task(self._run_servers())

        await self._servers_ready.wait()
        self._log("[MCP DEBUG] MCP servers ready.")

    async def _run_servers(self):
        """Background task to maintain server connections."""
        try:
            async with self.exit_stack:
                config = await self.load_config()
                mcp_servers = config.get("mcpServers", {})
                
                self._log(f"[MCP DEBUG] Found {len(mcp_servers)} servers in config")

                for server_name, server_config in mcp_servers.items():
                    if server_config.get("disabled", False):
                        self._log(f"[MCP DEBUG] Server '{server_name}' is disabled, skipping.")
                        continue
                    
                    try:
                        self._log(f"[MCP DEBUG] Connecting to server: {server_name}...")
                        await self._connect_server(server_name, server_config)
                        self._log(f"[MCP DEBUG] Successfully connected to: {server_name}")
                    except Exception as e:
                        self._log(f"[MCP DEBUG] Failed to connect to {server_name}: {e}", force=True)
                        logger.error(f"Failed to connect to MCP server {server_name}: {e}")
                
                self._servers_ready.set()
                
                await self._stop_event.wait()
                
        except Exception as e:
             self._log(f"[MCP DEBUG] MCP Server loop crashed: {e}", force=True)
             logger.error(f"MCP Server loop crashed: {e}")
        finally:
             self._servers_ready.clear()
             self._log("[MCP DEBUG] MCP Server loop exited.", force=True)

    async def _connect_server(self, server_name: str, config: Dict[str, Any]):
        """Establishes a connection to a single MCP server."""
        
        
        if "url" in config:
            url = config["url"]
            self._log(f"[MCP DEBUG] {server_name} using SSE transport: {url}")
            transport_ctx = sse_client(url)
            read_stream, write_stream = await self.exit_stack.enter_async_context(transport_ctx)
            
            session = ClientSession(read_stream, write_stream)
            await self.exit_stack.enter_async_context(session)
            await session.initialize()
            
            self.sessions[server_name] = session
            
        elif "command" in config:
            command = config["command"]
            args = config.get("args", [])
            env = config.get("env", {})

            self._log(f"[MCP DEBUG] {server_name} using Stdio transport: {command} {args}")

            # Root G hygiene (2026-06-29 audit follow-up): never hand a child
            # the full backend environment. os.environ here carries
            # OPENAI_API_KEY / DJANGO_SECRET_KEY / Redis + Mem0 credentials,
            # and stdio servers run third-party code (sec-edgar-mcp,
            # npx-fetched packages) that needs none of them — a compromised or
            # merely chatty dependency could exfiltrate every secret with one
            # os.environ dump. Start from the MCP SDK's safe base
            # (HOME/LOGNAME/PATH/SHELL/TERM/USER on posix, exported shell
            # functions skipped) — exactly what stdio_client would use if we
            # passed env=None — then layer on only what this server's config
            # block declares. tests/test_mcp_child_env.py pins this.
            full_env = get_default_environment()

            # Opt-in passthrough for non-secret parent vars a server may need
            # (e.g. proxy settings): "inheritEnv": ["NAME", ...]. Applied
            # before the env block so explicit config values always win.
            # Secret-shaped names are rejected by the config sentinel in
            # tests/test_mcp_child_env.py; no shipped server uses this today.
            for inherited_name in config.get("inheritEnv", []):
                if inherited_name in os.environ:
                    full_env[inherited_name] = os.environ[inherited_name]

            for key, value in env.items():
                if isinstance(value, str):
                    import re
                    def replace_var(match):
                        var_name = match.group(1) or match.group(2)
                        return os.environ.get(var_name, match.group(0))
                    value = re.sub(r'\$\{([^}]+)\}|\$(\w+)', replace_var, value)
                full_env[key] = value

            # Pass log level control to subprocesses
            if not self.verbose:
                full_env["MCP_LOG_LEVEL"] = "WARNING"
            else:
                full_env["MCP_LOG_LEVEL"] = "INFO"

            executable = shutil.which(command) or command

            server_params = StdioServerParameters(
                command=executable,
                args=args,
                env=full_env
            )
            
            transport_ctx = stdio_client(server_params)
            read_stream, write_stream = await self.exit_stack.enter_async_context(transport_ctx)
            
            session = ClientSession(read_stream, write_stream)
            await self.exit_stack.enter_async_context(session)
            await session.initialize()
            
            self.sessions[server_name] = session
            
        else:
            raise ValueError(f"Invalid config for server {server_name}: missing 'command' or 'url'")

    def run_async_from_sync(self, coro):
        """
        Run an async coroutine from a synchronous context.
        Uses the MCP manager's event loop (running in background thread).
        """
        if not self._loop:
            raise RuntimeError("MCP event loop not available. Ensure connect_to_servers was called.")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    async def get_all_tools(self) -> List[MCPTool]:
        """Fetches tools from all connected servers."""
        if not self._servers_ready.is_set():
             self._log("[MCP DEBUG] Warning: Servers not ready when fetching tools", force=True)

        all_tools = []
        self.tools_map.clear()

        self._log(f"[MCP DEBUG] Fetching tools from {len(self.sessions)} connected servers...")

        for server_name, session in self.sessions.items():
            try:
                result = await session.list_tools()
                self._log(f"[MCP DEBUG] Server '{server_name}' provided {len(result.tools)} tools")

                for tool in result.tools:
                    if tool.name in self.tools_map:
                        self._log(f"[MCP DEBUG] WARNING: Tool name collision: {tool.name} in {server_name}", force=True)
                        logger.warning(f"Tool name collision: {tool.name} in {server_name} and {self.tools_map[tool.name]}")

                    self.tools_map[tool.name] = server_name
                    all_tools.append(tool)

            except Exception as e:
                self._log(f"[MCP DEBUG] Error listing tools for {server_name}: {e}", force=True)
                logger.error(f"Error listing tools for {server_name}: {e}")

        self._log(f"[MCP DEBUG] Total tools available: {len(all_tools)}")
        return all_tools

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any],
                           allowed_tools: Optional[List[str]] = None) -> Any:
        """Executes a tool on the appropriate server with very detailed logging.

        Defense-in-depth allow-list enforcement. ``allowed_tools`` is the
        per-request allow-list captured in the agent-build closure in
        ``mcp_client.agent`` and forwarded here AS AN ARGUMENT (never stored on
        the manager: it is a process-wide singleton, so a manager attribute
        would race across concurrent requests, and a ContextVar would not cross
        the run_coroutine_threadsafe thread hop). When ``allowed_tools`` is
        provided, any tool whose name is not allowed -- including the 14
        DENY_ALWAYS filesystem tools -- is refused with PermissionError BEFORE
        ``session.call_tool`` runs. ``allowed_tools=None`` means "no list
        supplied" (internal callers) and is permitted; every agent run forwards
        a concrete list, so that fallback is not hit in production.
        """
        from .tool_policy import is_allowed

        if allowed_tools is not None and not is_allowed(tool_name, allowed_tools):
            self._log(
                f"[MCP SECURITY] BLOCKED '{tool_name}': not in the active "
                f"allow-list for this skill",
                force=True,
            )
            raise PermissionError(
                f"Tool {tool_name} is not in the active allow-list for this skill"
            )

        import json

        self._log("=" * 80)
        self._log(f"[MCP TOOL REQUEST] Tool: {tool_name}")
        self._log(f"[MCP TOOL REQUEST] Server: {self.tools_map.get(tool_name, 'unknown')}")
        self._log(f"[MCP TOOL REQUEST] Timestamp: {__import__('datetime').datetime.now().isoformat()}")
        self._log(f"[MCP TOOL REQUEST] Arguments ({len(arguments)} params):")
        for key, value in arguments.items():
            str_value = str(value)
            if len(str_value) > 200:
                self._log(f"[MCP TOOL REQUEST]   {key}: {str_value[:197]}... ({len(str_value)} chars)")
            else:
                self._log(f"[MCP TOOL REQUEST]   {key}: {str_value}")
        self._log("-" * 80)

        server_name = self.tools_map.get(tool_name)
        if not server_name:
            self._log(f"[MCP TOOL ERROR] Tool '{tool_name}' not found in tools map", force=True)
            self._log(f"[MCP TOOL ERROR] Available tools: {list(self.tools_map.keys())[:5]}...", force=True)
            self._log("=" * 80, force=True)
            raise ValueError(f"Tool {tool_name} not found or not associated with any server.")

        session = self.sessions.get(server_name)
        if not session:
            self._log(f"[MCP TOOL ERROR] Session for server '{server_name}' is not active", force=True)
            self._log(f"[MCP TOOL ERROR] Active sessions: {list(self.sessions.keys())}", force=True)
            self._log("=" * 80, force=True)
            raise RuntimeError(f"Session for server {server_name} is not active.")

        try:
            self._log(f"[MCP TOOL EXEC] Calling {server_name} server...")
            result: CallToolResult = await session.call_tool(tool_name, arguments)
            self._log(f"[MCP TOOL EXEC] Server responded")

            self._log("-" * 80)
            self._log(f"[MCP TOOL RESPONSE] Processing result from {tool_name}")

            total_size = 0
            item_count = 0

            if hasattr(result, 'content') and isinstance(result.content, list):
                self._log(f"[MCP TOOL RESPONSE] Response contains {len(result.content)} content items")

                for idx, item in enumerate(result.content, 1):
                    item_count += 1
                    self._log(f"[MCP TOOL RESPONSE] Item {idx}/{len(result.content)}:")

                    if item.type == 'text':
                        item_size = len(item.text)
                        total_size += item_size

                        if len(item.text) > 300:
                            preview = item.text[:300] + f"... ({len(item.text)} total chars)"
                        else:
                            preview = item.text

                        self._log(f"[MCP TOOL RESPONSE]   Type: text")
                        self._log(f"[MCP TOOL RESPONSE]   Size: {item_size} bytes ({item_size/1024:.2f} KB)")
                        self._log(f"[MCP TOOL RESPONSE]   Content: {preview}")

                    elif item.type == 'image':
                        self._log(f"[MCP TOOL RESPONSE]   Type: image")
                        self._log(f"[MCP TOOL RESPONSE]   MIME: {item.mimeType}")
                        if hasattr(item, 'data'):
                            self._log(f"[MCP TOOL RESPONSE]   Data size: {len(str(item.data))} bytes")

                    elif item.type == 'resource':
                        self._log(f"[MCP TOOL RESPONSE]   Type: resource")
                        self._log(f"[MCP TOOL RESPONSE]   URI: {item.uri}")
                    else:
                        self._log(f"[MCP TOOL RESPONSE]   Type: {item.type}")
                        self._log(f"[MCP TOOL RESPONSE]   Data: {str(item)[:200]}")

            self._log("-" * 80)
            self._log(f"[MCP TOOL SUMMARY] Total items: {item_count}")
            self._log(f"[MCP TOOL SUMMARY] Total text size: {total_size} bytes ({total_size/1024:.2f} KB)")

            if total_size > 10240:
                self._log(f"[MCP TOOL WARNING] ⚠ Large payload: {total_size/1024:.1f} KB", force=True)
                self._log(f"[MCP TOOL WARNING] ⚠ Exceeds OpenAI tracing limit (10KB)", force=True)
                self._log(f"[MCP TOOL WARNING] ⚠ Tracing errors expected but tool execution will succeed", force=True)

            self._log(f"[MCP TOOL SUCCESS] ✓ {tool_name} completed successfully")
            self._log("=" * 80)

            return result

        except Exception as e:
            self._log("-" * 80, force=True)
            self._log(f"[MCP TOOL ERROR] ✗ Execution failed for {tool_name}", force=True)
            self._log(f"[MCP TOOL ERROR] ✗ Error type: {type(e).__name__}", force=True)
            self._log(f"[MCP TOOL ERROR] ✗ Error message: {str(e)}", force=True)

            if hasattr(e, '__dict__'):
                self._log(f"[MCP TOOL ERROR] ✗ Error details: {e.__dict__}", force=True)

            self._log("=" * 80, force=True)
            raise

    async def cleanup(self):
        """Closes all sessions and transports."""
        self._log("[MCP DEBUG] Cleaning up MCP connections...", force=True)
        self._stop_event.set()
        if self._connection_task:
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass

        # Properly close all async contexts in the exit stack
        try:
            await self.exit_stack.aclose()
            self._log("[MCP DEBUG] Exit stack closed successfully", force=True)
        except Exception as e:
            self._log(f"[MCP DEBUG] Error closing exit stack: {e}", force=True)
            logger.error(f"Error closing MCP exit stack: {e}")

        self.sessions.clear()
        self.tools_map.clear()
