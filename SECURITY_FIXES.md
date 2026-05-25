# Security & Reliability Fixes

This fork contains a full audit and remediation of winremote-mcp.
All 44 issues identified in a five-pillar code review (Security · Architecture · Performance · Reliability · Quality) have been resolved.
**135/135 tests pass.**

---

## Critical Security Fixes

### SEC-01 — Unconditional remote-bind authentication guard
**File:** `config.py`, `__main__.py`

The original code included an `--allow-insecure-remote` flag that bypassed authentication checks when binding to non-loopback addresses. This was removed entirely. The guard is now unconditional: binding to any non-loopback address without an `--auth-key` or OAuth credentials raises a hard error at startup.

### SEC-02 — Timing-safe API key comparison
**File:** `auth.py`

API key comparison used Python's `==` operator, which short-circuits on the first mismatched byte. This enables a timing side-channel attack to recover the key one byte at a time. Fixed with `hmac.compare_digest()`.

```python
# Before (vulnerable)
if auth_header == f"Bearer {self.auth_key}":

# After (safe)
if hmac.compare_digest(auth_header, f"Bearer {self.auth_key}"):
```

### SEC-03 — DNS rebinding / TOCTOU in URL fetch
**File:** `security.py`, `__main__.py`

`validate_fetch_url()` resolved the hostname to check if it was a private IP, but the actual HTTP connection re-resolved the hostname. A DNS rebinding attack could cause the validation to see a public IP while the connection goes to `127.0.0.1`. Fixed by resolving the hostname once, pinning the IP, and passing it directly to the connection with the original hostname in the `Host` header.

### SEC-04 — Path traversal in all file operations
**File:** `__main__.py`

All six file tools (`FileRead`, `FileWrite`, `FileList`, `FileSearch`, `FileDownload`, `FileUpload`) accepted arbitrary paths with no sandboxing. A `../../../` traversal could read or write anywhere on the filesystem. Fixed with `_check_path()` which resolves the path and enforces it stays within a configurable `--file-root` (defaults to the user's home directory).

### SEC-05 — Unrestricted Windows registry writes
**File:** `registry.py`

`RegWrite` accepted any registry path including system-critical keys (`HKLM\SYSTEM\CurrentControlSet`, `HKLM\SOFTWARE`). An attacker or misbehaving LLM call could modify system keys, disable services, or create persistence. Fixed with a `SAFE_REG_WRITE_PREFIXES` allowlist restricting writes to `HKCU\SOFTWARE\` and `HKCU\Environment` by default. A `--allow-reg-write-all` flag (requires `--enable-tier3`) allows override.

### SEC-06 — UNC path injection in PlaySound
**File:** `__main__.py`

`PlaySound(path=...)` accepted `\\server\share\evil.wav` UNC paths. On Windows, opening a UNC path forces NTLM authentication to the remote server, leaking the machine's NetNTLM hash to an attacker-controlled server. Fixed by rejecting paths starting with `\\` or `//`, and applying `_check_path()` sandboxing.

### SEC-07 — Command injection via unvalidated schtasks schedule
**File:** `services.py`

`TaskCreate` passed the `schedule` parameter directly into a `schtasks /Create /SC '{schedule}'` command without validation. An attacker could inject additional schtasks flags or shell operators. Fixed with a `VALID_SCHEDULES` frozenset allowlist.

---

## High Reliability Fixes

### REL-01 — Subprocess cancellation (Shell tool)
**File:** `__main__.py`

`CancelTask` on a running `Shell` tool had no effect — the subprocess ran to completion regardless. Rewrote `Shell` to use `subprocess.Popen` with a polling loop that calls `proc.kill()` when the task's cancel event is set.

### REL-02 — Thread-safe TaskInfo status transitions
**File:** `taskmanager.py`

`TaskInfo` status writes (`RUNNING`, `COMPLETED`, `FAILED`) were not protected under a lock, creating race conditions between the executing thread and a cancelling thread. All terminal-state writes are now guarded with `with task._lock: if task.status != CANCELLED:`.

### REL-03 — Atomic FileWrite
**File:** `__main__.py`

`FileWrite` wrote directly to the target path, leaving a partial file if the process was interrupted mid-write. Fixed with `tempfile.mkstemp()` + `os.replace()` for atomicity. `UnicodeEncodeError` is now caught and reported distinctly.

### REL-04 — OAuth token store race conditions
**File:** `oauth.py`

`del store.tokens[token]` raised `KeyError` under concurrent access. Replaced with `.pop(token, None)`. Added `_evict_expired()` sweep and a 1000-entry cap to prevent unbounded memory growth.

### REL-05 — PlaySound URL download deadline
**File:** `__main__.py`

Downloading a URL for `PlaySound` had no total time limit. A slow server could block the tool indefinitely. Added a 30-second wall-clock deadline with cleanup of any partial temp file on timeout.

### REL-06 — UTF-8 encoding on all subprocess calls
**Files:** `__main__.py`, `services.py`, `desktop.py`

All `subprocess.run(text=True)` calls now include `encoding="utf-8", errors="replace"`. PowerShell commands are prefixed with `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;` to prevent garbled output on non-English Windows (Chinese, Japanese, etc.).

### REL-07 — Clipboard retry on `OpenClipboard` failure
**File:** `desktop.py`

`win32clipboard.OpenClipboard()` fails if another process holds the clipboard. Instead of immediately raising, the code now retries up to 10 times with 10ms backoff before propagating the error.

### REL-08 — Locale-independent session detection
**File:** `desktop.py`

`_ensure_session_connected` parsed `query session` text output which varies by Windows locale (e.g. Chinese: `已断开` vs English: `Disc`). Rewrote to prefer the `win32ts.WTSEnumerateSessions()` API (state 4 = `WTSDisconnected` regardless of locale) with the text parser as a fallback.

---

## Performance Fixes

| ID | Fix |
|----|-----|
| PERF-01 | Removed `Wait` from the desktop semaphore (it holds no exclusive resource) |
| PERF-02 | `FileSearch`: generator + early-break instead of `list(p.rglob(...))` |
| PERF-03 | `FileRead`: reads at most 100KB instead of the entire file |
| PERF-04 | Per-category semaphore acquire timeouts: `SHELL=60s`, `DESKTOP=30s`, `FILE=30s`, `QUERY=15s`, `NETWORK=15s` |
| PERF-05 | `ListProcesses`: primes CPU counters with a 200ms sleep before sampling |
| PERF-06 | `FileList`: uses `os.scandir()` for cached `stat` metadata |
| PERF-07 | `ListProcesses`: uses `heapq.nlargest()` instead of sort+slice |
| PERF-08 | `_get_system_language()`: cached after first call |
| PERF-09 | `port_check()`: socket wrapped in `with` context manager (fixes FD leak) |
| PERF-10 | `AnnotatedSnapshot` resize: `resample=Image.Resampling.LANCZOS` (was magic `3`) |

---

## Architecture Improvements

### New files
| File | Purpose |
|------|---------|
| `src/winremote/app.py` | Central FastMCP instance and shared helpers |
| `src/winremote/tool_registry.py` | Single source of truth for tool tier + concurrency category |
| `src/winremote/fastmcp_compat.py` | Isolates all access to fastmcp private internals |
| `src/winremote/tools/desktop_tools.py` | Desktop control tools |
| `src/winremote/tools/file_tools.py` | File operation tools |
| `src/winremote/tools/system_tools.py` | System management tools |
| `src/winremote/tools/network_tools.py` | Network diagnostic tools |
| `src/winremote/tools/registry_tools.py` | Registry tools |

### `__main__.py` reduced from ~2000 → 664 lines
The god module is now a thin CLI entry point. Tool definitions live in their domain modules.

### ARCH-02 — Monkey-patch moved inside CLI
`_patch_fastmcp_streamable_http_get_probe()` was called at module import time, mutating a global on every import. It now only runs when the streamable-http transport is actually started.

### ARCH-03 — Tool registry as single source of truth
Previously, adding a new tool required editing three separate tables in three files (`tiers.py`, `taskmanager.py`, and the tool definition). `tool_registry.py` is now the single source; `tiers.py` and `taskmanager.py` derive their tables from it.

---

## Quality Fixes

| ID | Fix |
|----|-----|
| QUAL-01 | All stdlib imports moved to module level |
| QUAL-02 | `filter` parameter renamed to `name_filter`/`filter_str` (was shadowing builtin) |
| QUAL-03 | `ScreenRecord` duration clamp removed from tool layer; `recording.py` is sole authority |
| QUAL-04 | `MAX_UPLOAD_B64_BYTES` named constant replaces inline magic number with misleading comment |
| QUAL-05 | `__import__("urllib.request")` inline hack replaced with top-level import |
| QUAL-06 | All byte caps, truncation limits, fuzzy match thresholds extracted to named constants |
| QUAL-07 | `Optional[str]` → `str \| None` in desktop.py |
| QUAL-08 | `resample=3` → `Image.Resampling.LANCZOS` |
| QUAL-09 | `getattr(globals().get("winreg"), ...)` pattern replaced with clean `if HAS_WINREG:` block |
| QUAL-10 | `chr(39)` → `"'"` |
| QUAL-11 | Added block comment documenting `query session` output format and locale variants |
| QUAL-12 | `EventLog` now returns a clean empty message instead of surfacing `Get-WinEvent` stderr |
| QUAL-13 | `SetForegroundWindow` verifies focus was granted after the call; reports truthfully if denied |
| QUAL-14 | `--port` option validates range 1–65535 |
| QUAL-15 | Shell semaphore acquire timeout aligned to 60s (was 30s, causing spurious timeouts) |

---

## New CLI Flags

| Flag | Description |
|------|-------------|
| `--file-root <path>` | Sandbox all file operations to this directory tree (default: user home) |
| `--allow-reg-write-all` | Allow registry writes outside `HKCU\SOFTWARE` (requires `--enable-tier3`) |

---

## Breaking Changes

- `--allow-insecure-remote` flag removed. There is no override for the non-loopback + no-auth guard.
- `ListProcesses` parameter renamed `filter` → `name_filter`
- `ServiceList`, `TaskList` parameter renamed `filter` → `name_filter`
- `NetConnections` parameter renamed `filter` → `filter_str`
