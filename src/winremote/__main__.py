"""winremote-mcp — CLI entry point and MCP tool definitions."""

from __future__ import annotations

import base64
import getpass
import io
import json
import logging
import os
import platform
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("winremote")

import click
from click.core import ParameterSource

try:
    import pyautogui

    PYAUTOGUI_IMPORT_ERROR: Exception | None = None
except Exception as e:  # pragma: no cover - environment-specific import failure
    pyautogui = None
    PYAUTOGUI_IMPORT_ERROR = e
from dotenv import load_dotenv
from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

try:
    from mcp.types import ToolAnnotations
except ImportError:
    from fastmcp.tools import ToolAnnotations

from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from winremote import __version__, desktop, network, ocr, process_mgr, recording, registry, services
from winremote.desktop import ensure_session_connected as _ensure_session_connected
from winremote.config import discover_config_path, load_config
from winremote.security import (
    IPAllowlistMiddleware,
    _resolve_and_validate,
    is_loopback_bind_host,
    parse_ip_allowlist,
    validate_fetch_url,
)
from winremote.taskmanager import get_current_cancel_event, manager as task_manager
from winremote.tiers import ALL_TOOLS, get_tier_names, parse_tool_csv, resolve_enabled_tools

load_dotenv()

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


def _check_path(path: str) -> Path:
    """Resolve path and verify it is within the sandboxed file root.

    Raises ValueError if the resolved path escapes _FILE_ROOT.
    """
    resolved = Path(path).expanduser().resolve()
    root = _FILE_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"Path '{path}' is outside the permitted file root '{root}'")
    return resolved


def _patch_fastmcp_streamable_http_get_probe() -> None:
    """Make session-less GET /mcp probes return 405 instead of 400."""
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    except Exception:
        return

    original = getattr(StreamableHTTPSessionManager, "handle_request", None)
    if original is None or getattr(StreamableHTTPSessionManager, "_winremote_get_probe_patched", False):
        return

    async def patched_handle_request(self, scope, receive, send):
        request = Request(scope, receive)
        if request.method == "GET" and not request.headers.get("mcp-session-id"):
            accept = (request.headers.get("accept") or "").lower()
            if "text/event-stream" in accept:
                response = Response(status_code=405, headers={"Allow": "POST, GET"})
                await response(scope, receive, send)
                return
        return await original(self, scope, receive, send)

    StreamableHTTPSessionManager.handle_request = patched_handle_request
    StreamableHTTPSessionManager._winremote_get_probe_patched = True


if pyautogui is not None:
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05

mcp = FastMCP(
    "winremote-mcp",
    instructions=(
        "Windows Remote MCP Server. Provides desktop control, window management, "
        "shell execution, file operations, network tools, registry, services, "
        "and system management tools for a Windows machine."
    ),
)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "ok", "version": __version__})


# ---------------------------------------------------------------------------
# Helpers (defined here so tool modules can access them via _main.*)
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


# ---------------------------------------------------------------------------
# Import domain tool modules — this triggers @mcp.tool() registration.
# Tool modules use `import winremote.__main__ as _main` for late binding so
# test patches on winremote.__main__.desktop etc. propagate correctly.
# ---------------------------------------------------------------------------

from winremote.tools import desktop_tools  # noqa: E402 — must come after mcp and helpers are defined
from winremote.tools import file_tools  # noqa: E402
from winremote.tools import system_tools  # noqa: E402
from winremote.tools import network_tools  # noqa: E402
from winremote.tools import registry_tools  # noqa: E402

# ---------------------------------------------------------------------------
# Re-export tool functions that tests import directly from winremote.__main__
# ---------------------------------------------------------------------------

from winremote.tools.desktop_tools import (  # noqa: E402
    App,
    AnnotatedSnapshot,
    Click,
    FocusWindow,
    GetClipboard,
    LockScreen,
    MinimizeAll,
    Move,
    Notification,
    OCR,
    ScreenRecord,
    Scroll,
    SetClipboard,
    Shortcut,
    Snapshot,
    Type,
    Wait,
)
from winremote.tools.file_tools import (  # noqa: E402
    FileDownload,
    FileList,
    FileRead,
    FileSearch,
    FileUpload,
    FileWrite,
)
from winremote.tools.system_tools import (  # noqa: E402
    CancelTask,
    EventLog,
    GetRunningTasks,
    GetSystemInfo,
    GetTaskStatus,
    KillProcess,
    ListProcesses,
    PlaySound,
    ReconnectSession,
    Scrape,
    ServiceList,
    ServiceStart,
    ServiceStop,
    Shell,
    TaskCreate,
    TaskDelete,
    TaskList,
)
from winremote.tools.network_tools import (  # noqa: E402
    NetConnections,
    Ping,
    PortCheck,
)
from winremote.tools.registry_tools import (  # noqa: E402
    RegRead,
    RegWrite,
)

# ====================== Apply task manager wrapping ========================


from winremote.tiers import _get_registered_tools as _tiers_get_registered_tools
from winremote.tiers import filter_tools as _tiers_filter_tools


def _wrap_all_tools():
    """Wrap all registered MCP tools with task manager for error resilience + concurrency."""
    skip = {"CancelTask", "GetTaskStatus", "GetRunningTasks"}
    for name, tool in _tiers_get_registered_tools(mcp).items():
        if name in skip:
            continue
        original_fn = getattr(tool, "fn", None)
        if callable(original_fn):
            tool.fn = task_manager.wrap_sync_tool(name, original_fn)


_wrap_all_tools()


def _param_explicit(ctx: click.Context, name: str) -> bool:
    src = ctx.get_parameter_source(name)
    return src in {ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT}


def _choose_value(ctx: click.Context, name: str, cli_value, config_value, default_value):
    if _param_explicit(ctx, name):
        return cli_value
    if config_value is not None:
        return config_value
    return default_value


def _apply_tool_filter(enabled_tools: set[str]) -> None:
    _tiers_filter_tools(mcp, enabled_tools)


def _get_registered_tools() -> dict:
    return _tiers_get_registered_tools(mcp)


def _resolve_settings(
    ctx,
    cfg,
    *,
    host,
    port,
    auth_key,
    enable_tier3,
    disable_tier2,
    tools,
    exclude_tools,
    ip_allowlist,
    ssl_certfile,
    ssl_keyfile,
    oauth_client_id,
    oauth_client_secret,
    file_root,
    enable_all,
):
    """Merge CLI params, config file values, and defaults into a flat settings dict."""
    resolved = {
        "host": _choose_value(ctx, "host", host, cfg.server.host, "127.0.0.1"),
        "port": int(_choose_value(ctx, "port", port, cfg.server.port, 8090)),
        "auth_key": _choose_value(ctx, "auth_key", auth_key, cfg.server.auth_key, None),
        "ssl_certfile": _choose_value(ctx, "ssl_certfile", ssl_certfile, cfg.server.ssl_certfile, None),
        "ssl_keyfile": _choose_value(ctx, "ssl_keyfile", ssl_keyfile, cfg.server.ssl_keyfile, None),
        "oauth_client_id": _choose_value(ctx, "oauth_client_id", oauth_client_id, cfg.security.oauth_client_id, None),
        "oauth_client_secret": _choose_value(ctx, "oauth_client_secret", oauth_client_secret, cfg.security.oauth_client_secret, None),
        "enable_tier3": bool(_choose_value(ctx, "enable_tier3", enable_tier3, cfg.security.enable_tier3, False)),
        "disable_tier2": bool(_choose_value(ctx, "disable_tier2", disable_tier2, cfg.security.disable_tier2, False)),
        "enable_all": enable_all,
    }

    raw_file_root = _choose_value(ctx, "file_root", file_root, cfg.server.file_root, None)
    resolved["file_root_path"] = Path(raw_file_root).expanduser().resolve() if raw_file_root else Path.home()

    cli_tools = parse_tool_csv(tools)
    cli_excluded = parse_tool_csv(exclude_tools)
    cli_allowlist = parse_tool_csv(ip_allowlist)
    resolved["selected_tools"] = cli_tools if _param_explicit(ctx, "tools") else cfg.tools.enable
    resolved["excluded_tools"] = cli_excluded if _param_explicit(ctx, "exclude_tools") else cfg.tools.exclude
    resolved["allowlist_entries"] = cli_allowlist if _param_explicit(ctx, "ip_allowlist") else cfg.security.ip_allowlist
    return resolved


def _setup_oauth(mcp_instance, store, issuer, client_id, client_secret):
    """Wire OAuth routes onto mcp and return a validator callable."""
    from winremote.oauth import build_oauth_routes, validate_oauth_token

    routes = build_oauth_routes(
        store=store,
        issuer=issuer,
        configured_client_id=client_id,
        configured_client_secret=client_secret,
    )
    for path, (handler, methods) in routes.items():
        mcp_instance.custom_route(path, methods=methods)(handler)
    return lambda tok: validate_oauth_token(store, tok)


def _build_middleware(auth_key, oauth_validator, allowlist_entries) -> list:
    """Build starlette Middleware list from auth and allowlist settings."""
    middleware: list[Middleware] = []

    if allowlist_entries:
        allowlist_networks = parse_ip_allowlist(allowlist_entries)
        middleware.append(Middleware(IPAllowlistMiddleware, allowlist=allowlist_networks))

    if auth_key:
        from winremote.auth import AuthKeyMiddleware

        middleware.append(Middleware(AuthKeyMiddleware, auth_key=auth_key, oauth_validator=oauth_validator))
    elif oauth_validator:
        from winremote.auth import OAuthOnlyMiddleware

        middleware.append(Middleware(OAuthOnlyMiddleware, oauth_validator=oauth_validator))

    return middleware


# ================================== CLI ====================================


@click.group(invoke_without_command=True)
@click.option("--transport", default="streamable-http", type=click.Choice(["stdio", "streamable-http"]))
@click.option("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1; use 0.0.0.0 for remote access)")
@click.option("--port", default=8090, type=click.IntRange(1, 65535))
@click.option("--reload", is_flag=True, default=False, help="Enable hot reload (streamable-http only)")
@click.option("--debug", is_flag=True, default=False, help="Enable detailed debug logging")
@click.option("--auth-key", default=None, envvar="WINREMOTE_AUTH_KEY", help="API key for authentication")
@click.option("--config", default=None, help="Path to winremote.toml config file")
@click.option(
    "--enable-all",
    is_flag=True,
    default=False,
    help="Enable all tools including high-risk Tier 3 tools (backward-compatible)",
)
@click.option("--enable-tier3", is_flag=True, default=False, help="Enable tier3 destructive tools")
@click.option("--disable-tier2", is_flag=True, default=False, help="Disable tier2 interactive tools")
@click.option("--tools", default="", help="Comma-separated tools to enable (highest precedence)")
@click.option("--exclude-tools", default="", help="Comma-separated tools to disable")
@click.option("--ip-allowlist", default="", help="Comma-separated IPs/CIDRs allowed to access HTTP transport")
@click.option("--ssl-certfile", default=None, help="Path to SSL certificate file for HTTPS")
@click.option("--ssl-keyfile", default=None, help="Path to SSL private key file for HTTPS")
@click.option("--oauth-client-id", default=None, envvar="WINREMOTE_OAUTH_CLIENT_ID", help="OAuth client ID whitelist")
@click.option("--oauth-client-secret", default=None, envvar="WINREMOTE_OAUTH_CLIENT_SECRET", help="OAuth client secret")
@click.option(
    "--file-root",
    default=None,
    envvar="WINREMOTE_FILE_ROOT",
    help="Root directory for file operations (default: user home). All file tools are sandboxed to this tree.",
)
@click.option(
    "--allow-reg-write-all",
    is_flag=True,
    default=False,
    help="Allow registry writes to any key (requires --enable-tier3; default: HKCU\\SOFTWARE and HKCU\\Environment only).",
)
@click.pass_context
def cli(
    ctx,
    transport: str,
    host: str,
    port: int,
    reload: bool,
    debug: bool,
    auth_key: str | None,
    config: str | None,
    enable_all: bool,
    enable_tier3: bool,
    disable_tier2: bool,
    tools: str,
    exclude_tools: str,
    ip_allowlist: str,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
    oauth_client_id: str | None,
    oauth_client_secret: str | None,
    file_root: str | None,
    allow_reg_write_all: bool,
):
    """Start the winremote MCP server."""
    if ctx.invoked_subcommand is not None:
        return

    config_path = discover_config_path(config)
    cfg = load_config(config_path)

    s = _resolve_settings(
        ctx, cfg,
        host=host, port=port, auth_key=auth_key,
        enable_tier3=enable_tier3, disable_tier2=disable_tier2,
        tools=tools, exclude_tools=exclude_tools, ip_allowlist=ip_allowlist,
        ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile,
        oauth_client_id=oauth_client_id, oauth_client_secret=oauth_client_secret,
        file_root=file_root, enable_all=enable_all,
    )
    host = s["host"]
    port = s["port"]
    auth_key = s["auth_key"]
    ssl_certfile = s["ssl_certfile"]
    ssl_keyfile = s["ssl_keyfile"]
    oauth_client_id = s["oauth_client_id"]
    oauth_client_secret = s["oauth_client_secret"]
    enable_tier3 = s["enable_tier3"]
    disable_tier2 = s["disable_tier2"]
    global _FILE_ROOT
    _FILE_ROOT = s["file_root_path"]

    configured_oauth = bool(oauth_client_id and oauth_client_secret)
    if transport != "stdio" and not is_loopback_bind_host(host) and not (auth_key or configured_oauth):
        raise click.ClickException(
            "Refusing to bind HTTP transport to a non-loopback address without authentication. "
            "Use --auth-key or --oauth-client-id/--oauth-client-secret, or bind to 127.0.0.1."
        )
    if (oauth_client_id or oauth_client_secret) and not configured_oauth:
        raise click.ClickException("OAuth requires both --oauth-client-id and --oauth-client-secret.")

    enabled_tools = resolve_enabled_tools(
        enable_tier3=enable_tier3, disable_tier2=disable_tier2, enable_all=enable_all,
        explicit_tools=s["selected_tools"], exclude_tools=s["excluded_tools"],
    )
    _apply_tool_filter(enabled_tools)
    enabled_tiers = get_tier_names(enabled_tools)

    if allow_reg_write_all:
        if not enable_tier3:
            raise click.ClickException("--allow-reg-write-all requires --enable-tier3.")
        registry.allow_reg_write_all = True

    use_oauth = bool(oauth_client_id or oauth_client_secret)
    oauth_validator = None
    if use_oauth and transport != "stdio":
        from winremote.oauth import OAuthStore
        oauth_store = OAuthStore()
        scheme = "https" if (ssl_certfile and ssl_keyfile) else "http"
        issuer = f"{scheme}://{host}:{port}"
        oauth_validator = _setup_oauth(mcp, oauth_store, issuer, oauth_client_id, oauth_client_secret)

    middleware = _build_middleware(auth_key, oauth_validator, s["allowlist_entries"])

    if debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("winremote").setLevel(logging.DEBUG)

    class BannerFilter(logging.Filter):
        """Inject our banner after uvicorn's 'Application startup complete' log."""

        _shown = False

        def filter(self, record):
            if os.environ.get("WINREMOTE_QUIET") in {"1", "true", "yes"}:
                return True
            if not self._shown and "Application startup complete" in record.getMessage():
                self._shown = True
                auth_line = "[auth ON]" if (auth_key or use_oauth) else "[no auth]"
                ssl_line = "[https ON]" if (ssl_certfile and ssl_keyfile) else ""
                oauth_line = "[oauth ON]" if use_oauth else ""
                bind_line = f"[{host}:{port}]"
                tiers_line = f"[tiers: {','.join(enabled_tiers)}]"
                tools_line = f"[tools: {len(enabled_tools)}/{len(ALL_TOOLS)}]"
                pad = " " * 10
                ver_line = f"winremote-mcp v{__version__}"
                lines = [
                    f"{pad}+----------------------------------+",
                    f"{pad}|  {ver_line:<32s}|",
                    f"{pad}|  by dddabtc                      |",
                    f"{pad}|  github.com/dddabtc              |",
                    f"{pad}|  {auth_line:<32s}|",
                    *([f"{pad}|  {ssl_line:<32s}|"] if ssl_line else []),
                    *([f"{pad}|  {oauth_line:<32s}|"] if oauth_line else []),
                    f"{pad}|  {bind_line:<32s}|",
                    f"{pad}|  {tiers_line:<16s}{tools_line:<16s}|",
                    f"{pad}+----------------------------------+",
                ]
                if host == "0.0.0.0" and not (auth_key or use_oauth):
                    lines.append(f"{pad}  WARNING: open to network without auth!")
                    lines.append(f"{pad}  Use --auth-key for security.")
                if enable_all:
                    lines.append(f"{pad}  INFO: High-risk Tier 3 tools enabled!")
                print("\n" + "\n".join(lines) + "\n", flush=True)
            return True

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        _patch_fastmcp_streamable_http_get_probe()
        logging.getLogger("uvicorn.error").addFilter(BannerFilter())
        run_kwargs = dict(transport="streamable-http", host=host, port=port)
        if middleware:
            run_kwargs["middleware"] = middleware
        if platform.system() == "Windows":
            os.environ.setdefault("NO_COLOR", "1")
        uvicorn_args = {}
        if reload:
            uvicorn_args["reload"] = True
        if debug:
            uvicorn_args["log_level"] = "debug"
        if ssl_certfile and ssl_keyfile:
            uvicorn_args["ssl_certfile"] = ssl_certfile
            uvicorn_args["ssl_keyfile"] = ssl_keyfile
        if uvicorn_args:
            run_kwargs["uvicorn_args"] = uvicorn_args
        mcp.run(**run_kwargs)


@cli.command()
def install():
    """Create a Windows scheduled task for auto-start."""
    username = getpass.getuser()

    # Create start_mcp.bat for Chinese Windows compatibility
    python_exe = subprocess.run(["where", "python"], capture_output=True, text=True).stdout.strip().split("\n")[0]
    bat_content = f"""@echo off
rem winremote-mcp startup script with UTF-8 encoding for Chinese Windows
set PYTHONIOENCODING=utf-8
"{python_exe}" -m winremote %*
"""

    # Write batch file to user's profile directory
    user_profile = os.environ.get("USERPROFILE", ".")
    bat_path = os.path.join(user_profile, "start_mcp.bat")

    try:
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_content)
        click.echo(f"[OK] Created startup script: {bat_path}")
    except Exception as e:
        click.echo(f"[ERROR] Failed to create startup script: {e}")
        return

    # Create scheduled task using the batch file
    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/SC", "ONSTART", "/TN", "WinRemoteMCP", "/TR", bat_path, "/RU", username, "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.echo("[OK] Scheduled task 'WinRemoteMCP' created for auto-start.")
            click.echo("The server will start automatically on system boot.")
            click.echo("Note: Uses start_mcp.bat for Chinese Windows compatibility.")
        else:
            click.echo(f"[ERROR] Failed to create task:\n{result.stderr or result.stdout}")
    except Exception as e:
        click.echo(f"[ERROR] Error: {e}")


@cli.command()
def uninstall():
    """Remove the WinRemoteMCP scheduled task."""
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", "WinRemoteMCP", "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.echo("[OK] Scheduled task 'WinRemoteMCP' removed.")
        else:
            click.echo(f"[ERROR] Failed to remove task:\n{result.stderr or result.stdout}")
    except Exception as e:
        click.echo(f"[ERROR] Error: {e}")

    # Also remove the batch file
    user_profile = os.environ.get("USERPROFILE", ".")
    bat_path = os.path.join(user_profile, "start_mcp.bat")
    try:
        if os.path.exists(bat_path):
            os.remove(bat_path)
            click.echo(f"[OK] Removed startup script: {bat_path}")
    except Exception as e:
        click.echo(f"[ERROR] Failed to remove startup script: {e}")


@cli.command()
def health():
    """Print health status JSON."""
    click.echo(json.dumps({"status": "ok", "version": __version__}))


if __name__ == "__main__":
    cli()
