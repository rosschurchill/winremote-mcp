"""Desktop control, window management, clipboard, and UI tools."""

from __future__ import annotations

import base64
import io
import os
import subprocess
import tempfile
import time

# Late-bound import so tests can patch winremote.__main__.desktop, etc.
import winremote.__main__ as _main

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations

from mcp.types import ImageContent, TextContent


# ---------------------------------------------------------------------------
# Desktop control tools
# ---------------------------------------------------------------------------


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Snapshot",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def Snapshot(
    use_vision: bool | str = True,
    quality: int = 75,
    max_width: int = 0,
    monitor: int = 0,
) -> list:
    """Capture desktop screenshot, window list, and interactive UI elements.

    Args:
        use_vision: Include screenshot image (default True).
        quality: JPEG quality 1-100 (default 75). Lower = smaller.
        max_width: Max image width in pixels. 0=native resolution (default). Set to e.g. 1920 to downscale.
        monitor: Monitor to capture. 0=all monitors (default), 1/2/3=specific monitor.

    Returns a list containing:
    - Screenshot image as JPEG (if use_vision=True)
    - Text summary of windows and UI elements
    """
    try:
        parts = []
        use_vision = _main._tobool(use_vision)

        # Screenshot (auto-reconnect session if grab fails)
        if use_vision:
            try:
                b64 = _main.desktop.take_screenshot(quality=quality, max_width=max_width, monitor=monitor)
            except Exception as screenshot_error:
                # Check if a disconnected session is the cause
                reconnect_result = _main._ensure_session_connected()
                if reconnect_result is not None:
                    # Session wasn't disconnected (or reconnect failed) — not a session issue
                    return [f"Snapshot error: {screenshot_error}"]
                # Session was disconnected and reconnected, retry
                try:
                    b64 = _main.desktop.take_screenshot(quality=quality, max_width=max_width, monitor=monitor)
                except Exception as retry_error:
                    return [f"Snapshot error (after session reconnect): {retry_error}"]
            parts.append(ImageContent(type="image", data=b64, mimeType="image/jpeg"))

        # Window list
        windows = _main.desktop.enumerate_windows()
        win_lines = [f"**System Language:** {_main.desktop._get_system_language()}", "", "**Windows:**"]
        for w in windows:
            win_lines.append(f"  [{w.handle}] {w.title} ({w.width}x{w.height} at {w.rect[0]},{w.rect[1]})")

        # Interactive elements from foreground window
        elements = _main.desktop.get_interactive_elements()
        if elements:
            win_lines.append("")
            win_lines.append("**Interactive Elements (foreground window):**")
            for el in elements[:50]:  # limit
                r = el["rect"]
                cx = (r["left"] + r["right"]) // 2
                cy = (r["top"] + r["bottom"]) // 2
                label = el["text"] or el["class"]
                win_lines.append(f"  [{el['index']}] {label} — center ({cx},{cy})")

        parts.append(TextContent(type="text", text="\n".join(win_lines)))
        return parts
    except Exception as e:
        return [f"Snapshot error: {e}"]


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Click",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Click(
    x: int,
    y: int,
    button: str = "left",
    action: str = "click",
) -> str:
    """Mouse click at screen coordinates.

    Args:
        x: X coordinate.
        y: Y coordinate.
        button: 'left', 'right', or 'middle'.
        action: 'click', 'double', or 'hover'.
    """
    try:
        import pyautogui
        if action == "hover":
            pyautogui.moveTo(x, y)
            return f"Hovered at ({x},{y})"
        elif action == "double":
            pyautogui.doubleClick(x, y, button=button)
            return f"Double-clicked {button} at ({x},{y})"
        else:
            pyautogui.click(x, y, button=button)
            return f"Clicked {button} at ({x},{y})"
    except Exception as e:
        return f"Click error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Type",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Type(
    text: str,
    x: int = 0,
    y: int = 0,
    clear: bool | str = False,
    press_enter: bool | str = False,
) -> str:
    """Type text, optionally at specific coordinates.

    Args:
        text: Text to type.
        x: X coordinate (0 = current position).
        y: Y coordinate (0 = current position).
        clear: Clear existing content first (Ctrl+A, Delete).
        press_enter: Press Enter after typing.
    """
    try:
        import pyautogui
        if x or y:
            pyautogui.click(x, y)
            time.sleep(0.1)
        if _main._tobool(clear):
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("delete")
            time.sleep(0.05)
        pyautogui.typewrite(text, interval=0.02) if text.isascii() else pyautogui.write(text)
        if _main._tobool(press_enter):
            pyautogui.press("enter")
        return f"Typed {len(text)} chars"
    except Exception as e:
        return f"Type error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Scroll",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Scroll(
    amount: int,
    x: int = 0,
    y: int = 0,
    horizontal: bool | str = False,
) -> str:
    """Scroll at a position.

    Args:
        amount: Scroll amount (positive=up/right, negative=down/left).
        x: X coordinate (0 = current).
        y: Y coordinate (0 = current).
        horizontal: Horizontal scroll instead of vertical.
    """
    try:
        import pyautogui
        if x or y:
            pyautogui.moveTo(x, y)
        if _main._tobool(horizontal):
            pyautogui.hscroll(amount)
        else:
            pyautogui.scroll(amount)
        direction = "horizontally" if _main._tobool(horizontal) else "vertically"
        return f"Scrolled {amount} {direction}"
    except Exception as e:
        return f"Scroll error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Move",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Move(
    x: int,
    y: int,
    drag: bool | str = False,
    start_x: int = 0,
    start_y: int = 0,
    duration: float = 0.3,
) -> str:
    """Move mouse or drag to position.

    Args:
        x: Target X.
        y: Target Y.
        drag: If true, drag from start position to target.
        start_x: Drag start X.
        start_y: Drag start Y.
        duration: Movement duration in seconds.
    """
    try:
        import pyautogui
        if _main._tobool(drag):
            if start_x or start_y:
                pyautogui.moveTo(start_x, start_y)
            pyautogui.drag(x - pyautogui.position()[0], y - pyautogui.position()[1], duration=duration)
            return f"Dragged to ({x},{y})"
        else:
            pyautogui.moveTo(x, y, duration=duration)
            return f"Moved to ({x},{y})"
    except Exception as e:
        return f"Move error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Shortcut",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Shortcut(keys: str) -> str:
    """Execute keyboard shortcut.

    Args:
        keys: Shortcut string, e.g. 'ctrl+c', 'alt+tab', 'win+e'.
    """
    try:
        import pyautogui
        parts = [k.strip() for k in keys.lower().split("+")]
        pyautogui.hotkey(*parts)
        return f"Executed shortcut: {keys}"
    except Exception as e:
        return f"Shortcut error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Wait",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def Wait(seconds: float = 1.0) -> str:
    """Pause execution.

    Args:
        seconds: Seconds to wait.
    """
    time.sleep(seconds)
    return f"Waited {seconds}s"


# ---------------------------------------------------------------------------
# Window management tools
# ---------------------------------------------------------------------------


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="FocusWindow",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def FocusWindow(title: str = "", handle: int = 0) -> str:
    """Bring a window to the foreground.

    Args:
        title: Window title (fuzzy matched).
        handle: Window handle (exact).
    """
    err = _main._check_win32("FocusWindow")
    if err:
        return err
    try:
        return _main.desktop.focus_window(title=title or None, handle=handle or None)
    except Exception as e:
        return f"FocusWindow error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="MinimizeAll",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def MinimizeAll() -> str:
    """Minimize all windows (Win+D — show desktop)."""
    try:
        return _main.desktop.minimize_all()
    except Exception as e:
        return f"MinimizeAll error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="App",
        destructiveHint=False,
        openWorldHint=True,
    )
)
def App(
    action: str = "launch",
    name: str = "",
    args: str = "",
    handle: int = 0,
    width: int = 0,
    height: int = 0,
) -> str:
    """Launch, switch to, or resize an application.

    Args:
        action: 'launch', 'switch', or 'resize'.
        name: Application name or path (for launch/switch).
        args: Arguments (for launch).
        handle: Window handle (for resize/switch).
        width: New width (for resize).
        height: New height (for resize).
    """
    try:
        if action == "launch":
            return _main.desktop.launch_app(name, args)
        elif action == "switch":
            err = _main._check_win32("App(switch)")
            if err:
                return err
            return _main.desktop.focus_window(title=name or None, handle=handle or None)
        elif action == "resize":
            err = _main._check_win32("App(resize)")
            if err:
                return err
            if not handle:
                return "resize requires a window handle"
            return _main.desktop.resize_window(handle, width, height)
        return f"Unknown action: {action}"
    except Exception as e:
        return f"App error: {e}"


# ---------------------------------------------------------------------------
# Clipboard tools
# ---------------------------------------------------------------------------


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="GetClipboard",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetClipboard() -> str:
    """Read the Windows clipboard text content."""
    err = _main._check_win32("GetClipboard")
    if err:
        return err
    try:
        return _main.desktop.get_clipboard()
    except Exception as e:
        return f"GetClipboard error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="SetClipboard",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def SetClipboard(text: str) -> str:
    """Set the Windows clipboard text content.

    Args:
        text: Text to place on clipboard.
    """
    err = _main._check_win32("SetClipboard")
    if err:
        return err
    try:
        return _main.desktop.set_clipboard(text)
    except Exception as e:
        return f"SetClipboard error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="LockScreen",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def LockScreen() -> str:
    """Lock the Windows workstation."""
    try:
        return _main.desktop.lock_screen()
    except Exception as e:
        return f"LockScreen error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Notification",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def Notification(title: str = "winremote-mcp", message: str = "") -> str:
    """Show a Windows toast notification.

    Args:
        title: Notification title.
        message: Notification body text.
    """
    try:
        return _main.desktop.show_notification(title, message)
    except Exception as e:
        return f"Notification error: {e}"


# ---------------------------------------------------------------------------
# Screen recording and OCR tools
# ---------------------------------------------------------------------------


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="ScreenRecord",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ScreenRecord(
    duration: float = 3.0,
    fps: int = 5,
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    max_width: int = 800,
) -> list:
    """Record the screen and return an animated GIF.

    Args:
        duration: Recording length in seconds (default 3, max 10).
        fps: Frames per second (default 5, max 10).
        left: Left edge of capture region (0 = full screen).
        top: Top edge of capture region.
        right: Right edge of capture region.
        bottom: Bottom edge of capture region.
        max_width: Max width of output GIF (default 800).
    """
    from winremote import recording
    try:
        fps = min(max(fps, 1), 10)
        region = {}
        if left or top or right or bottom:
            region = {"left": left, "top": top, "right": right, "bottom": bottom}
        b64 = recording.record_screen(duration=duration, fps=fps, max_width=max_width, **region)
        size_kb = (len(b64) * 3 // 4) // 1024
        return [
            ImageContent(type="image", data=b64, mimeType="image/gif"),
            TextContent(
                type="text",
                text=f"Recorded {duration}s at {fps}fps ({size_kb}KB GIF)",
            ),
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"ScreenRecord error: {e}")]


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="OCR",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def OCR(
    left: int = 0,
    top: int = 0,
    right: int = 0,
    bottom: int = 0,
    lang: str = "eng",
) -> str:
    """Extract text from screen using OCR. Captures a region or the full screen.

    Uses pytesseract if available, falls back to Windows built-in OCR engine.

    Args:
        left: Left edge of region (0 = full screen).
        top: Top edge of region.
        right: Right edge of region.
        bottom: Bottom edge of region.
        lang: OCR language for pytesseract (default 'eng').
    """
    from winremote import ocr
    try:
        region = {}
        if left or top or right or bottom:
            region = {"left": left, "top": top, "right": right, "bottom": bottom}
        text = ocr.run_ocr(**region, lang=lang) if region else ocr.run_ocr(lang=lang)
        if not text:
            return "(no text detected)"
        return text
    except ImportError as e:
        return f"OCR error: {e}"
    except Exception as e:
        return f"OCR error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="AnnotatedSnapshot",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def AnnotatedSnapshot(
    max_elements: int = 30,
    quality: int = 75,
    max_width: int = 0,
) -> list:
    """Take a screenshot with numbered labels on interactive UI elements.

    Draws red rectangles and white numbered labels on each interactive element,
    making it easy for AI agents to identify click targets visually.

    Args:
        max_elements: Maximum number of elements to annotate (default 30).
        quality: JPEG quality 1-100 (default 75).
        max_width: Max image width in pixels. 0=native resolution (default).
    """
    try:
        from PIL import ImageDraw, ImageFont, ImageGrab  # noqa: F401

        # Take screenshot (auto-reconnect session if grab fails)
        try:
            img = _main.desktop.grab_screenshot_with_reconnect()
        except Exception as screenshot_error:
            return [TextContent(type="text", text=f"AnnotatedSnapshot error: {screenshot_error}")]
        native_width = img.width
        if max_width > 0 and img.width > max_width:
            ratio = max_width / img.width
            from PIL import Image as PILImage
            img = img.resize((max_width, int(img.height * ratio)), resample=PILImage.Resampling.LANCZOS)

        # Get interactive elements
        elements = _main.desktop.get_interactive_elements()
        if not elements:
            # Return screenshot with no annotations
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return [
                ImageContent(type="image", data=b64, mimeType="image/jpeg"),
                TextContent(type="text", text="No interactive elements found."),
            ]

        draw = ImageDraw.Draw(img)

        # Try to get a font
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            font = ImageFont.load_default()

        # Scale factor if image was resized
        scale = img.width / native_width if img.width != native_width else 1.0

        element_lines = []
        for el in elements[:max_elements]:
            idx = el["index"]
            r = el["rect"]
            x1 = int(r["left"] * scale)
            y1 = int(r["top"] * scale)
            x2 = int(r["right"] * scale)
            y2 = int(r["bottom"] * scale)

            # Draw red rectangle
            draw.rectangle([x1, y1, x2, y2], outline="red", width=2)

            # Draw label background + number
            label = str(idx)
            bbox = font.getbbox(label)
            lw = bbox[2] - bbox[0] + 6
            lh = bbox[3] - bbox[1] + 4
            draw.rectangle([x1, y1 - lh - 2, x1 + lw, y1 - 2], fill="red")
            draw.text((x1 + 3, y1 - lh - 1), label, fill="white", font=font)

            # Build text description
            cx = (r["left"] + r["right"]) // 2
            cy = (r["top"] + r["bottom"]) // 2
            name = el["text"] or el["class"]
            element_lines.append(f"  [{idx}] {name} — center ({cx},{cy})")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        b64 = base64.b64encode(buf.getvalue()).decode()

        text_summary = f"**Annotated {len(element_lines)} elements:**\n" + "\n".join(element_lines)
        return [
            ImageContent(type="image", data=b64, mimeType="image/jpeg"),
            TextContent(type="text", text=text_summary),
        ]
    except Exception as e:
        return [TextContent(type="text", text=f"AnnotatedSnapshot error: {e}")]
