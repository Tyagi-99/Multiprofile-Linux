"""CDP-level browser identity configuration.

Sets navigator.userAgent, Sec-CH-UA-* HTTP headers, and navigator.platform
consistently via Chrome DevTools Protocol so that JS-level and HTTP-level
identity match.

Why this matters:
    --user-agent only sets the initial UA string.
    Sec-CH-UA-Platform, Sec-CH-UA-Arch, Sec-CH-UA-Bitness are sent by Chrome
    itself at the network layer.  If the JS UA says 'Windows' but the HTTP
    headers say 'Linux', advanced bot-detection identifies the mismatch.

    Network.setUserAgentOverride with userAgentMetadata aligns both surfaces.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import time
import urllib.request
from typing import Any

log = logging.getLogger("multiprofile.browser.cdp_identity")

_CDP_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Low-level CDP helpers
# ---------------------------------------------------------------------------

def _cdp_get(port: int, path: str) -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, headers={"Host": "localhost"})
    with urllib.request.urlopen(req, timeout=_CDP_TIMEOUT) as r:
        return json.loads(r.read())


def _cdp_ws_send(port: int, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Send one CDP command over a raw WebSocket and return the response."""
    import threading

    try:
        targets = _cdp_get(port, "/json")
    except Exception as e:
        log.debug("CDP /json failed on port %s: %s", port, e)
        return None

    ws_url: str | None = None
    for t in targets:
        if t.get("type") == "page":
            ws_url = t.get("webSocketDebuggerUrl")
            break
    if not ws_url:
        return None

    # Parse ws://127.0.0.1:<port>/devtools/page/<id>
    m = re.match(r"ws://([^/]+)(.*)", ws_url)
    if not m:
        return None
    host_port = m.group(1)
    path = m.group(2)
    host, _, port_s = host_port.partition(":")
    ws_port = int(port_s) if port_s else port

    result: dict[str, Any] | None = None
    error_box: list[Exception] = []

    def _run():
        nonlocal result
        try:
            import websocket  # type: ignore
        except ImportError:
            error_box.append(ImportError("websocket-client not installed; install python3-websocket"))
            return
        try:
            ws = websocket.WebSocket()
            ws.connect(ws_url, timeout=_CDP_TIMEOUT)
            msg_id = 1
            payload = json.dumps({"id": msg_id, "method": method, "params": params})
            ws.send(payload)
            deadline = time.monotonic() + _CDP_TIMEOUT
            while time.monotonic() < deadline:
                raw = ws.recv()
                if not raw:
                    break
                obj = json.loads(raw)
                if obj.get("id") == msg_id:
                    result = obj
                    break
            ws.close()
        except Exception as e:
            error_box.append(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=_CDP_TIMEOUT + 1)

    if error_box:
        log.debug("CDP WS error: %s", error_box[0])
    return result


# ---------------------------------------------------------------------------
# Brand list builder
# ---------------------------------------------------------------------------

def _build_brands(chrome_version: str) -> list[dict[str, str]]:
    """Return Sec-CH-UA brands list for a given Chrome major version.

    The real list varies; this produces a realistic set.
    """
    major = chrome_version.split(".")[0] if "." in chrome_version else chrome_version
    try:
        major_int = int(major)
    except ValueError:
        major_int = 131
    # Typical pattern: "Google Chrome", "Chromium", and a dummy brand
    return [
        {"brand": "Google Chrome", "version": str(major_int)},
        {"brand": "Chromium", "version": str(major_int)},
        {"brand": "Not-A.Brand", "version": "99"},
    ]


def _build_full_version_list(chrome_version: str) -> list[dict[str, str]]:
    major = chrome_version.split(".")[0] if "." in chrome_version else chrome_version
    try:
        major_int = int(major)
    except ValueError:
        major_int = 131
    full = chrome_version if "." in chrome_version else f"{major_int}.0.0.0"
    return [
        {"brand": "Google Chrome", "version": full},
        {"brand": "Chromium", "version": full},
        {"brand": "Not-A.Brand", "version": "99.0.0.0"},
    ]


# ---------------------------------------------------------------------------
# UA metadata extractor from fingerprint
# ---------------------------------------------------------------------------

def _extract_ua_metadata(fingerprint: dict[str, Any]) -> dict[str, Any]:
    """Build CDP userAgentMetadata from the fingerprint dict."""
    platform = fingerprint.get("platform") or "Win32"
    ch_platform = fingerprint.get("ch_platform") or (
        "Windows" if "Win" in platform else
        "macOS" if "Mac" in platform else
        "Linux"
    )
    architecture = fingerprint.get("architecture") or "x86"
    bitness = fingerprint.get("bitness") or "64"
    platform_version = fingerprint.get("platformVersion") or "15.0.0"
    is_mobile = fingerprint.get("touch", 0) > 0

    # Extract Chrome version from UA string
    ua_str = fingerprint.get("userAgent") or ""
    chrome_version = "131.0.0.0"
    m = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", ua_str)
    if m:
        chrome_version = m.group(1)

    # Use pre-built brands from fingerprint if available, else generate
    brands = fingerprint.get("ch_brands") or _build_brands(chrome_version)
    full_version_list = fingerprint.get("ch_full_version_list") or _build_full_version_list(chrome_version)

    return {
        "brands": brands,
        "fullVersionList": full_version_list,
        "platform": ch_platform,
        "platformVersion": platform_version,
        "architecture": architecture,
        "model": fingerprint.get("model") or "",
        "mobile": is_mobile,
        "bitness": bitness,
        "wow64": False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_ua_override(
    cdp_port: int,
    fingerprint: dict[str, Any],
    ua_string: str,
) -> bool:
    """Apply UA + client-hint metadata via CDP Network.setUserAgentOverride.

    Returns True if the override was applied, False if it failed
    (e.g. websocket-client not available — non-fatal).
    """
    if not ua_string:
        ua_string = fingerprint.get("userAgent") or ""
    if not ua_string:
        return False

    metadata = _extract_ua_metadata(fingerprint)
    language = fingerprint.get("language") or "en-US"

    params = {
        "userAgent": ua_string,
        "acceptLanguage": f"{language},en;q=0.9",
        "platform": fingerprint.get("platform") or "Win32",
        "userAgentMetadata": metadata,
    }

    result = _cdp_ws_send(cdp_port, "Network.setUserAgentOverride", params)
    if result and result.get("error"):
        log.warning("CDP UA override error: %s", result["error"])
        return False
    if result is not None:
        log.info(
            "CDP UA override applied: platform=%s arch=%s bitness=%s",
            metadata["platform"],
            metadata["architecture"],
            metadata["bitness"],
        )
        return True
    return False
