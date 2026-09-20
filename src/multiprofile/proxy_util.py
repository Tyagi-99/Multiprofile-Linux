"""Proxy string parsing and connectivity testing."""
from __future__ import annotations

import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


def parse_proxy(custom_proxy: str, proxy_type: str = "custom") -> dict[str, Any] | None:
    """
    Parse proxy strings into a structured dict.
    Supports: ip:port, ip:port:user:pass, user:pass@host:port,
              http://, https://, socks5://, socks5h://
    Also accepts host/port/user/pass already split via custom_proxy empty + fields in type dict... 
    For type none/github returns None.
    """
    if proxy_type in (None, "", "none", "github"):
        # still allow parse if custom string given with type custom implied
        if proxy_type != "custom" and not (custom_proxy or "").strip():
            return None

    raw = (custom_proxy or "").strip()
    if not raw:
        return None

    scheme = "http"
    username = None
    password = None
    host = None
    port = None

    lower = raw.lower()
    if "://" in raw:
        # URL form
        # socks5h://user:pass@host:port
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "http").lower()
        if scheme == "socks5h":
            scheme = "socks5"
        if scheme == "https":
            scheme = "http"  # Chrome --proxy-server uses http= for HTTPS CONNECT
        host = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    elif "@" in raw:
        # user:pass@host:port
        auth, _, hostpart = raw.rpartition("@")
        if ":" in auth:
            username, _, password = auth.partition(":")
        else:
            username = auth
        if ":" in hostpart:
            host, _, port_s = hostpart.rpartition(":")
            try:
                port = int(port_s)
            except ValueError:
                return None
        else:
            host = hostpart
    else:
        # ip:port or ip:port:user:pass  (also host:port:user:pass)
        parts = raw.split(":")
        if len(parts) == 2:
            host, port_s = parts
            try:
                port = int(port_s)
            except ValueError:
                return None
        elif len(parts) == 4:
            host, port_s, username, password = parts
            try:
                port = int(port_s)
            except ValueError:
                return None
        elif len(parts) == 3:
            # host:port:user (no pass) — uncommon
            host, port_s, username = parts
            try:
                port = int(port_s)
            except ValueError:
                return None
        else:
            # Maybe socks5:// already handled; try scheme prefix without ://
            m = re.match(r"^(socks5h?|https?|socks4):(.+)$", raw, re.I)
            if m:
                return parse_proxy(f"{m.group(1)}://{m.group(2)}", "custom")
            return None

    if not host or not port:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None

    # Infer scheme from proxy_type hint if raw had no scheme
    if "://" not in raw and proxy_type in ("socks5", "socks5h", "http", "https"):
        scheme = "socks5" if proxy_type.startswith("socks") else "http"

    return {
        "scheme": scheme,
        "type": scheme,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
    }


def proxy_server_arg(parsed: dict[str, Any]) -> str:
    """Chrome --proxy-server value (no credentials)."""
    if not parsed:
        return ""
    scheme = (parsed.get("scheme") or parsed.get("type") or "http").lower()
    if scheme in ("socks5h",):
        scheme = "socks5"
    if scheme == "https":
        scheme = "http"
    host = parsed["host"]
    port = parsed["port"]
    if scheme == "socks5":
        return f"socks5://{host}:{port}"
    if scheme == "socks4":
        return f"socks4://{host}:{port}"
    return f"{host}:{port}"


def test_proxy(parsed: dict[str, Any], timeout: float = 8) -> dict[str, Any]:
    """TCP connect + optional HTTP GET via urllib ProxyHandler."""
    if not parsed:
        return {"ok": False, "success": False, "error": "No proxy configured", "latencyMs": None}

    host = parsed["host"]
    port = int(parsed["port"])
    scheme = (parsed.get("scheme") or "http").lower()
    t0 = time.monotonic()

    # TCP connect
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        return {
            "ok": False,
            "success": False,
            "error": f"TCP connect failed: {e}",
            "latencyMs": int((time.monotonic() - t0) * 1000),
            "host": host,
            "port": port,
        }

    tcp_ms = int((time.monotonic() - t0) * 1000)
    egress_ip = None
    http_error = None

    # HTTP GET through proxy (skip for socks — urllib needs socks support)
    if scheme in ("http", "https", ""):
        try:
            user = parsed.get("username")
            password = parsed.get("password")
            if user and password:
                proxy_url = f"http://{user}:{password}@{host}:{port}"
            elif user:
                proxy_url = f"http://{user}@{host}:{port}"
            else:
                proxy_url = f"http://{host}:{port}"
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(
                "http://api.ipify.org/?format=json",
                headers={"User-Agent": "multiprofile-native/1.1.0"},
            )
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "ignore")
                try:
                    egress_ip = json_loads_ip(body)
                except Exception:
                    egress_ip = body.strip()[:64] or None
        except Exception as e:
            http_error = str(e)

    latency = int((time.monotonic() - t0) * 1000)
    ok = True  # TCP succeeded
    result = {
        "ok": ok,
        "success": ok,
        "latencyMs": latency,
        "tcpMs": tcp_ms,
        "egress_ip": egress_ip,
        "ip": egress_ip or host,
        "host": host,
        "port": port,
        "scheme": scheme,
        "error": http_error,
        "note": None if egress_ip else ("TCP OK; HTTP egress check skipped/failed" + (f": {http_error}" if http_error else " (socks)")),
    }
    return result


def json_loads_ip(body: str) -> str | None:
    import json

    data = json.loads(body)
    if isinstance(data, dict) and "ip" in data:
        return str(data["ip"])
    return None
