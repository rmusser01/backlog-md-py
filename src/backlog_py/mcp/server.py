from __future__ import annotations

from typing import Any

from backlog_py.mcp.stdio_server import SdkFreeMcpServer, main, run_stdio


def is_mcp_sdk_available() -> bool:
    """Return False because backlog-md-py no longer uses the Python MCP SDK."""
    return False


def create_server(*_legacy_args: Any, **_legacy_kwargs: Any) -> SdkFreeMcpServer:
    """Create the SDK-free MCP stdio server facade.

    Ignored positional and keyword arguments are accepted for backwards
    compatibility with older embedding code that supplied an SDK adapter.
    """
    return SdkFreeMcpServer()


__all__ = ["SdkFreeMcpServer", "create_server", "is_mcp_sdk_available", "main", "run_stdio"]
