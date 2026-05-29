"""File operation tools: read, write, list, search, download, upload."""

from __future__ import annotations

import base64
import os
import tempfile
from datetime import datetime
from pathlib import Path

import winremote.__main__ as _main

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="FileRead",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileRead(path: str, encoding: str = "utf-8") -> str:
    """Read file content. Returns base64 for binary files.

    Args:
        path: File path.
        encoding: Text encoding (default utf-8). Use 'binary' for base64 output.
    """
    try:
        p = _main._check_path(path)
    except ValueError as e:
        return str(e)
    if not p.exists():
        return f"File not found: {path}"
    if encoding == "binary":
        data = p.read_bytes()
        return base64.b64encode(data).decode()
    else:
        with p.open(encoding=encoding, errors="replace") as fh:
            text = fh.read(_main.MAX_FILE_READ_CHARS + 1)
        if len(text) > _main.MAX_FILE_READ_CHARS:
            text = text[:_main.MAX_FILE_READ_CHARS] + "\n\n[... truncated at 100KB]"
        return text


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="FileWrite",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def FileWrite(path: str, content: str, encoding: str = "utf-8", append: bool | str = False) -> str:
    """Write content to a file.

    Args:
        path: File path.
        content: Content to write.
        encoding: Text encoding (default utf-8).
        append: Append instead of overwrite.
    """
    try:
        p = _main._check_path(path)
    except ValueError as e:
        return str(e)
    if len(content) > _main.MAX_WRITE_CHARS:
        return f"FileWrite error: content exceeds {_main.MAX_WRITE_CHARS // 1024 // 1024}MB limit"
    p.parent.mkdir(parents=True, exist_ok=True)
    if _main._tobool(append):
        with open(p, "a", encoding=encoding) as f:
            f.write(content)
    else:
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".winremote_tmp_")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
            os.replace(tmp, p)
        except UnicodeEncodeError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return f"Encoding error writing to {path}: {e}"
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return f"Written {len(content)} chars to {path}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="FileList",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileList(path: str = ".", show_hidden: bool | str = False) -> str:
    """List directory contents with size and modification date.

    Args:
        path: Directory path.
        show_hidden: Include hidden files/folders.
    """
    from tabulate import tabulate
    try:
        p = _main._check_path(path)
    except ValueError as e:
        return str(e)
    if not p.is_dir():
        return f"Not a directory: {path}"

    rows = []
    entries = sorted(os.scandir(p), key=lambda e: e.name)
    show_hidden_flag = _main._tobool(show_hidden)
    for entry in entries:
        name = entry.name
        if not show_hidden_flag and name.startswith("."):
            continue
        try:
            stat = entry.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            kind = "DIR" if entry.is_dir() else "FILE"
            if entry.is_dir():
                size_str = "<DIR>"
            elif size < 1024:
                size_str = f"{size}B"
            elif size < 1048576:
                size_str = f"{size // 1024}KB"
            else:
                size_str = f"{size // 1048576}MB"
            rows.append([kind, name, size_str, mtime])
        except Exception:
            rows.append(["?", name, "?", "?"])

    if not rows:
        return "Directory is empty."
    return tabulate(rows, headers=["Type", "Name", "Size", "Modified"], tablefmt="simple")


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="FileSearch",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileSearch(pattern: str, path: str = ".", recursive: bool | str = True, limit: int = 50) -> str:
    """Search files by name pattern.

    Args:
        pattern: Glob pattern (e.g. '*.py', 'report*').
        path: Root directory to search.
        recursive: Search subdirectories.
        limit: Max results.
    """
    try:
        p = _main._check_path(path)
    except ValueError as e:
        return str(e)
    gen = p.rglob(pattern) if _main._tobool(recursive) else p.glob(pattern)
    matches: list = []
    total = 0
    for m in gen:
        total += 1
        if len(matches) < limit:
            matches.append(m)
        if total > limit + 1:
            break

    if not matches:
        return f"No files matching '{pattern}' in {path}"

    lines = []
    for m in matches:
        try:
            size = m.stat().st_size
            lines.append(f"  {m} ({size} bytes)")
        except Exception:
            lines.append(f"  {m}")

    result = f"Found {total} files"
    if total > limit:
        result += f" (showing first {limit})"
    result += ":\n" + "\n".join(lines)
    return result


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="FileDownload",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def FileDownload(path: str) -> str:
    """Download a file as base64-encoded content. Use for binary files.

    Args:
        path: File path to download.
    """
    try:
        p = _main._check_path(path)
    except ValueError as e:
        return str(e)
    if not p.exists():
        return f"File not found: {path}"
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode()
    return f"base64:{len(data)}bytes:{b64}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="FileUpload",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def FileUpload(path: str, data_base64: str) -> str:
    """Upload a file from base64-encoded content. Use for binary files.

    Args:
        path: Destination file path.
        data_base64: Base64-encoded file content.
    """
    if len(data_base64) > _main.MAX_UPLOAD_B64_BYTES:
        return "FileUpload error: data exceeds maximum size (100MB base64)"
    try:
        p = _main._check_path(path)
    except ValueError as e:
        return str(e)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(data_base64, validate=True)
    p.write_bytes(data)
    return f"Written {len(data)} bytes to {path}"
