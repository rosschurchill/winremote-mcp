"""Adapter isolating all access to fastmcp private internals in one place."""

from __future__ import annotations


def _get_tools_dict(mcp) -> dict:
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_mgr, "_tools", None)
    if isinstance(tools, dict):
        return tools

    # fastmcp 3.x
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if isinstance(components, dict):
        return components

    raise RuntimeError("Unsupported fastmcp internals: cannot locate registered tools")


def list_tool_names(mcp) -> list[str]:
    """Return names of all registered tools."""
    raw = _get_tools_dict(mcp)
    # fastmcp 2.x: keys are plain tool names
    tool_mgr = getattr(mcp, "_tool_manager", None)
    if getattr(tool_mgr, "_tools", None) is raw:
        return list(raw.keys())

    # fastmcp 3.x: keys are "tool:<name>@<hash>"
    names = []
    for k, v in raw.items():
        if not isinstance(k, str) or not k.startswith("tool:"):
            continue
        name = getattr(v, "name", None)
        if not isinstance(name, str) or not name:
            name = k.split(":", 1)[1].split("@", 1)[0]
        names.append(name)
    return names


def get_registered_tools(mcp) -> dict[str, object]:
    """Return {name: tool_object} for all registered tools."""
    raw = _get_tools_dict(mcp)
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    if getattr(tool_mgr, "_tools", None) is raw:
        return dict(raw)

    # fastmcp 3.x
    out: dict[str, object] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.startswith("tool:"):
            continue
        name = getattr(v, "name", None)
        if not isinstance(name, str) or not name:
            name = k.split(":", 1)[1].split("@", 1)[0]
        out[name] = v
    return out


def remove_tool(mcp, name: str) -> None:
    """Remove a tool by name from the mcp instance."""
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_mgr, "_tools", None)
    if isinstance(tools, dict):
        tools.pop(name, None)
        return

    # fastmcp 3.x
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if isinstance(components, dict):
        keys_to_remove = [
            k
            for k, v in components.items()
            if isinstance(k, str)
            and k.startswith("tool:")
            and (
                (getattr(v, "name", None) == name)
                or k.split(":", 1)[1].split("@", 1)[0] == name
            )
        ]
        for k in keys_to_remove:
            components.pop(k, None)


def set_tool_fn(mcp, name: str, fn) -> None:
    """Replace the handler function for a named tool."""
    # fastmcp 2.x
    tool_mgr = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_mgr, "_tools", None)
    if isinstance(tools, dict) and name in tools:
        tool = tools[name]
        if hasattr(tool, "fn"):
            tool.fn = fn
        return

    # fastmcp 3.x
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if isinstance(components, dict):
        for k, v in components.items():
            if not isinstance(k, str) or not k.startswith("tool:"):
                continue
            comp_name = getattr(v, "name", None)
            if not isinstance(comp_name, str) or not comp_name:
                comp_name = k.split(":", 1)[1].split("@", 1)[0]
            if comp_name == name and hasattr(v, "fn"):
                v.fn = fn
                return
