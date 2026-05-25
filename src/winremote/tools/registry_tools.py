"""Registry tools: RegRead, RegWrite."""

from __future__ import annotations

import winremote.__main__ as _main

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="RegRead",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def RegRead(key: str, value_name: str) -> str:
    """Read a Windows registry value.

    Args:
        key: Registry key path, e.g. "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion".
        value_name: Name of the value to read.
    """
    from winremote import registry
    try:
        return registry.reg_read(key, value_name)
    except Exception as e:
        return f"RegRead error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="RegWrite",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def RegWrite(key: str, value_name: str, data: str, reg_type: str = "REG_SZ") -> str:
    """Write a Windows registry value.

    Args:
        key: Registry key path, e.g. "HKCU\\SOFTWARE\\MyApp".
        value_name: Name of the value to write.
        data: Value data. For REG_DWORD/REG_QWORD pass as string number. For REG_MULTI_SZ use | separator.
        reg_type: Registry type: REG_SZ, REG_EXPAND_SZ, REG_DWORD, REG_QWORD, REG_BINARY, REG_MULTI_SZ.
    """
    from winremote import registry
    try:
        return registry.reg_write(key, value_name, data, reg_type)
    except Exception as e:
        return f"RegWrite error: {e}"
