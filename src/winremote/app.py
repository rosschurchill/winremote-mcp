"""winremote-mcp — FastMCP instance, shared constants, and helper utilities."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, urlunparse

try:
    import pyautogui

    PYAUTOGUI_IMPORT_ERROR: Exception | None = None
except Exception as e:  # pragma: no cover - environment-specific import failure
    pyautogui = None
    PYAUTOGUI_IMPORT_ERROR = e

from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent  # noqa: F401 — re-exported for tool modules

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations  # noqa: F401

from winremote import desktop  # noqa: F401 — re-exported so tests can patch winremote.app.desktop
from winremote.desktop import ensure_session_connected as _ensure_session_connected  # noqa: F401
from winremote.security import _resolve_and_validate, validate_fetch_url

if pyautogui is not None:
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SOUND_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_SCRAPE_RESPONSE_BYTES = 1024 * 1024
MAX_SCRAPE_MD_CHARS = 50_000
MAX_FILE_READ_CHARS = 100_000
MAX_UPLOAD_B64_BYTES = 100 * 1024 * 1024  # base64-encoded limit (~75 MiB after decode)
WINDOW_MATCH_MIN_SCORE = 50
PROCESS_FILTER_MIN_SCORE = 60
PROCESS_KILL_MIN_SCORE = 80

# Sandboxed file root — all file operations are restricted to this directory tree.
# Set to Path.home() by default; overridden at startup via --file-root or config.
_FILE_ROOT: Path = Path.home()

# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "winremote-mcp",
    instructions=(
        "Windows Remote MCP Server. Provides desktop control, window management, "
        "shell execution, file operations, network tools, registry, services, "
        "and system management tools for a Windows machine."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tobool(v: bool | str) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def _require_pyautogui(tool_name: str = "This tool") -> str | None:
    """Return an error string if pyautogui is unavailable, else None."""
    if pyautogui is not None:
        return None
    detail = f" ({PYAUTOGUI_IMPORT_ERROR})" if PYAUTOGUI_IMPORT_ERROR else ""
    return (
        f"Error: pyautogui is unavailable — {tool_name} requires an interactive desktop session"
        f" with GUI support{detail}."
    )


def _check_win32(tool_name: str = "This tool") -> str | None:
    """Return an error string if pywin32 is unavailable, else None."""
    pyautogui_error = _require_pyautogui(tool_name)
    if pyautogui_error:
        return pyautogui_error
    if not desktop.HAS_WIN32:
        return f"Error: pywin32 not installed — {tool_name} requires it. Run `pip install pywin32` on the Windows host."
    return None


def _check_path(path: str) -> Path:
    """Resolve path and verify it is within the sandboxed file root.

    Raises ValueError if the resolved path escapes _FILE_ROOT.
    """
    import winremote.app as _app_module

    resolved = Path(path).expanduser().resolve()
    root = _app_module._FILE_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"Path '{path}' is outside the permitted file root '{root}'")
    return resolved


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib handler that rejects automatic redirects after surfacing target URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, "Redirects are not allowed", headers, fp)


def _open_validated_fetch_url(url: str, *, headers: dict[str, str] | None = None, timeout: int = 15):
    """Open a validated URL, connecting to the pre-resolved IP to prevent DNS rebinding.

    Resolves the hostname once, validates all returned IPs, then connects directly
    to the pinned IP address with the original hostname in the Host header. This
    eliminates the TOCTOU window between validation and the actual connection.
    """
    allowed, reason, pinned_ip = _resolve_and_validate(url)
    if not allowed:
        raise ValueError(reason)

    # Build URL with pinned IP so urllib never re-resolves the hostname
    parsed = urlparse(url)
    netloc_with_ip = pinned_ip if not parsed.port else f"{pinned_ip}:{parsed.port}"
    pinned_url = urlunparse(parsed._replace(netloc=netloc_with_ip))
    req = urllib.request.Request(pinned_url, headers={"Host": parsed.hostname, **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            location = exc.headers.get("Location", "") if exc.headers else ""
            if location:
                redirected = urllib.request.urljoin(url, location)
                redirect_allowed, redirect_reason = validate_fetch_url(redirected)
                if not redirect_allowed:
                    raise ValueError(f"redirect blocked: {redirect_reason}") from exc
            raise ValueError("redirects are not allowed") from exc
        raise
