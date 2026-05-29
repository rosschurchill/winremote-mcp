"""Win32 desktop interactions — screenshots, window enumeration, UI elements."""

from __future__ import annotations

import base64
import ctypes
import io
import locale
import logging
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger("winremote.desktop")

try:
    import pyautogui

    PYAUTOGUI_IMPORT_ERROR: Exception | None = None
except Exception as e:  # pragma: no cover - environment-specific import failure
    pyautogui = None
    PYAUTOGUI_IMPORT_ERROR = e

# Win32 imports (will fail on non-Windows — caught at tool level)
try:
    import win32api  # noqa: F401
    import win32clipboard
    import win32con
    import win32gui
    import win32process

    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

from PIL import Image as PILImage
from PIL import ImageGrab

# Enable DPI awareness so screenshots capture native resolution (e.g. 4K)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_pyautogui() -> None:
    if pyautogui is None:
        detail = f" ({PYAUTOGUI_IMPORT_ERROR})" if PYAUTOGUI_IMPORT_ERROR else ""
        raise RuntimeError("pyautogui is unavailable; desktop control requires an interactive GUI session" + detail)


def _tobool(v: bool | str) -> bool:
    """Handle MCP's bool-as-string quirk."""
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


_SYSTEM_LANGUAGE: str | None = None


def _get_system_language() -> str:
    """Return current Windows display language (cached after first call)."""
    global _SYSTEM_LANGUAGE
    if _SYSTEM_LANGUAGE is None:
        try:
            _SYSTEM_LANGUAGE = locale.getdefaultlocale()[0] or "en_US"
        except Exception:
            _SYSTEM_LANGUAGE = "en_US"
    return _SYSTEM_LANGUAGE


# ---------------------------------------------------------------------------
# Window info
# ---------------------------------------------------------------------------


@dataclass
class WindowInfo:
    handle: int
    title: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    visible: bool
    pid: int = 0

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def enumerate_windows() -> list[WindowInfo]:
    """List all visible top-level windows."""
    if not HAS_WIN32:
        raise RuntimeError("pywin32 not installed — run `pip install pywin32`")
    results: list[WindowInfo] = []

    def _cb(hwnd: int, _extra: None) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        rect = win32gui.GetWindowRect(hwnd)
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        results.append(WindowInfo(handle=hwnd, title=title, rect=rect, visible=True, pid=pid))
        return True

    win32gui.EnumWindows(_cb, None)
    return results


def get_interactive_elements() -> list[dict]:
    """Simplified accessibility tree — enumerate child windows with class/text."""
    if not HAS_WIN32:
        raise RuntimeError("pywin32 not installed — run `pip install pywin32`")
    fg = win32gui.GetForegroundWindow()
    if not fg:
        return []
    elements: list[dict] = []
    idx = [0]

    def _cb(hwnd: int, _extra: None) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd)
        text = win32gui.GetWindowText(hwnd)
        try:
            rect = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        idx[0] += 1
        elements.append(
            {
                "index": idx[0],
                "class": cls,
                "text": text,
                "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
            }
        )
        return True

    try:
        win32gui.EnumChildWindows(fg, _cb, None)
    except Exception:
        pass
    return elements


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def _get_monitor_bbox(monitor: int) -> tuple[int, int, int, int] | None:
    """Get bounding box for a specific monitor (1-indexed). Returns None for all monitors."""
    if monitor <= 0:
        return None  # all monitors
    try:
        if HAS_WIN32:
            monitors = win32api.EnumDisplayMonitors()
            if monitor <= len(monitors):
                _hmon, _hdc, rect = monitors[monitor - 1]
                return (rect[0], rect[1], rect[2], rect[3])
            raise IndexError(f"Monitor {monitor} not found (have {len(monitors)})")
        else:
            raise RuntimeError("pywin32 needed for specific monitor selection")
    except Exception:
        raise


def take_screenshot(quality: int = 75, max_width: int = 0, monitor: int = 0) -> str:
    """Capture screen, return base64 JPEG. Resizes if wider than max_width.

    Args:
        quality: JPEG quality 1-100.
        max_width: Max width in pixels. 0=no resize (native resolution).
        monitor: 0=all monitors, 1/2/3=specific monitor.
    """
    if monitor == 0:
        img = ImageGrab.grab(all_screens=True)
    else:
        bbox = _get_monitor_bbox(monitor)
        img = ImageGrab.grab(bbox=bbox)
    # Resize if needed
    if max_width > 0 and img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), resample=PILImage.Resampling.LANCZOS)
    # Convert to JPEG
    if img.mode in ("RGBA", "LA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------


def focus_window(title: str | None = None, handle: int | None = None) -> str:
    """Bring a window to the foreground. Fuzzy-match title if provided."""
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"

    hwnd = None
    if handle:
        hwnd = handle
    elif title:
        from thefuzz import fuzz

        best_score = 0
        for w in enumerate_windows():
            score = fuzz.partial_ratio(title.lower(), w.title.lower())
            if score > best_score:
                best_score = score
                hwnd = w.handle
        if best_score < 50:
            return f"No window matching '{title}' (best score {best_score})"

    if not hwnd:
        return "No window found"

    try:
        # Restore if minimized
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        actual_fg = win32gui.GetForegroundWindow()
        title_str = win32gui.GetWindowText(hwnd)
        if actual_fg != hwnd:
            return f"Warning: focus request sent but window {hwnd} may not have focus (foreground is {actual_fg})"
        return f"Focused window handle={hwnd} title='{title_str}'"
    except Exception as e:
        return f"Failed to focus: {e}"


def minimize_all() -> str:
    """Win+D — show desktop."""
    try:
        _require_pyautogui()
        pyautogui.hotkey("win", "d")
        return "Minimized all windows"
    except Exception as e:
        return f"Failed: {e}"


def launch_app(name: str, args: str = "") -> str:
    """Launch application via PowerShell Start-Process."""
    import subprocess

    try:
        safe_name = name.replace("'", "''")
        cmd = f"Start-Process '{safe_name}'"
        if args:
            safe_args = args.replace("'", "''")
            cmd += f" -ArgumentList '{safe_args}'"
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], timeout=10, capture_output=True)
        return f"Launched {name}"
    except Exception as e:
        return f"Failed to launch {name}: {e}"


def resize_window(handle: int, width: int, height: int) -> str:
    """Resize a window by handle."""
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"
    try:
        rect = win32gui.GetWindowRect(handle)
        win32gui.MoveWindow(handle, rect[0], rect[1], width, height, True)
        return f"Resized {handle} to {width}x{height}"
    except Exception as e:
        return f"Failed: {e}"


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------


def _open_clipboard(retries: int = 10, delay: float = 0.01) -> None:
    """Open the Windows clipboard with retry backoff (another process may hold the lock)."""
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
    win32clipboard.OpenClipboard()  # final attempt — let it raise if still locked


def get_clipboard() -> str:
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"
    try:
        _open_clipboard()
        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return data
    except Exception as e:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return f"Error: {e}"


def set_clipboard(text: str) -> str:
    if not HAS_WIN32:
        return "Error: pywin32 not installed — run `pip install pywin32`"
    try:
        _open_clipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return "Clipboard set"
    except Exception as e:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Lock screen
# ---------------------------------------------------------------------------


def lock_screen() -> str:
    try:
        ctypes.windll.user32.LockWorkStation()
        return "Screen locked"
    except Exception as e:
        return f"Failed: {e}"


# ---------------------------------------------------------------------------
# Toast notification
# ---------------------------------------------------------------------------


def show_notification(title: str, message: str) -> str:
    """Show a Windows toast notification via PowerShell."""
    import subprocess

    # Pass title/message as bound parameters (-Title, -Message) so user-supplied
    # text never touches the PowerShell source — only data, never code.
    # [System.Security.SecurityElement]::Escape() handles XML-special chars inside PS.
    ps = """
param($Title, $Message)
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$safe_title = [System.Security.SecurityElement]::Escape($Title)
$safe_msg   = [System.Security.SecurityElement]::Escape($Message)
$xml_str = '<toast><visual><binding template="ToastGeneric"><text>' + $safe_title + '</text><text>' + $safe_msg + '</text></binding></visual></toast>'
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xml_str)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("winremote-mcp").Show($toast)
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps, "-Title", title, "-Message", message],
            timeout=10,
            capture_output=True,
        )
        return "Notification shown"
    except Exception as e:
        return f"Failed: {e}"


def grab_screenshot_with_reconnect():
    """Grab a PIL screenshot, reconnecting the desktop session once on failure."""
    try:
        return ImageGrab.grab()
    except Exception as first_err:
        reconnect_err = ensure_session_connected()
        if reconnect_err is not None:
            raise RuntimeError(str(first_err)) from first_err
        return ImageGrab.grab()


def ensure_session_connected(force: bool = False) -> str | None:
    """Reconnect a disconnected desktop session to console.

    Returns None on success or if already connected, error string on failure.
    Tries win32ts.WTSEnumerateSessions first (locale-independent); falls back
    to the 'query session' text parser when pywin32 is unavailable.

    The 'query session' output has fixed-width columns:
      SESSIONNAME  USERNAME  ID  STATE  TYPE  DEVICE
    State values: Active, Disc (disconnected), Listen, Idle
    Chinese Windows: 已断开 / 运行中 for Disc / Active
    """
    try:
        user_session_id = None
        is_disconnected = False

        # Preferred: win32ts API — WTSDisconnected == state 4 regardless of locale
        try:
            import win32ts

            WTS_DISCONNECTED = 4
            sessions = win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE)
            for sess in sessions:
                sid = sess["SessionId"]
                state = sess["State"]
                name = sess.get("WinStationName", "").lower()
                if name in ("services", "rdp-tcp", ""):
                    continue
                logger.debug("win32ts session id=%s name=%s state=%s", sid, name, state)
                user_session_id = sid
                is_disconnected = state == WTS_DISCONNECTED
                break
        except Exception:
            # Fall back to text parser when win32ts is not available
            result = subprocess.run(
                ["query", "session"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if result.returncode != 0:
                return f"Failed to query sessions: {result.stderr}"

            lines = result.stdout.strip().split("\n")
            for line in lines[1:]:
                line = line.lstrip(">").strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 4:
                    continue
                name = parts[0].lower()
                if name in ("services", "rdp-tcp"):
                    continue
                for i, p in enumerate(parts[1:], 1):
                    if p.isdigit():
                        sid = int(p)
                        if i + 1 < len(parts):
                            state = parts[i + 1].lower()
                            has_user = i > 1 and not parts[i - 1].isdigit()
                            if has_user or name == "console":
                                user_session_id = sid
                                is_disconnected = state in ("disc", "断开", "已断开", "disconnected")
                                logger.debug(
                                    "query session parsed id=%s state=%s disconnected=%s",
                                    sid,
                                    state,
                                    is_disconnected,
                                )
                        break

        if user_session_id is None:
            return "No user session found"

        if not is_disconnected and not force:
            return None  # Already connected

        result = subprocess.run(
            ["tscon", str(user_session_id), "/dest:console"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return f"tscon failed: {err}"
        time.sleep(1)
        return None
    except subprocess.TimeoutExpired:
        return "Session reconnect timed out"
    except Exception as e:
        return f"Session reconnect error: {e}"
