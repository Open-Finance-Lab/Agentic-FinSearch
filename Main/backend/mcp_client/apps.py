import logging
import asyncio
import os
import threading
from django.apps import AppConfig

logger = logging.getLogger(__name__)

_global_mcp_manager = None
_global_mcp_lock = threading.Lock()
_initialized = False

def get_global_mcp_manager():
    """Get the global MCP manager instance"""
    return _global_mcp_manager

def set_global_mcp_manager(manager):
    """Set the global MCP manager instance"""
    global _global_mcp_manager
    _global_mcp_manager = manager

class MCPClientConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mcp_client'

    def ready(self):
        """
        Initialize global MCP manager on Django startup.
        This ensures all MCP connections are ready when the backend starts.
        """
        global _initialized

        if _initialized:
            return

        import os
        import sys

        # Skip initialization for management commands like collectstatic, migrate, etc.
        is_manage_cmd = 'manage.py' in sys.argv[0] or (len(sys.argv) > 0 and sys.argv[0].endswith('manage.py'))
        is_server_cmd = 'runserver' in sys.argv

        if is_manage_cmd and not is_server_cmd:
            if any(cmd in sys.argv for cmd in ['collectstatic', 'migrate', 'makemigrations', 'shell', 'test']):
                logger.debug(f"Skipping MCP init for management command: {sys.argv}")
                return

        _initialized = True

        from .mcp_manager import MCPClientManager

        async def initialize_global_manager():
            """Initialize and maintain the global MCP manager"""
            global _global_mcp_manager

            try:
                mcp_verbose = os.getenv("MCP_VERBOSE", "false").lower() in ("true", "1")
                manager = MCPClientManager(verbose=mcp_verbose)

                config = await manager.load_config()
                server_count = len(config.get("mcpServers", {}))

                if server_count > 0:
                    await manager.connect_to_servers()
                    tools = await manager.get_all_tools()

                    with _global_mcp_lock:
                        set_global_mcp_manager(manager)

                    logger.info(f"MCP ready ({len(tools)} tools from {server_count} servers)")
                else:
                    logger.info("No MCP servers configured")

            except Exception as e:
                logger.error(f"MCP initialization failed: {e}", exc_info=True)

        def run_async_init():
            """Run MCP initialization and keep event loop alive for async operations"""
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(initialize_global_manager())
                loop.run_forever()
            except Exception as e:
                logger.error(f"Error in MCP initialization thread: {e}", exc_info=True)
            finally:
                try:
                    loop.close()
                except Exception as e:
                    logger.error(f"Error closing event loop: {e}")

        thread = threading.Thread(target=run_async_init, name="MCPManagerThread", daemon=True)
        thread.start()
