"""Network tools: Ping, PortCheck, NetConnections."""

from __future__ import annotations

import winremote.__main__ as _main

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Ping",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def Ping(host: str, count: int = 4) -> str:
    """Ping a host.

    Args:
        host: Hostname or IP address.
        count: Number of ping requests (default 4).
    """
    from winremote import network
    return network.ping(host, count)


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="PortCheck",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def PortCheck(host: str, port: int, timeout: float = 5.0) -> str:
    """Check if a TCP port is open.

    Args:
        host: Hostname or IP address.
        port: Port number.
        timeout: Connection timeout in seconds (default 5).
    """
    from winremote import network
    return network.port_check(host, port, timeout)


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="NetConnections",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def NetConnections(filter_str: str = "", limit: int = 50) -> str:
    """List network connections.

    Args:
        filter_str: Filter connections by local/remote address, status, or PID.
        limit: Maximum number of connections to return (default 50).
    """
    from winremote import network
    return network.net_connections(filter_str, limit=limit)
