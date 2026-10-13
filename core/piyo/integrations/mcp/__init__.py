"""MCP servers the user adds (Phase 7). Piyo is a client only; `tools.py` says how their tools are offered."""

from piyo.integrations.mcp.client import McpClient, McpError
from piyo.integrations.mcp.config import McpConfigError, McpServer, McpServers, McpTool
from piyo.integrations.mcp.tools import McpManager

__all__ = ["McpClient", "McpConfigError", "McpError", "McpManager", "McpServer", "McpServers", "McpTool"]
