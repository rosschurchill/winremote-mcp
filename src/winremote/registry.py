"""Windows Registry operations."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import winreg

    HAS_WINREG = True
    _ROOT_KEYS = {
        "HKCR": winreg.HKEY_CLASSES_ROOT,
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKU": winreg.HKEY_USERS,
        "HKEY_USERS": winreg.HKEY_USERS,
        "HKCC": winreg.HKEY_CURRENT_CONFIG,
        "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
    }
    _REG_TYPES = {
        "REG_SZ": winreg.REG_SZ,
        "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
        "REG_DWORD": winreg.REG_DWORD,
        "REG_QWORD": winreg.REG_QWORD,
        "REG_BINARY": winreg.REG_BINARY,
        "REG_MULTI_SZ": winreg.REG_MULTI_SZ,
    }
else:
    HAS_WINREG = False
    _ROOT_KEYS: dict = {}
    _REG_TYPES: dict = {}

# Keys that reg_write is allowed to target without --allow-reg-write-all.
# Restricted to per-user software and environment to prevent system-level damage.
SAFE_REG_WRITE_PREFIXES: tuple[str, ...] = (
    "HKCU\\SOFTWARE\\",
    "HKEY_CURRENT_USER\\SOFTWARE\\",
    "HKCU\\Environment",
    "HKEY_CURRENT_USER\\Environment",
)

# Autostart/persistence subtrees that are explicitly blocked even within the safe allowlist.
# These keys enable persistence mechanisms and must require --allow-reg-write-all.
_DENIED_REG_WRITE_PREFIXES: tuple[str, ...] = (
    "HKCU\\SOFTWARE\\MICROSOFT\\WINDOWS\\CURRENTVERSION\\RUN",
    "HKEY_CURRENT_USER\\SOFTWARE\\MICROSOFT\\WINDOWS\\CURRENTVERSION\\RUN",
    "HKCU\\SOFTWARE\\MICROSOFT\\WINDOWS NT\\CURRENTVERSION\\WINLOGON",
    "HKEY_CURRENT_USER\\SOFTWARE\\MICROSOFT\\WINDOWS NT\\CURRENTVERSION\\WINLOGON",
)

# Set to True by --allow-reg-write-all (tier3 only)
allow_reg_write_all: bool = False


def _parse_key(key: str) -> tuple:
    """Parse 'HKLM\\SOFTWARE\\...' into (root_handle, subkey_path)."""
    parts = key.split("\\", 1)
    root_name = parts[0].upper()
    subkey = parts[1] if len(parts) > 1 else ""
    root = _ROOT_KEYS.get(root_name)
    if root is None:
        raise ValueError(f"Unknown root key: {root_name}. Use HKCR, HKCU, HKLM, HKU, or HKCC.")
    return root, subkey


def reg_read(key: str, value_name: str) -> str:
    """Read a registry value."""
    if not HAS_WINREG:
        return "Error: Registry operations only available on Windows."
    try:
        root, subkey = _parse_key(key)
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as k:
            data, reg_type = winreg.QueryValueEx(k, value_name)
            return f"Value: {data!r} (type: {reg_type})"
    except FileNotFoundError:
        return f"Error: Key or value not found: {key}\\{value_name}"
    except Exception as e:
        return f"RegRead error: {e}"


def reg_write(key: str, value_name: str, data: str, reg_type: str = "REG_SZ") -> str:
    """Write a registry value."""
    if not HAS_WINREG:
        return "Error: Registry operations only available on Windows."
    if not allow_reg_write_all:
        key_upper = key.upper().replace("/", "\\")
        if not any(key_upper.startswith(prefix.upper()) for prefix in SAFE_REG_WRITE_PREFIXES):
            return (
                f"RegWrite blocked: '{key}' is outside the safe write allowlist "
                f"({', '.join(SAFE_REG_WRITE_PREFIXES)}). "
                "Use --allow-reg-write-all to permit writes to arbitrary keys (requires tier3)."
            )
        if any(key_upper.startswith(prefix.upper()) for prefix in _DENIED_REG_WRITE_PREFIXES):
            return (
                f"RegWrite blocked: '{key}' targets an autostart/persistence subtree. "
                "Use --allow-reg-write-all to permit writes to this key (requires tier3)."
            )
    try:
        root, subkey = _parse_key(key)
        rtype = _REG_TYPES.get(reg_type.upper())
        if rtype is None:
            return f"Error: Unknown type '{reg_type}'. Use: {', '.join(_REG_TYPES.keys())}"

        # Convert data based on type
        if reg_type.upper() == "REG_DWORD":
            data = int(data)
        elif reg_type.upper() == "REG_QWORD":
            data = int(data)
        elif reg_type.upper() == "REG_MULTI_SZ":
            data = data.split("|")

        with winreg.CreateKey(root, subkey) as k:
            winreg.SetValueEx(k, value_name, 0, rtype, data)
            return f"Written {value_name} = {data!r} to {key}"
    except Exception as e:
        return f"RegWrite error: {e}"
