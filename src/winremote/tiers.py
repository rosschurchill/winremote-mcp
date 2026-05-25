"""Tool tier definitions and access control."""

from __future__ import annotations

from winremote.fastmcp_compat import get_registered_tools, remove_tool
from winremote.tool_registry import TOOL_REGISTRY

TOOL_TIERS: dict[str, set[str]] = {"tier1": set(), "tier2": set(), "tier3": set()}
for _name, _meta in TOOL_REGISTRY.items():
    TOOL_TIERS[f"tier{_meta.tier}"].add(_name)

ALL_TOOLS = TOOL_TIERS["tier1"] | TOOL_TIERS["tier2"] | TOOL_TIERS["tier3"]
_NAME_LOOKUP = {name.lower(): name for name in ALL_TOOLS}


def parse_tool_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def normalize_tool_names(tool_names: list[str]) -> list[str]:
    normalized = []
    unknown = []
    for name in tool_names:
        hit = _NAME_LOOKUP.get(name.lower())
        if hit:
            normalized.append(hit)
        else:
            unknown.append(name)
    if unknown:
        allowed = ", ".join(sorted(ALL_TOOLS))
        raise ValueError(f"Unknown tools: {', '.join(unknown)}. Allowed tools: {allowed}")
    return normalized


def resolve_enabled_tools(
    *,
    enable_tier3: bool = False,
    disable_tier2: bool = False,
    enable_all: bool = False,
    explicit_tools: list[str] | None = None,
    exclude_tools: list[str] | None = None,
) -> set[str]:
    """Resolve active tools.

    Precedence: explicit tools > tier toggles.
    """
    explicit_tools = explicit_tools or []
    exclude_tools = exclude_tools or []

    if explicit_tools:
        enabled = set(normalize_tool_names(explicit_tools))
    elif enable_all:
        enabled = set(ALL_TOOLS)
    else:
        enabled = set(TOOL_TIERS["tier1"])
        if not disable_tier2:
            enabled |= TOOL_TIERS["tier2"]
        if enable_tier3:
            enabled |= TOOL_TIERS["tier3"]

    if exclude_tools:
        enabled -= set(normalize_tool_names(exclude_tools))

    return enabled


def get_tier_names(enabled_tools: set[str]) -> list[str]:
    enabled_tiers = []
    if TOOL_TIERS["tier1"] & enabled_tools:
        enabled_tiers.append("1")
    if TOOL_TIERS["tier2"] & enabled_tools:
        enabled_tiers.append("2")
    if TOOL_TIERS["tier3"] & enabled_tools:
        enabled_tiers.append("3")
    return enabled_tiers


def _get_registered_tools(mcp) -> dict[str, object]:
    return get_registered_tools(mcp)


def filter_tools(mcp, enabled_tools: set[str]) -> dict[str, int]:
    all_tools = list(get_registered_tools(mcp).keys())
    total_count = len(all_tools)
    for name in all_tools:
        if name not in enabled_tools:
            remove_tool(mcp, name)
    return {"enabled": len(enabled_tools), "disabled": total_count - len(enabled_tools), "total": total_count}
