"""
ELF MCP Server - Model Context Protocol server for Emergent Learning Framework.

Exposes ELF's knowledge and recording capabilities to Claude Code agents.
"""

from .server import mcp, main

__all__ = ["mcp", "main"]
