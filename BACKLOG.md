# winremote-mcp Fix Backlog

Generated from full code review (Sentinel · Architect · Auditor · Librarian · Skeptic).
Goal: make this codebase safe and reliable for Claude to use as a Windows remote-control MCP server.

## Session Progress

Next item: SEC-01

| ID | Area | Severity | Status | Description |
|----|------|----------|--------|-------------|
| SEC-01 | Security | CRITICAL | done  | Remove --allow-insecure-remote escape hatch |
| SEC-02 | Security | HIGH | done  | Fix timing side-channel in API key comparison |
| SEC-03 | Security | HIGH | done  | Fix DNS-rebinding SSRF in Scrape/PlaySound |
| SEC-04 | Security | HIGH | done  | Add path confinement to file tools |
| SEC-05 | Security | HIGH | done  | Add key allowlist to RegWrite |
| SEC-06 | Security | MEDIUM | done  | Block UNC paths in PlaySound |
| SEC-07 | Security | MEDIUM | done  | Validate TaskCreate schedule parameter |
| REL-01 | Reliability | CRITICAL | done  | Fix CancelTask no-op (cancellation lies) |
| REL-02 | Reliability | CRITICAL | done  | Fix task status race condition (unlocked mutations) |
| REL-03 | Reliability | HIGH | done  | Fix FileWrite non-atomic write |
| REL-04 | Reliability | HIGH | done  | Fix OAuthStore concurrent delete race + unbounded growth |
| REL-05 | Reliability | HIGH | done  | Fix PlaySound URL download: add per-chunk read timeout |
| REL-06 | Reliability | HIGH | done  | Fix subprocess text encoding for Chinese Windows |
| REL-07 | Reliability | HIGH | done  | Fix clipboard busy race (retry OpenClipboard) |
| REL-08 | Reliability | HIGH | done  | Fix session parser locale fragility |
| PERF-01 | Performance | HIGH | done  | Move Wait out of DESKTOP semaphore category |
| PERF-02 | Performance | HIGH | done  | Fix FileSearch: stream rglob instead of materializing |
| PERF-03 | Performance | HIGH | done  | Fix FileRead: read with limit not full file |
| PERF-04 | Performance | HIGH | done  | Fix threadpool exhaustion from blocking semaphore acquire |
| PERF-05 | Performance | MEDIUM | done  | Fix cpu_percent always 0.0 (add priming interval) |
| PERF-06 | Performance | MEDIUM | done  | Fix FileList: use os.scandir instead of stat() per entry |
| PERF-07 | Performance | MEDIUM | done  | Fix process sort: use heapq.nlargest |
| PERF-08 | Performance | MEDIUM | done  | Cache _get_system_language() result |
| PERF-09 | Performance | MEDIUM | done  | Fix socket FD leak in port_check |
| PERF-10 | Performance | MEDIUM | done  | Fix AnnotatedSnapshot resize: use LANCZOS not BICUBIC |
| ARCH-01 | Architecture | CRITICAL | done  | Consolidate duplicate fastmcp internals access |
| ARCH-02 | Architecture | HIGH | done  | Remove module-level monkey-patch at import time |
| ARCH-03 | Architecture | HIGH | done  | Consolidate tool metadata (tier+category co-location) |
| ARCH-04 | Architecture | HIGH | done  | Split god module __main__.py into domain modules |
| ARCH-05 | Architecture | HIGH | done  | Extract cli() into focused helper functions |
| ARCH-06 | Architecture | MEDIUM | done  | Isolate fastmcp private internals in adapter module |
| ARCH-07 | Architecture | MEDIUM | done  | Move _ensure_session_connected to desktop.py/session.py |
| ARCH-08 | Architecture | MEDIUM | done  | Extract shared grab-with-reconnect helper |
| ARCH-09 | Architecture | MEDIUM | done  | Remove dead async semaphore code |
| QUAL-01 | Quality | MEDIUM | done  | Fix deferred import inconsistency |
| QUAL-02 | Quality | MEDIUM | done  | Fix filter param naming inconsistency |
| QUAL-03 | Quality | MEDIUM | done  | Fix ScreenRecord conflicting duration clamp |
| QUAL-04 | Quality | MEDIUM | done  | Fix misleading ~75MB comment in FileUpload |
| QUAL-05 | Quality | MEDIUM | done  | Replace inline __import__ in _NoRedirectHandler |
| QUAL-06 | Quality | LOW | done  | Extract named constants (byte caps, truncation lengths, fuzzy thresholds) |
| QUAL-07 | Quality | LOW | done  | Fix Optional[str] → str | None in desktop.py |
| QUAL-08 | Quality | LOW | done  | Fix magic resample=3 → Image.Resampling.LANCZOS |
| QUAL-09 | Quality | LOW | done  | Fix registry.py winreg constant obfuscation |
| QUAL-10 | Quality | LOW | done  | Fix ocr.py chr(39) obfuscation |
| QUAL-11 | Quality | LOW | done  | Add query-session format comment to _ensure_session_connected |
| QUAL-12 | Quality | LOW | done  | Fix services.py empty event log stderr false error |
| QUAL-13 | Quality | LOW | done  | Fix SetForegroundWindow false success report |
| QUAL-14 | Quality | LOW | done  | Range-check port in CLI |
| QUAL-15 | Quality | LOW | done  | Align semaphore acquire timeout with Shell timeout |

---

## Detailed Fix Specifications

### SEC-01 — Remove --allow-insecure-remote escape hatch
**File:** `src/winremote/__main__.py:1799-1808`
**Problem:** `--allow-insecure-remote` allows binding 0.0.0.0 with no authentication at all. With tier3
enabled this exposes Shell/FileWrite/RegWrite/TaskCreate unauthenticated to the entire network.
The flag defeats the purpose of the auth guard entirely.
**Fix:** Remove `--allow-insecure-remote` flag and `allow_insecure_remote` config option. The guard at
lines 1799-1808 should be unconditional: non-loopback bind always requires `--auth-key` or OAuth.
If the user genuinely needs unauthenticated access they can bind to 127.0.0.1 and use a reverse proxy.
Also remove from `config.py` ServerConfig dataclass.

---

### SEC-02 — Fix timing side-channel in API key comparison
**File:** `src/winremote/auth.py:44`
**Problem:** `if auth_header == f"Bearer {self.auth_key}"` short-circuits on first differing byte.
An attacker can incrementally recover the key via response timing. OAuth already uses
`secrets.compare_digest` (oauth.py:82,280) but the primary API key path does not.
**Fix:**
```python
import hmac
if hmac.compare_digest(
    auth_header.encode("utf-8", errors="replace"),
    f"Bearer {self.auth_key}".encode("utf-8", errors="replace")
):
    return await call_next(request)
```

---

### SEC-03 — Fix DNS-rebinding SSRF in Scrape/PlaySound
**File:** `src/winremote/security.py:27-55` and `src/winremote/__main__.py:723-749`
**Problem:** `validate_fetch_url` resolves the hostname with `getaddrinfo` and checks IPs, but the
actual fetch in `_open_validated_fetch_url` performs a second independent DNS resolution via urllib.
An attacker-controlled domain returns a public IP at validation time and a private/loopback IP at
fetch time (DNS rebinding), defeating the SSRF check. CWE-918/CWE-367.
**Fix:** Resolve once in `validate_fetch_url`, return the validated IP. In `_open_validated_fetch_url`,
connect directly to that pinned IP (override `HTTPConnection.connect`) with the original hostname in
the Host header. This eliminates the TOCTOU window entirely.

---

### SEC-04 — Add path confinement to file tools
**File:** `src/winremote/__main__.py:903-950, 1056-1097` and `src/winremote/config.py`
**Problem:** `FileRead`, `FileWrite`, `FileDownload`, `FileUpload` accept any absolute path with no
sandbox restriction. Allows reading SAM, dropping payloads into Startup, etc. CWE-22.
**Fix:** Add `file_root` config option (default: user's home directory). In each file tool, resolve
the path and assert it is under `file_root`:
```python
def _check_path(path: str, root: Path) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PermissionError(f"Path outside allowed root: {path}")
    return resolved
```
Expose `--file-root` CLI flag and `file_root` in `[server]` TOML section.

---

### SEC-05 — Add key allowlist to RegWrite
**File:** `src/winremote/registry.py:63-85`
**Problem:** `RegWrite` allows writing to any registry key with no restriction. Can set HKLM autostart,
hijack class handlers, disable security tooling. Combined with FileWrite = full persistence + privesc. CWE-250.
**Fix:** Add a `SAFE_REG_PREFIXES` allowlist (configurable, default conservative):
```python
SAFE_REG_PREFIXES = [
    "HKCU\\SOFTWARE\\",
    "HKCU\\Environment",
]
```
Reject writes outside the allowlist by default. Add `--allow-reg-write-all` flag (tier3 only) to
override for power users who need it.

---

### SEC-06 — Block UNC paths in PlaySound
**File:** `src/winremote/__main__.py:759-845`
**Problem:** `PlaySound(path=...)` accepts UNC paths like `\\attacker\share\x.wav`. When PowerShell
opens these, Windows performs SMB authentication, leaking NetNTLMv2 hashes to the attacker.
**Fix:** Validate the local `path` argument — reject paths starting with `\\` or `//`:
```python
if path and (path.startswith("\\\\") or path.startswith("//")):
    return "PlaySound error: UNC paths are not allowed"
```
Also ensure the path is within `file_root` (same as SEC-04).

---

### SEC-07 — Validate TaskCreate schedule parameter
**File:** `src/winremote/services.py:86-104`
**Problem:** `schedule` parameter is interpolated into `schtasks /SC '{schedule}'` with only
single-quote escaping. Not validated against the documented enum. Malformed input reaches schtasks.
**Fix:**
```python
VALID_SCHEDULES = {"ONCE","DAILY","WEEKLY","MONTHLY","ONSTART","ONLOGON","ONIDLE"}
def task_create(name: str, command: str, schedule: str) -> str:
    if schedule.upper() not in VALID_SCHEDULES:
        return f"TaskCreate error: invalid schedule '{schedule}'; must be one of {VALID_SCHEDULES}"
    ...
```

---

### REL-01 — Fix CancelTask no-op
**File:** `src/winremote/taskmanager.py:123-134, 237-246`
**Problem:** `cancel()` sets a `threading.Event` but no tool body checks `is_cancelled` during
execution. The wrapper only checks before (`line 237`) and after (`line 245`) `func()`. A running
`Shell(timeout=30)` runs to completion regardless of `CancelTask`. The API actively lies.
**Fix:** For the subprocess-based tools (Shell, PlaySound, network tools), pass a cancel event and
poll it: use `subprocess.Popen` + a polling loop that calls `proc.kill()` when the event fires.
For tools that cannot be interrupted (Click, Type), document clearly in the tool description that
cancellation is "prevents pending start only." Update `CancelTask` docstring to be honest about scope.
Also fix `task_create` wrapper to not set COMPLETED if already CANCELLED (guard at line 248):
```python
with self._manager._lock:
    if self.status != TaskStatus.CANCELLED:
        self.status = TaskStatus.COMPLETED
```

---

### REL-02 — Fix task status race condition
**File:** `src/winremote/taskmanager.py:123-130, 237-249`
**Problem:** All status mutations (`RUNNING`, `COMPLETED`, `CANCELLED`, `completed_at`) are performed
outside `self._manager._lock`. `cancel()` sets status and `completed_at` unguarded while the wrapper
concurrently writes to the same fields. Final state is non-deterministic under concurrent load.
**Fix:** Guard every `TaskInfo` status write with the manager lock:
```python
# In wrapper, before setting RUNNING:
with self._manager._lock:
    self.status = TaskStatus.RUNNING
    self.started_at = time.time()

# In wrapper, on completion:
with self._manager._lock:
    if self.status != TaskStatus.CANCELLED:
        self.status = TaskStatus.COMPLETED
        self.completed_at = time.time()
```
Same pattern for FAILED. `cancel()` already acquires manager lock if you add it there.

---

### REL-03 — Fix FileWrite non-atomic write
**File:** `src/winremote/__main__.py:933-950`
**Problem:** `open(p, "w")` truncates the file before writing. If the write fails (disk full,
encoding error, antivirus lock) the original file is left empty/corrupt. CWE-73.
**Fix:** Write to a temp file in the same directory, then `os.replace()`:
```python
import tempfile
tmp = None
try:
    if _tobool(append):
        with open(p, "a", encoding=encoding) as f:
            f.write(content)
    else:
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp_")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
            os.replace(tmp, p)
            tmp = None
        except:
            os.unlink(tmp)
            raise
except UnicodeEncodeError as e:
    return f"FileWrite error: encoding {encoding!r} cannot represent the content: {e}"
```

---

### REL-04 — Fix OAuthStore concurrent delete race + unbounded growth
**File:** `src/winremote/oauth.py:53-59, 263-273, 312-320`
**Problem:** (1) `del store.tokens[token]` in `validate_oauth_token` raises `KeyError` on concurrent
validation of the same expired token → 500 instead of 401. (2) Expired codes/tokens never evicted;
dicts grow unbounded (DoS vector). CWE-770.
**Fix:** (1) Use `store.tokens.pop(token, None)` throughout. (2) Add eviction on every `authorize`
call — sweep expired entries from `store.codes` and `store.tokens` (O(n) but called rarely):
```python
def _evict_expired(store: OAuthStore) -> None:
    now = time.time()
    store.codes = {k: v for k, v in store.codes.items() if v["expires_at"] > now}
    store.tokens = {k: v for k, v in store.tokens.items() if v["expires_at"] > now}
```
(3) Cap store sizes at 1000 entries to bound memory under attack.

---

### REL-05 — Fix PlaySound URL download: add streaming timeout
**File:** `src/winremote/__main__.py:786-795`
**Problem:** The urllib read loop has no wall-clock deadline. A server that accepts the connection but
stalls mid-stream holds the SHELL semaphore slot indefinitely (connection timeout is not a streaming
timeout). Denial of service.
**Fix:** Track a wall-clock deadline around the download loop:
```python
deadline = time.monotonic() + 30  # 30s total download budget
while True:
    if time.monotonic() > deadline:
        return "PlaySound error: download timed out after 30s"
    chunk = resp.read(65536)
    ...
```

---

### REL-06 — Fix subprocess text encoding for Chinese Windows
**File:** `src/winremote/services.py:8-21`, `src/winremote/__main__.py:493-508`,
`src/winremote/desktop.py:248-261`
**Problem:** `text=True` in `subprocess.run` decodes output with the system locale. On Chinese Windows
(explicit target in install comments) PowerShell emits GBK/CP936. Python may raise `UnicodeDecodeError`,
turning successful operations into error responses.
**Fix:** In all `subprocess.run` calls, add `encoding="utf-8", errors="replace"` and prefix PowerShell
commands with `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; `:
```python
command = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + command
result = subprocess.run(
    ["powershell", "-NoProfile", "-Command", command],
    capture_output=True, text=True,
    encoding="utf-8", errors="replace",
    timeout=timeout,
)
```

---

### REL-07 — Fix clipboard busy race
**File:** `src/winremote/desktop.py:281-311`
**Problem:** `win32clipboard.OpenClipboard()` raises on ACCESS_DENIED when another process holds the
clipboard. Under an active RDP session (the primary use case) this is common. No retry logic.
**Fix:** Retry with backoff (Windows convention: up to 10 retries, ~10ms apart):
```python
import time as _time
def _open_clipboard(retries: int = 10, delay: float = 0.01) -> None:
    for i in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception:
            if i == retries - 1:
                raise
            _time.sleep(delay)
```

---

### REL-08 — Fix session parser locale fragility
**File:** `src/winremote/__main__.py:611-676`
**Problem:** The `query session` output parser splits on whitespace positionally. Fails on:
usernames with spaces, non-English `disc` state names (German: "Getrennt", French: "Déconnecté"),
the `>` current-session marker. On non-English Windows disconnected sessions read as connected.
**Fix:** Use `WTSEnumerateSessions` via `win32ts` (part of pywin32) instead of scraping localized
text. `win32ts` returns structured data regardless of locale:
```python
import win32ts
sessions = win32ts.WTSEnumerateSessions(win32ts.WTS_CURRENT_SERVER_HANDLE)
# Each session: {'SessionId': int, 'WinStationName': str, 'State': int}
# State 4 = WTSDisconnected
```
This eliminates the locale dependency entirely. Add a `win32ts` fallback to the existing parser for
environments where it is unavailable.

---

### PERF-01 — Move Wait out of DESKTOP semaphore category
**File:** `src/winremote/taskmanager.py:43-89`
**Problem:** `Wait` is in `TOOL_CATEGORIES["DESKTOP"]` (limit=1). `Wait(seconds=10)` holds the
exclusive desktop semaphore for 10 seconds, blocking all screenshot/click/type tools.
**Fix:** Move `"Wait"` to a new `"WAIT"` category with limit=None (or remove from TOOL_CATEGORIES
entirely since it touches no shared hardware resource):
```python
TOOL_CATEGORIES: dict[str, list[str]] = {
    "DESKTOP": [...],  # remove "Wait"
    "SHELL": [...],
    "QUERY": [...],
    # Wait needs no semaphore - it's pure sleep
}
```

---

### PERF-02 — Fix FileSearch: stream rglob instead of materializing
**File:** `src/winremote/__main__.py:1010-1043`
**Problem:** `list(p.rglob(pattern))` materializes the entire match list before slicing. On C:\ with
500k files, `limit=50` means nothing — full O(N) traversal and memory allocation.
**Fix:** Use the generator with early break:
```python
gen = p.rglob(pattern) if _tobool(recursive) else p.glob(pattern)
matches: list[Path] = []
truncated = False
for m in gen:
    matches.append(m)
    if len(matches) >= limit + 1:
        truncated = True
        matches = matches[:limit]
        break
```

---

### PERF-03 — Fix FileRead: read with limit not full file
**File:** `src/winremote/__main__.py:903-923`
**Problem:** `p.read_text(...)` reads the entire file into memory before slicing to 100KB.
A 1GB log file allocates 1GB in the process.
**Fix:**
```python
with p.open(encoding=encoding, errors="replace") as fh:
    text = fh.read(100_001)
if len(text) > 100_000:
    text = text[:100_000] + "\n\n[... truncated at 100KB]"
```

---

### PERF-04 — Fix threadpool exhaustion from blocking semaphore acquire
**File:** `src/winremote/taskmanager.py:225-234`
**Problem:** `threading.Semaphore.acquire(timeout=30)` parks the FastMCP threadpool worker for up to
30s waiting for the desktop slot. With 3-4 queued desktop tasks this drains the thread pool.
**Fix:** Move semaphore acquisition outside the worker thread using a queue. Blocked callers
should release their thread and re-enter when the slot is free. Short-term: replace the 30s
fixed timeout with a per-category timeout that matches expected tool duration. Long-term: for the
DESKTOP category, use a `queue.Queue(maxsize=1)` dispatcher running on its own thread.

---

### PERF-05 — Fix cpu_percent always 0.0
**File:** `src/winremote/process_mgr.py:23-46`
**Problem:** `psutil.process_iter(["cpu_percent"])` returns 0.0 for every process on first access
because the kernel interval sample has not elapsed. Sort-by-cpu is meaningless.
**Fix:** Add a short priming interval:
```python
# Prime the CPU counters
list(psutil.process_iter(["cpu_percent"]))
time.sleep(0.2)
# Now read actual values
procs = list(psutil.process_iter([...]))
```

---

### PERF-06 — Fix FileList: use os.scandir
**File:** `src/winremote/__main__.py:960-1000`
**Problem:** `Path.iterdir()` + `item.stat()` = one extra syscall per entry. `os.scandir` provides
cached stat metadata from the directory read itself.
**Fix:** Replace `p.iterdir()` with `os.scandir(str(p))` and use `entry.stat()` (cached) and
`entry.is_dir()` (also cached) on the `DirEntry` objects.

---

### PERF-07 — Fix process sort: use heapq.nlargest
**File:** `src/winremote/process_mgr.py:40-46`
**Problem:** `procs.sort(...)` then `procs[:limit]` is O(n log n) when O(n log k) suffices.
**Fix:**
```python
import heapq
if sort_by in ("cpu", "memory"):
    key = "cpu" if sort_by == "cpu" else "memory"
    procs = heapq.nlargest(limit, procs, key=lambda x: x[key])
else:
    procs.sort(key=lambda x: x["name"].lower())
    procs = procs[:limit]
```

---

### PERF-08 — Cache _get_system_language() result
**File:** `src/winremote/desktop.py:61-66`
**Problem:** Called on every `Snapshot` invocation. Locale does not change at runtime.
**Fix:**
```python
_SYSTEM_LANGUAGE: str | None = None

def _get_system_language() -> str:
    global _SYSTEM_LANGUAGE
    if _SYSTEM_LANGUAGE is None:
        try:
            _SYSTEM_LANGUAGE = locale.getdefaultlocale()[0] or "en_US"
        except Exception:
            _SYSTEM_LANGUAGE = "en_US"
    return _SYSTEM_LANGUAGE
```

---

### PERF-09 — Fix socket FD leak in port_check
**File:** `src/winremote/network.py:27-37`
**Problem:** `sock.close()` only reached on happy path. Exception leaves socket open until GC.
**Fix:** Use socket as context manager:
```python
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(timeout)
    result = sock.connect_ex((host, port))
    return "open" if result == 0 else f"closed (errno {result})"
```

---

### PERF-10 — Fix AnnotatedSnapshot resize: use LANCZOS
**File:** `src/winremote/__main__.py:1494-1496`
**Problem:** `img.resize((max_width, int(img.height * ratio)))` uses default resampling (BICUBIC in
Pillow ≥10). `desktop.take_screenshot` explicitly uses LANCZOS (resample=3). Inconsistent quality.
**Fix:**
```python
from PIL import Image
img = img.resize((max_width, int(img.height * ratio)), resample=Image.Resampling.LANCZOS)
```

---

### ARCH-01 — Consolidate duplicate fastmcp internals access
**File:** `src/winremote/tiers.py:127-180` and `src/winremote/__main__.py:1619-1666`
**Problem:** `_get_registered_tools` and `_remove_tool` are near-verbatim duplicated. `tiers.filter_tools()`
exists but is never called. Any fastmcp internals change requires editing two files.
**Fix:** Delete the `__main__.py` copies (`_get_registered_tools`, `_remove_tool`, `_apply_tool_filter`).
Call `tiers.filter_tools(mcp, enabled_tools)` from `__main__.py` instead of `_apply_tool_filter`.
Have `_wrap_all_tools` call `tiers._get_registered_tools(mcp)`.

---

### ARCH-02 — Remove module-level monkey-patch at import time
**File:** `src/winremote/__main__.py:45-70`
**Problem:** `_patch_fastmcp_streamable_http_get_probe()` is called at module scope (line 70), mutating
`StreamableHTTPSessionManager.handle_request` globally on import. Invisible side effect.
**Fix:** Move the call inside `cli()`, after the transport is chosen and only when `transport == "streamable-http"`:
```python
if transport == "streamable-http":
    _patch_fastmcp_streamable_http_get_probe()
```

---

### ARCH-03 — Consolidate tool metadata (tier + category co-location)
**File:** `src/winremote/tiers.py:5-57`, `src/winremote/taskmanager.py:43-89`
**Problem:** Adding one tool requires editing 3 tables in 3 files. Tables can drift silently.
**Fix:** Create a `src/winremote/tool_registry.py` that is the single source of truth:
```python
from dataclasses import dataclass

@dataclass
class ToolMeta:
    tier: int        # 1, 2, or 3
    category: str    # DESKTOP, SHELL, QUERY, FILE, NETWORK

TOOL_REGISTRY: dict[str, ToolMeta] = {
    "Snapshot":   ToolMeta(tier=1, category="DESKTOP"),
    "Click":      ToolMeta(tier=2, category="DESKTOP"),
    "Shell":      ToolMeta(tier=3, category="SHELL"),
    ...
}
```
Update `tiers.py` and `taskmanager.py` to read from `TOOL_REGISTRY`.

---

### ARCH-04 — Split god module __main__.py
**File:** `src/winremote/__main__.py` (2012 lines)
**Problem:** One file owns ~10 distinct responsibilities. No human holds this in their head.
**Fix:** Extract by domain (one PR each to keep diffs reviewable):
- `src/winremote/tools/desktop_tools.py` — Snapshot, Click, Type, Scroll, Move, Shortcut, Wait, FocusWindow, MinimizeAll, App, GetClipboard, SetClipboard, LockScreen, Notification, AnnotatedSnapshot, ScreenRecord, OCR
- `src/winremote/tools/file_tools.py` — FileRead, FileWrite, FileList, FileSearch, FileDownload, FileUpload
- `src/winremote/tools/system_tools.py` — Shell, ListProcesses, KillProcess, GetSystemInfo, ReconnectSession, PlaySound, Scrape, ServiceList/Start/Stop, TaskList/Create/Delete, EventLog
- `src/winremote/tools/network_tools.py` — Ping, PortCheck, NetConnections
- `src/winremote/tools/registry_tools.py` — RegRead, RegWrite
- `src/winremote/server.py` — server startup, middleware assembly, OAuth wiring, banner
- `src/winremote/__main__.py` — thin CLI entrypoint + install/uninstall subcommands only

---

### ARCH-05 — Extract cli() into focused helpers
**File:** `src/winremote/__main__.py:1684-1925`
**Problem:** `cli()` is a ~190-line god function. Cannot understand startup at a glance.
**Fix:** Extract:
- `_resolve_config(ctx, cfg, ...)` → returns resolved settings dict
- `_setup_oauth(mcp, store, issuer, client_id, client_secret)` → wires OAuth routes + validator
- `_build_middleware(auth_key, oauth_validator, allowlist)` → returns list[Middleware]
Leave `cli()` as ~30-line orchestration calling these helpers.

---

### ARCH-06 — Isolate fastmcp private internals in adapter module
**File:** `src/winremote/tiers.py:127-180`
**Problem:** Private attributes `_tool_manager._tools` and `_local_provider._components` accessed
from two modules. Fragile layering violation.
**Fix:** Create `src/winremote/fastmcp_compat.py` with a stable public surface:
```python
def list_tool_names(mcp) -> list[str]: ...
def remove_tool(mcp, name: str) -> None: ...
def set_tool_fn(mcp, name: str, fn) -> None: ...
```
All other modules import only from `fastmcp_compat`. The internals-poking is isolated to one file.

---

### ARCH-07 — Move _ensure_session_connected to desktop.py/session.py
**File:** `src/winremote/__main__.py:611-676`
**Problem:** Windows session management logic lives in the entry-point file. Misplaced.
**Fix:** Move to `src/winremote/desktop.py` or a new `src/winremote/session.py`. Update
`Snapshot` and `AnnotatedSnapshot` to import from there.

---

### ARCH-08 — Extract shared grab-with-reconnect helper
**File:** `src/winremote/__main__.py:161-175` and `src/winremote/__main__.py:1477-1492`
**Problem:** The grab-then-reconnect-then-retry pattern is duplicated verbatim in `Snapshot` and
`AnnotatedSnapshot`. Any bug fix needs to be applied twice.
**Fix:** Extract:
```python
def _grab_screenshot_with_reconnect() -> PIL.Image.Image:
    try:
        return ImageGrab.grab()
    except Exception as e:
        err = _ensure_session_connected()
        if err is not None:
            raise RuntimeError(str(e)) from e
        return ImageGrab.grab()  # retry once after reconnect
```

---

### ARCH-09 — Remove dead async semaphore code
**File:** `src/winremote/taskmanager.py:153-157, 162-167`
**Problem:** `_semaphores: dict[str, asyncio.Semaphore]` and `_get_semaphore()` are never called.
Abandoned async design. Dead code confuses readers about whether async tools are supported.
**Fix:** Delete `_semaphores`, `_get_semaphore`, and the `asyncio` import if unused. If async tool
support is planned, track it in a separate ticket rather than leaving dead scaffolding.

---

### QUAL-01 — Fix deferred import inconsistency
**File:** `src/winremote/__main__.py` (multiple locations: 877, 1583, 1862 etc.)
**Problem:** Some stdlib imports are deferred inside functions, some at module level, with no stated rule.
**Fix:** Move all stdlib imports to module level. Keep only genuinely optional third-party imports
(e.g. `markdownify`, `pytesseract`) deferred inside functions with a clear comment explaining why.

---

### QUAL-02 — Fix filter param naming inconsistency
**Files:** `src/winremote/process_mgr.py:11`, `src/winremote/services.py:34`,
`src/winremote/network.py:42`, `src/winremote/__main__.py` (tool signatures)
**Problem:** Parameter named `filter_name`, `filter_str`, and `filter` (shadows builtin) across modules.
**Fix:** Standardise on `filter_str` in all internal functions. Rename tool signature parameters from
`filter` to `name_filter` or `search` to avoid shadowing the `filter` builtin.

---

### QUAL-03 — Fix ScreenRecord conflicting duration clamp
**Files:** `src/winremote/__main__.py:1428` and `src/winremote/recording.py:32`
**Problem:** Tool layer clamps to `min 0.1`; recording layer clamps to `min 0.5`. Conflicting contracts.
**Fix:** Remove the clamp from `__main__.py` and let `recording.py` be the single authority.
Document the effective minimum (0.5s) in the tool docstring.

---

### QUAL-04 — Fix misleading ~75MB comment in FileUpload
**File:** `src/winremote/__main__.py:1088`
**Problem:** `max_b64_size = 100 * 1024 * 1024  # ~75MB decoded` conflates decoded and encoded sizes.
**Fix:**
```python
MAX_UPLOAD_B64_BYTES = 100 * 1024 * 1024  # base64-encoded limit (~75 MiB after decode)
```

---

### QUAL-05 — Replace inline __import__ in _NoRedirectHandler
**File:** `src/winremote/__main__.py:716-720`
**Problem:** `class _NoRedirectHandler(__import__("urllib.request").request.HTTPRedirectHandler)` is
unreadable. `__import__` used as base class and in raise expression.
**Fix:** Add `import urllib.request` and `import urllib.error` at module level and reference normally.

---

### QUAL-06 — Extract named constants
**Files:** `src/winremote/__main__.py`, `src/winremote/desktop.py`, `src/winremote/oauth.py`
**Problem:** Magic numbers: byte caps (10MB, 1MB), truncation lengths (50000, 100000), fuzzy match
thresholds (50, 60, 80), token entropy widths (32, 48).
**Fix:** Add a `src/winremote/constants.py` (or module-level constants where they live):
```python
MAX_SOUND_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_SCRAPE_RESPONSE_BYTES = 1024 * 1024
MAX_SCRAPE_MD_CHARS = 50_000
MAX_FILE_READ_CHARS = 100_000
WINDOW_MATCH_MIN_SCORE = 50
PROCESS_FILTER_MIN_SCORE = 60
PROCESS_KILL_MIN_SCORE = 80
AUTH_CODE_ENTROPY_BYTES = 32
ACCESS_TOKEN_ENTROPY_BYTES = 48
```

---

### QUAL-07 — Fix Optional[str] → str | None
**File:** `src/winremote/desktop.py:205`
**Fix:** `def focus_window(title: str | None = None, handle: int | None = None):`
Remove `from typing import Optional` if no longer used.

---

### QUAL-08 — Fix magic resample=3 → Image.Resampling.LANCZOS
**File:** `src/winremote/desktop.py:191`
**Fix:** `img = img.resize((max_width, new_height), resample=Image.Resampling.LANCZOS)`

---

### QUAL-09 — Fix registry.py winreg constant obfuscation
**File:** `src/winremote/registry.py:14-34`
**Fix:**
```python
if HAS_WINREG:
    _ROOT_KEYS = {
        "HKCR": winreg.HKEY_CLASSES_ROOT,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        ...
    }
else:
    _ROOT_KEYS = {}
```

---

### QUAL-10 — Fix ocr.py chr(39) obfuscation
**File:** `src/winremote/ocr.py:92`
**Fix:** `tmp_path.replace("'", "''")`

---

### QUAL-11 — Add query-session format comment
**File:** `src/winremote/__main__.py:611` (or `session.py` after ARCH-07)
**Fix:** Add a block comment above the parser explaining the `query session` output format and
column layout, especially the `>` current-session marker and locale state string variants.

---

### QUAL-12 — Fix services.py empty event log stderr false error
**File:** `src/winremote/services.py:112-132`
**Problem:** `Get-WinEvent` with no matching events writes "No events were found" to stderr, which
`_ps()` appends as `[STDERR] ...`, presenting a successful empty result as an error.
**Fix:** Post-process the output: if `result.returncode != 0` and `"No events were found"` is in stderr,
return `"No events matching the filter."` instead.

---

### QUAL-13 — Fix SetForegroundWindow false success report
**File:** `src/winremote/desktop.py:230-234`
**Problem:** `SetForegroundWindow` succeeds even when focus-stealing prevention denies the focus change.
Reports "Focused window ..." when focus was actually denied.
**Fix:** Verify after the call:
```python
win32gui.SetForegroundWindow(hwnd)
import time; time.sleep(0.05)
actual_fg = win32gui.GetForegroundWindow()
if actual_fg != hwnd:
    return f"Warning: focus request sent but window {hwnd} may not have focus (foreground is {actual_fg})"
```

---

### QUAL-14 — Range-check port in CLI
**File:** `src/winremote/__main__.py:1684`
**Fix:** Add `click.IntRange(1, 65535)` to the `--port` option:
```python
@click.option("--port", default=8090, type=click.IntRange(1, 65535))
```

---

### QUAL-15 — Align semaphore acquire timeout with Shell timeout
**File:** `src/winremote/taskmanager.py:230`
**Problem:** Fixed 30s acquire timeout; a Shell with timeout=300 holds the SHELL slot for 300s,
so a queued second Shell waits 30s then fails with a misleading message.
**Fix:** Make the acquire timeout configurable per category, defaulting to 60s for SHELL:
```python
CATEGORY_ACQUIRE_TIMEOUTS = {
    "DESKTOP": 30,
    "SHELL": 60,
    "QUERY": 15,
    "FILE": 30,
    "NETWORK": 15,
}
```
