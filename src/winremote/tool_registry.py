"""Single source of truth for tool metadata (tier + concurrency category)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolMeta:
    tier: int
    category: str  # matches ToolCategory enum values in taskmanager


TOOL_REGISTRY: dict[str, ToolMeta] = {
    # Tier 1 — always on
    "Snapshot":         ToolMeta(tier=1, category="desktop"),
    "AnnotatedSnapshot": ToolMeta(tier=1, category="desktop"),
    "OCR":              ToolMeta(tier=1, category="desktop"),
    "ScreenRecord":     ToolMeta(tier=1, category="desktop"),
    "GetClipboard":     ToolMeta(tier=1, category="query"),
    "GetSystemInfo":    ToolMeta(tier=1, category="query"),
    "ListProcesses":    ToolMeta(tier=1, category="query"),
    "FileList":         ToolMeta(tier=1, category="file"),
    "FileSearch":       ToolMeta(tier=1, category="file"),
    "RegRead":          ToolMeta(tier=1, category="query"),
    "ServiceList":      ToolMeta(tier=1, category="query"),
    "TaskList":         ToolMeta(tier=1, category="query"),
    "EventLog":         ToolMeta(tier=1, category="query"),
    "Ping":             ToolMeta(tier=1, category="network"),
    "PortCheck":        ToolMeta(tier=1, category="network"),
    "NetConnections":   ToolMeta(tier=1, category="network"),
    "Notification":     ToolMeta(tier=1, category="query"),
    "Wait":             ToolMeta(tier=1, category="query"),
    "GetTaskStatus":    ToolMeta(tier=1, category="query"),
    "GetRunningTasks":  ToolMeta(tier=1, category="query"),
    # Tier 2 — enabled by default, disable-able
    "Click":            ToolMeta(tier=2, category="desktop"),
    "Type":             ToolMeta(tier=2, category="desktop"),
    "Move":             ToolMeta(tier=2, category="desktop"),
    "Scroll":           ToolMeta(tier=2, category="desktop"),
    "Shortcut":         ToolMeta(tier=2, category="desktop"),
    "FocusWindow":      ToolMeta(tier=2, category="desktop"),
    "MinimizeAll":      ToolMeta(tier=2, category="desktop"),
    "Scrape":           ToolMeta(tier=2, category="shell"),
    "CancelTask":       ToolMeta(tier=2, category="query"),
    "ReconnectSession": ToolMeta(tier=2, category="query"),
    # Tier 3 — opt-in only
    "Shell":            ToolMeta(tier=3, category="shell"),
    "App":              ToolMeta(tier=3, category="desktop"),
    "PlaySound":        ToolMeta(tier=3, category="desktop"),
    "FileRead":         ToolMeta(tier=3, category="file"),
    "FileWrite":        ToolMeta(tier=3, category="file"),
    "FileDownload":     ToolMeta(tier=3, category="file"),
    "FileUpload":       ToolMeta(tier=3, category="file"),
    "KillProcess":      ToolMeta(tier=3, category="query"),
    "RegWrite":         ToolMeta(tier=3, category="query"),
    "ServiceStart":     ToolMeta(tier=3, category="query"),
    "ServiceStop":      ToolMeta(tier=3, category="query"),
    "TaskCreate":       ToolMeta(tier=3, category="query"),
    "TaskDelete":       ToolMeta(tier=3, category="query"),
    "SetClipboard":     ToolMeta(tier=3, category="query"),
    "LockScreen":       ToolMeta(tier=3, category="desktop"),
}
