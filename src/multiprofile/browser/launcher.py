from __future__ import annotations
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..cookies_store import load_cookies
from ..fingerprint import build_fingerprint, normalize_seed
from ..proxy_util import parse_proxy, proxy_server_arg
from ..storage.profiles import get_profile, set_runtime, update_profile
from ..storage.xdg import chrome_profile_dir
from . import manager as browser_manager
from .sandbox import sandbox_chrome_args
from .cdp_window import allocate_localhost_port, wait_for_cdp
from .grid import Rect
from .runtime_state import LaunchState, clear_runtime, set_launch_state, upsert_runtime
from .window_layout import chrome_geometry_args  # weak hint only; no x11 place

log = logging.getLogger("multiprofile.browser")


def extension_src() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "extension",
        Path("/usr/share/multiprofile/extension"),
        Path(__file__).resolve().parents[3] / "src" / "extension",
        Path(os.environ["MULTIPROFILE_EXTENSION"]) if os.environ.get("MULTIPROFILE_EXTENSION") else None,
    ]
    for c in candidates:
        if c and (c / "manifest.json").exists():
            return c
    raise FileNotFoundError(
        "Anti-detect extension not found. Expected at src/extension or /usr/share/multiprofile/extension"
    )


def find_chrome() -> str:
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise FileNotFoundError(
        "Unable to start browser process.\n"
        "Reason: Chromium/Chrome executable was not found on PATH.\n"
        "Suggested action: sudo apt install chromium-browser  OR  install Google Chrome."
    )


def _resolve_proxy(profile: dict[str, Any], direct: bool) -> dict[str, Any] | None:
    """Apply proxy whenever custom proxy configured unless direct=true."""
    if direct:
        return None
    ptype = profile.get("proxyType") or "none"
    custom = (profile.get("customProxy") or "").strip()
    if ptype == "custom" or custom:
        parsed = parse_proxy(custom, "custom") if custom else None
        if not parsed and profile.get("proxyHost") and profile.get("proxyPort"):
            parsed = {
                "scheme": "http",
                "type": "http",
                "host": profile["proxyHost"],
                "port": int(profile["proxyPort"]),
                "username": profile.get("proxyUser") or None,
                "password": profile.get("proxyPass") or None,
            }
        return parsed
    return None


def _prepare_extension(profile: dict[str, Any], fingerprint: dict[str, Any]) -> Path:
    src = extension_src()
    dest = chrome_profile_dir(profile["id"]) / "extension"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    evasion = dest / "evasion.js"
    if evasion.exists():
        text = evasion.read_text(encoding="utf-8", errors="ignore")
        tz = profile.get("timezone") or fingerprint.get("timezone") or "America/New_York"
        seed = str(normalize_seed(profile))
        ip = profile.get("allocatedIp") or ""
        text = text.replace("__PROFILE_TIMEZONE__", tz)
        text = text.replace("__PROFILE_ALLOCATED_IP__", ip)
        text = text.replace("__PROFILE_SEED__", seed)

        cfg = dict(fingerprint)
        cfg["webrtcMode"] = profile.get("webrtcMode") or fingerprint.get("webrtcMode") or "default"
        cfg_json = json.dumps(cfg, separators=(",", ":"))

        # Inject as real JS object (never splice JSON into a quoted string)
        inline = f"var __MP_INLINE_CFG__ = {cfg_json};\nwindow.__MP_PROFILE__ = __MP_INLINE_CFG__;\n"
        if "/*__PROFILE_CONFIG_JSON__*/" in text or "__PROFILE_CONFIG_JSON__" in text:
            text = inline + text
            # neutralize leftover quoted placeholder if any
            text = text.replace('"__PROFILE_CONFIG_JSON__"', "null")
            text = text.replace("'__PROFILE_CONFIG_JSON__'", "null")
        else:
            text = inline + text
        evasion.write_text(text, encoding="utf-8")

    # cookies.json for background to apply
    cookies = load_cookies(profile["id"])
    (dest / "cookies.json").write_text(json.dumps(cookies, indent=2), encoding="utf-8")

    # proxy.json for extension auth / chrome.proxy
    proxy_info = None
    custom = (profile.get("customProxy") or "").strip()
    if (profile.get("proxyType") == "custom" or custom) and custom:
        proxy_info = parse_proxy(custom, "custom")
    elif profile.get("proxyHost") and profile.get("proxyPort"):
        proxy_info = {
            "scheme": "http",
            "host": profile["proxyHost"],
            "port": int(profile["proxyPort"]),
            "username": profile.get("proxyUser") or None,
            "password": profile.get("proxyPass") or None,
        }
    if proxy_info:
        (dest / "proxy.json").write_text(
            json.dumps(
                {
                    "host": proxy_info["host"],
                    "port": proxy_info["port"],
                    "type": proxy_info.get("scheme") or "http",
                    "username": proxy_info.get("username") or "",
                    "password": proxy_info.get("password") or "",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    return dest


def launch_profile(profile_id: str, url: str | None = None, direct: bool = False, cdp: bool = False, _slot_held: bool = False, window_rect: Rect | None = None) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        return {"error": f"Profile not found: {profile_id}", "errors": ["profile_not_found"], "ok": False}

    slot_held = _slot_held
    if not slot_held:
        if not browser_manager.acquire_slot(profile_id, timeout=120.0):
            return {
                "ok": False,
                "error": "Too many profiles running; wait for a free concurrency slot",
                "errors": ["concurrency_limit"],
            }
        slot_held = True

    # Ensure fingerprint cached
    fingerprint = profile.get("fingerprint") if isinstance(profile.get("fingerprint"), dict) else None
    if not fingerprint:
        fingerprint = build_fingerprint(profile)
        try:
            update_profile(profile_id, {"fingerprint": fingerprint, "seed": fingerprint.get("seed")})
            profile = get_profile(profile_id) or profile
        except Exception:
            pass

    try:
        chrome = find_chrome()
        ext = _prepare_extension(profile, fingerprint)
    except FileNotFoundError as e:
        if slot_held:
            browser_manager.release_slot(profile_id)
        return {"ok": False, "error": str(e), "errors": ["dependency_missing"]}

    user_dir = chrome_profile_dir(profile_id) / "user-data"
    user_dir.mkdir(parents=True, exist_ok=True)
    start = url or profile.get("startUrl") or "https://example.com"
    ua = profile.get("userAgent") or ""

    notes: list[str] = []
    parsed_proxy = _resolve_proxy(profile, direct=direct)
    webrtc_mode = profile.get("webrtcMode") or "default"

    if profile.get("proxyType") == "github" and not direct:
        notes.append(
            "GitHub Cloud Runner proxy is not implemented in native launcher; "
            "launch with direct=true or switch to custom proxy."
        )

    settings = get_settings()
    sandbox_args, sandbox_status = sandbox_chrome_args(chrome)
    notes.append(f"Sandbox: {sandbox_status.reason}")

    # Anti-detect exclusivity (AdsPower-class): only our extension loads by default.
    # Web Store / extra extensions are deferred — opt-in env only, not the product default.
    cmd = [chrome, f"--user-data-dir={user_dir}", *sandbox_args]
    if settings.allow_webstore_extensions:
        notes.append("Web Store extensions allowed (exclusivity OFF — opt-in only)")
    else:
        cmd.append(f"--disable-extensions-except={ext}")
        notes.append("Anti-detect exclusivity ON (--disable-extensions-except)")
    cmd.extend([
        f"--load-extension={ext}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=ChromeWhatsNewUI",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--new-window",
    ])
    if window_rect is not None and settings.window_tiling_enabled:
        cmd.extend(chrome_geometry_args(window_rect))
        notes.append(f"Window tile: {window_rect.w}x{window_rect.h}+{window_rect.x}+{window_rect.y}")

    if parsed_proxy:
        arg = proxy_server_arg(parsed_proxy)
        if arg:
            cmd.append(f"--proxy-server={arg}")
            notes.append(f"Proxy applied: {arg}")
            if parsed_proxy.get("username"):
                notes.append("Proxy auth delegated to extension (proxy.json + onAuthRequired)")
    elif profile.get("proxyType") == "custom" and not direct:
        notes.append("Custom proxy configured but could not be parsed; launching without --proxy-server")

    # WebRTC flags
    if webrtc_mode == "disable_non_proxied_udp":
        cmd.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        cmd.append("--enforce-webrtc-ip-permission-check")
        notes.append("WebRTC: disable_non_proxied_udp")
    elif webrtc_mode == "disable":
        cmd.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        cmd.append("--enforce-webrtc-ip-permission-check")
        cmd.append("--disable-webrtc")
        notes.append("WebRTC: disable (flags + JS hardening)")

    if ua:
        cmd.append(f"--user-agent={ua}")

    # Always enable localhost-only CDP for tiling (never bind 0.0.0.0).
    debug_port = allocate_localhost_port()
    cmd.append("--remote-debugging-address=127.0.0.1")
    cmd.append("--remote-allow-origins=*")
    cmd.append(f"--remote-debugging-port={debug_port}")
    notes.append(f"CDP localhost-only on 127.0.0.1:{debug_port}")
    if cdp:
        notes.append("Caller requested CDP (already allocated)")
    cmd.append(start)

    env = os.environ.copy()
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        if slot_held:
            browser_manager.release_slot(profile_id)
        return {"ok": False, "error": f"Failed to spawn Chrome: {e}", "errors": ["spawn_failed"], "cmd": cmd}

    time.sleep(1.2)
    rc = proc.poll()
    if rc is not None:
        err = (proc.stderr.read() or b"").decode("utf-8", "ignore")[-2500:]
        set_runtime(profile_id, "idle", None)
        clear_runtime(profile_id)
        set_launch_state(profile_id, LaunchState.LAUNCH_FAILED, error=f"chrome exit {rc}", error_code="chrome_exit_immediate")
        if slot_held:
            browser_manager.release_slot(profile_id)
        return {
            "ok": False,
            "error": f"Chrome exited immediately with code {rc}",
            "errors": ["chrome_exit_immediate"],
            "stderr": err,
            "cmd": cmd,
            "notes": notes,
            "fingerprint": {
                "seed": fingerprint.get("seed"),
                "platform": fingerprint.get("platform"),
                "screen": fingerprint.get("screen"),
            },
        }

    set_runtime(profile_id, "running", proc.pid)
    browser_manager.mark_running(profile_id)
    upsert_runtime(profile_id, pid=proc.pid, cdp_port=debug_port)
    set_launch_state(profile_id, LaunchState.LAUNCHING, pid=proc.pid, cdp_port=debug_port)
    log.info("launched profile %s pid=%s url=%s cdp=%s", profile_id, proc.pid, start, debug_port)
    try:
        wait_for_cdp(debug_port, timeout_s=8.0)
        set_launch_state(profile_id, LaunchState.CDP_READY, pid=proc.pid, cdp_port=debug_port)
        notes.append(f"CDP ready on 127.0.0.1:{debug_port}")
    except Exception as e:
        set_launch_state(
            profile_id,
            LaunchState.CDP_FAILED,
            pid=proc.pid,
            cdp_port=debug_port,
            error=str(e),
            error_code="cdp_unreachable",
        )
        notes.append(f"CDP not ready: {e}")
    # Browser stays up even if CDP failed; tiling may still fail later.
    set_launch_state(profile_id, LaunchState.RUNNING, pid=proc.pid, cdp_port=debug_port)
    # Geometry applied later by WindowLayoutManager via CDP (no xdotool/wmctrl).
    try:
        if proc.stderr:
            proc.stderr.close()
    except Exception:
        pass

    out = {
        "ok": True,
        "pid": proc.pid,
        "url": start,
        "userDataDir": str(user_dir),
        "extension": str(ext),
        "cmd": cmd,
        "notes": notes,
        "errors": [],
        "fingerprint": {
            "seed": fingerprint.get("seed"),
            "platform": fingerprint.get("platform"),
            "cores": fingerprint.get("cores"),
            "memory": fingerprint.get("memory"),
            "screen": fingerprint.get("screen"),
            "touch": fingerprint.get("touch"),
            "timezone": fingerprint.get("timezone"),
            "webrtcMode": webrtc_mode,
        },
    }
    out["sandbox"] = {
        "ok": sandbox_status.ok,
        "useNoSandbox": sandbox_status.use_no_sandbox,
        "reason": sandbox_status.reason,
    }
    out["debugPort"] = debug_port
    out["cdpPort"] = debug_port
    out["cdpHost"] = "127.0.0.1"
    from .runtime_state import get_runtime
    rt = get_runtime(profile_id)
    if rt:
        out["launchState"] = rt.launch_state.value
    return out


def stop_profile(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    pid = (profile or {}).get("pid")
    if not pid:
        set_runtime(profile_id, "idle", None)
        clear_runtime(profile_id)
        browser_manager.release_slot(profile_id)
        return {"ok": True, "stopped": None, "note": "No running pid recorded"}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        return {"error": f"Cannot stop pid {pid}: {e}", "errors": ["permission"]}
    set_runtime(profile_id, "idle", None)
    clear_runtime(profile_id)
    browser_manager.release_slot(profile_id)
    return {"ok": True, "stopped": pid}


def _allocate_debug_port() -> int:
    """Bind to port 0 to discover a free TCP port for Chrome remote debugging."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])

