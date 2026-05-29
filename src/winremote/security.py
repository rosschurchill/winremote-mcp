"""Security helpers: IP allowlist parsing, URL validation, and middleware."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def _is_private_target(ip_obj: ipaddress._BaseAddress) -> bool:
    """Return True for non-public targets that should not be fetched by default."""
    return any(
        (
            ip_obj.is_private,
            ip_obj.is_loopback,
            ip_obj.is_link_local,
            ip_obj.is_multicast,
            ip_obj.is_reserved,
            ip_obj.is_unspecified,
        )
    )


def _resolve_and_validate(url: str, *, allow_private: bool = False) -> tuple[bool, str, str | None]:
    """Resolve URL hostname once and validate all addresses.

    Returns (ok, reason, pinned_ip). Callers should connect to pinned_ip instead
    of re-resolving the hostname to eliminate DNS rebinding TOCTOU.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "Only http and https URL schemes are allowed", None
    if not parsed.hostname:
        return False, "URL must include a hostname", None
    if parsed.username or parsed.password:
        return False, "Credentials in URLs are not allowed", None

    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"Could not resolve hostname: {exc}", None

    pinned_ip = None
    for info in infos:
        raw_addr = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(raw_addr)
        except ValueError:
            return False, f"Could not validate resolved address: {raw_addr}", None
        if not allow_private and _is_private_target(ip_obj):
            return False, f"Private, loopback, link-local, multicast, and reserved addresses are blocked: {ip_obj}", None
        if pinned_ip is None:
            pinned_ip = raw_addr

    if pinned_ip is None:
        return False, "No addresses resolved for hostname", None

    return True, "", pinned_ip


def validate_fetch_url(url: str, *, allow_private: bool = False) -> tuple[bool, str]:
    """Validate a user-provided URL before server-side fetching.

    Only HTTP(S) URLs are allowed. Private, loopback, link-local, multicast,
    reserved, and unspecified targets are blocked by default to prevent SSRF
    against local services and internal networks.
    """
    ok, reason, _ = _resolve_and_validate(url, allow_private=allow_private)
    return ok, reason


def is_loopback_bind_host(host: str) -> bool:
    """Return True if host binds only to loopback interfaces."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_ip_allowlist(raw_entries: list[str]) -> list[ipaddress._BaseNetwork]:
    """Parse allowlist entries of single IPs and CIDR ranges."""
    parsed: list[ipaddress._BaseNetwork] = []
    errors: list[str] = []

    for entry in raw_entries:
        value = entry.strip()
        if not value:
            continue
        try:
            if "/" in value:
                parsed.append(ipaddress.ip_network(value, strict=False))
            else:
                ip_obj = ipaddress.ip_address(value)
                suffix = "/32" if ip_obj.version == 4 else "/128"
                parsed.append(ipaddress.ip_network(f"{ip_obj}{suffix}", strict=False))
        except ValueError as exc:
            errors.append(f"{entry}: {exc}")

    if errors:
        raise ValueError("Invalid IP allowlist entries: " + "; ".join(errors))

    return parsed


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """Restrict access to configured client IP networks."""

    def __init__(self, app, allowlist: list[ipaddress._BaseNetwork]):
        super().__init__(app)
        self.allowlist = allowlist

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        client = request.client
        if client is None:
            return JSONResponse({"error": "Forbidden: missing client address"}, status_code=403)

        try:
            client_ip = ipaddress.ip_address(client.host)
        except ValueError:
            return JSONResponse({"error": f"Forbidden: invalid client address {client.host}"}, status_code=403)

        if not any(client_ip in net for net in self.allowlist):
            return JSONResponse(
                {
                    "error": f"Forbidden: client IP {client_ip} is not in allowlist",
                },
                status_code=403,
            )

        return await call_next(request)
