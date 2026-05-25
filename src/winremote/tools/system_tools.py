"""System tools: Shell, processes, tasks, services, sound, scrape, event log."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time

import winremote.__main__ as _main

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations

from mcp.types import TextContent


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Shell",
        destructiveHint=True,
        openWorldHint=True,
    )
)
def Shell(command: str, timeout: int = 30, cwd: str = "") -> str:
    """Execute a PowerShell command.

    Args:
        command: PowerShell command to execute.
        timeout: Timeout in seconds (default 30).
        cwd: Working directory. If provided, the command runs inside that directory.
    """
    from winremote.taskmanager import get_current_cancel_event
    try:
        if cwd:
            safe_cwd = cwd.replace("'", "''")
            command = f"Set-Location -LiteralPath '{safe_cwd}'; {command}"
        cancel_event = get_current_cancel_event()
        ps_cmd = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + command
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.time() + timeout
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel_event and cancel_event.is_set():
                    proc.kill()
                    proc.communicate()
                    return "Shell cancelled"
                if time.time() >= deadline:
                    proc.kill()
                    proc.communicate()
                    return f"Command timed out after {timeout}s"
        output = stdout
        if stderr:
            output += f"\n[STDERR] {stderr}"
        if proc.returncode != 0:
            output += f"\n[Exit code: {proc.returncode}]"
        return output.strip() or "(no output)"
    except Exception as e:
        return f"Shell error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="ListProcesses",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ListProcesses(
    name_filter: str = "",
    sort_by: str = "memory",
    limit: int = 30,
) -> str:
    """List running processes with CPU and memory usage.

    Args:
        name_filter: Fuzzy filter by process name.
        sort_by: Sort by 'cpu', 'memory', or 'name'.
        limit: Max number of processes to return.
    """
    from winremote import process_mgr
    try:
        return process_mgr.list_processes(filter_str=name_filter, sort_by=sort_by, limit=limit)
    except Exception as e:
        return f"ListProcesses error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="KillProcess",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def KillProcess(pid: int = 0, name: str = "") -> str:
    """Kill a process by PID or name.

    Args:
        pid: Process ID.
        name: Process name (fuzzy matched).
    """
    from winremote import process_mgr
    try:
        return process_mgr.kill_process(pid=pid, name=name)
    except Exception as e:
        return f"KillProcess error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="GetSystemInfo",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def GetSystemInfo() -> str:
    """Get system information: CPU, memory, disk, network, uptime."""
    from winremote import process_mgr
    try:
        return process_mgr.get_system_info()
    except Exception as e:
        return f"GetSystemInfo error: {e}"


@_main.mcp.tool(annotations=ToolAnnotations(title="ReconnectSession", readOnlyHint=False))
def ReconnectSession(force: bool = False) -> list:
    """Reconnect a disconnected Windows desktop session to the console.

    This enables screenshot and UI automation tools to work when no RDP
    client is actively connected. Runs 'tscon' to attach the user's
    session to the console.

    Args:
        force: Reconnect even if session appears active (default False).
    """
    err = _main._ensure_session_connected(force=force)
    if err:
        return [TextContent(type="text", text=f"ReconnectSession failed: {err}")]
    return [TextContent(type="text", text="Session connected to console")]


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="PlaySound",
        destructiveHint=False,
        openWorldHint=False,
    )
)
def PlaySound(path: str | None = None, url: str | None = None) -> str:
    """Play an audio file on the Windows host.

    Supports .wav files natively. For .mp3/.ogg files, uses Windows Media Player.

    Args:
        path: Local file path to an audio file (e.g. C:\\Music\\alert.wav).
        url: URL to an audio file (will be downloaded to a temp file first).
    """
    from winremote.security import validate_fetch_url
    tmp_path = None
    try:
        if not path and not url:
            return "Error: provide either 'path' (local file) or 'url' (remote file)"

        if path:
            path_str = path.replace("\\", "/")
            if path_str.startswith("//") or path.startswith("\\\\"):
                return "PlaySound error: UNC paths are not allowed"
            path = str(_main._check_path(path))

        if url and not path:
            allowed, reason = validate_fetch_url(url)
            if not allowed:
                return f"PlaySound error: blocked URL: {reason}"
            suffix = ".wav"
            if ".mp3" in url:
                suffix = ".mp3"
            elif ".ogg" in url:
                suffix = ".ogg"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            deadline = time.time() + 30
            try:
                with _main._open_validated_fetch_url(url, timeout=15) as resp, open(tmp_path, "wb") as out:
                    remaining = 10 * 1024 * 1024
                    while True:
                        if time.time() > deadline:
                            return "PlaySound error: download timed out (30s limit)"
                        chunk = resp.read(min(65536, remaining + 1))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        if remaining < 0:
                            return "PlaySound error: remote file exceeds 10 MB limit"
                        out.write(chunk)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                tmp_path = None
                raise
            path = tmp_path

        ext = os.path.splitext(path)[1].lower()
        # Use single quotes to prevent PowerShell variable expansion / injection
        safe_path = path.replace("'", "''")

        if ext in (".mp3", ".ogg", ".wma", ".m4a"):
            # Use WPF MediaPlayer for non-WAV formats
            ps_command = (
                "Add-Type -AssemblyName presentationCore; "
                "$p = New-Object System.Windows.Media.MediaPlayer; "
                f"$p.Open([uri]'{safe_path}'); "
                "$p.Play(); "
                "Start-Sleep -Milliseconds 500; "
                "while ($p.NaturalDuration.HasTimeSpan -and "
                "$p.Position -lt $p.NaturalDuration.TimeSpan) "
                "{ Start-Sleep -Milliseconds 200 }; "
                "$p.Close()"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            if result.returncode != 0:
                return f"PlaySound error: {result.stderr}"
        else:
            # WAV: use SoundPlayer with PlaySync (blocks until done)
            ps_command = f"(New-Object System.Media.SoundPlayer '{safe_path}').PlaySync()"
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            if result.returncode != 0:
                return f"PlaySound error: {result.stderr}"

        return f"Played: {path}"
    except subprocess.TimeoutExpired:
        return "PlaySound timed out (audio may still be playing)"
    except Exception as e:
        return f"PlaySound error: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="Scrape",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def Scrape(url: str) -> str:
    """Fetch URL content and return as markdown.

    Args:
        url: URL to fetch.
    """
    try:
        from markdownify import markdownify

        with _main._open_validated_fetch_url(url, headers={"User-Agent": "winremote-mcp/0.4"}, timeout=15) as resp:
            html = resp.read(1024 * 1024 + 1)
            if len(html) > 1024 * 1024:
                return "Scrape error: response exceeds 1 MB limit"
            html = html.decode("utf-8", errors="replace")
        md = markdownify(html, heading_style="ATX", strip=["script", "style"])
        # Truncate
        if len(md) > 50000:
            md = md[:50000] + "\n\n[... truncated]"
        return md
    except Exception as e:
        return f"Scrape error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="ServiceList",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def ServiceList(name_filter: str = "") -> str:
    """List Windows services.

    Args:
        name_filter: Filter by service name or display name (substring match).
    """
    from winremote import services
    try:
        return services.service_list(name_filter)
    except Exception as e:
        return f"ServiceList error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="ServiceStart",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def ServiceStart(name: str) -> str:
    """Start a Windows service.

    Args:
        name: Service name.
    """
    from winremote import services
    try:
        return services.service_start(name)
    except Exception as e:
        return f"ServiceStart error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="ServiceStop",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def ServiceStop(name: str) -> str:
    """Stop a Windows service.

    Args:
        name: Service name.
    """
    from winremote import services
    try:
        return services.service_stop(name)
    except Exception as e:
        return f"ServiceStop error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="TaskList",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def TaskList(name_filter: str = "") -> str:
    """List Windows scheduled tasks.

    Args:
        name_filter: Filter by task name (substring match).
    """
    from winremote import services
    try:
        return services.task_list(name_filter)
    except Exception as e:
        return f"TaskList error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="TaskCreate",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def TaskCreate(name: str, command: str, schedule: str) -> str:
    """Create a Windows scheduled task.

    Args:
        name: Task name.
        command: Command to execute.
        schedule: Schedule type (ONCE, DAILY, WEEKLY, MONTHLY, ONSTART, ONLOGON, ONIDLE).
    """
    from winremote import services
    try:
        return services.task_create(name, command, schedule)
    except Exception as e:
        return f"TaskCreate error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="TaskDelete",
        destructiveHint=True,
        openWorldHint=False,
    )
)
def TaskDelete(name: str) -> str:
    """Delete a Windows scheduled task.

    Args:
        name: Task name.
    """
    from winremote import services
    try:
        return services.task_delete(name)
    except Exception as e:
        return f"TaskDelete error: {e}"


@_main.mcp.tool(
    annotations=ToolAnnotations(
        title="EventLog",
        readOnlyHint=True,
        openWorldHint=False,
    )
)
def EventLog(log_name: str = "System", count: int = 20, level: str = "") -> str:
    """Read Windows Event Log entries.

    Args:
        log_name: Log name (System, Application, Security, etc.).
        count: Number of entries to retrieve (default 20).
        level: Filter by level: critical, error, warning, information, verbose.
    """
    from winremote import services
    try:
        return services.event_log(log_name, count, level)
    except Exception as e:
        return f"EventLog error: {e}"


@_main.mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def CancelTask(task_id: str) -> str:
    """Cancel a running or pending task by its task ID.

    For Shell commands, the subprocess is killed mid-execution. For desktop
    interaction tools (Click, Type, Scroll, etc.) cancellation prevents the
    task from starting but cannot interrupt an in-progress operation.

    Args:
        task_id: The task ID returned when the tool was invoked (e.g. from [task:abc123]).
    """
    from winremote.taskmanager import manager as task_manager
    result = task_manager.cancel_task(task_id)
    if "error" in result:
        return f"Cancel failed: {result['error']}"
    return f"Cancelled task {task_id} ({result['tool_name']})"


@_main.mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def GetTaskStatus(task_id: str = "") -> str:
    """Get status of a specific task or list recent tasks.

    Args:
        task_id: If provided, get status of this task. If empty, list recent tasks.
    """
    from winremote.taskmanager import manager as task_manager
    if task_id:
        info = task_manager.get_task(task_id)
        if info is None:
            return f"Task {task_id} not found"
        return json.dumps(info, indent=2)
    tasks = task_manager.list_tasks()
    if not tasks:
        return "No tasks in history."
    lines = ["Recent tasks:"]
    for t in tasks[:20]:
        dur = f" ({t['duration']}s)" if t["duration"] is not None else ""
        err = f" — {t['error']}" if t.get("error") else ""
        lines.append(f"  [{t['task_id']}] {t['tool_name']} → {t['status']}{dur}{err}")
    return "\n".join(lines)


@_main.mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def GetRunningTasks() -> str:
    """List all currently running and pending tasks."""
    from winremote.taskmanager import manager as task_manager
    running = task_manager.list_tasks("running")
    pending = task_manager.list_tasks("pending")
    all_active = running + pending
    if not all_active:
        return "No active tasks."
    lines = [f"Active tasks ({len(all_active)}):"]
    for t in all_active:
        dur = f" ({t['duration']}s)" if t["duration"] is not None else ""
        lines.append(f"  [{t['task_id']}] {t['tool_name']} [{t['category']}] {t['status']}{dur}")
    return "\n".join(lines)
