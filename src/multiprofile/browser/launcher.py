"""Chrome browser launcher for Multiprofile Native.

Key design principles:
- Every profile has an explicit NetworkConfig; proxy failure BLOCKS launch.
- GPU mode is probed at runtime (auto/angle/software) — never claim hardware
  while actually using software rendering.
- No --disable-gpu by default; uses --use-gl=angle when GPU available.
- CDP is always localhost-only (127.0.0.1).
- Sandbox is probed and applied; only disabled when unavoidable.
- No silent direct-IP fallback when a proxy is configured.
"""
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

from ..audit import audit_log
from ..config import get_settings
from ..cookies_store import load_cookies
from ..fingerprint import build_fingerprint, normalize_seed
from ..network import (
    NetworkConfig,
    NetworkMode,
    ProxyHealthResult,
    check_proxy_before_launch,
    chrome_network_flags,
    network_config_for_profile,
)
from ..storage.profiles import get_profile, set_runtime, update_profile
from ..storage.xdg import chrome_profile_dir
from . import manager as browser_manager
from .cdp_window import allocate_localhost_port, wait_for_cdp
from .grid import Rect
from .runtime_state import LaunchState, clear_runtime, set_launch_state, upsert_runtime
from .sandbox import sandbox_chrome_args
from .window_layout import chrome_geometry_args  # weak hint only; no x11 place

log = logging.getLogger("multiprofile.browser")


# ---------------------------------------------------------------------------
# Platform / X11 flags
# ---------------------------------------------------------------------------

def chrome_window_platform_args() -> list[str]:
    """Force X11/XWayland so CDP Browser.setWindowBounds actually moves windows.

    On Wayland, getWindowBounds echoes the values we just set while the
    compositor leaves windows in the default cascade.
    """
    return ["--ozone-platform=x11", "--ozone-platform-hint=x11"]


def chrome_disable_features_arg() -> str:
    return "--disable-features=ChromeWhatsNewUI,WaylandWindowDecorations"


def github_runner_unavailable_error() -> str:
    """Return the human-readable error for GitHub proxy type launch attempts.

    GitHub Account does NOT change your IP address. It is an authentication
    credential for the GitHub API, not a network gateway. Use a Custom Proxy
    (HTTP/SOCKS5) or configure a Remote Gateway for a different egress IP.
    """
    return (
        "GitHub Account does not change your IP address or act as a network gateway. "
        "It is for GitHub API authentication only. "
        "To route a profile through a different IP: configure a Custom Proxy "
        "(HTTP or SOCKS5) or a Remote Gateway in the Network settings."
    )


# ---------------------------------------------------------------------------
# GPU mode detection
# ---------------------------------------------------------------------------

def _probe_gpu_available() -> bool:
    """Check whether a real GPU device is accessible from this process.

    Looks for DRI render nodes (AMD/Intel/Nouveau) or NVIDIA device files.
    Returns False on pure software/container environments.
    """
    # DRI render nodes (Mesa / AMDGPU / Intel)
    for path in Path("/dev/dri").glob("renderD*") if Path("/dev/dri").is_dir() else []:
        try:
            if path.stat():
                return True
        except OSError:
            pass
    # NVIDIA character devices
    for path in [Path("/dev/nvidia0"), Path("/dev/nvidiactl")]:
        if path.exists():
            return True
    # DISPLAY set — at minimum Xvfb may work with ANGLE
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def chrome_gpu_args(gpu_mode: str) -> list[str]:
    """Return Chrome GPU flags appropriate for the environment.

    Modes:
        auto     — probe for GPU; use ANGLE if available, else software
        angle    — force ANGLE (hardware via EGL/GL)
        software — explicitly use SwiftShader software rendering
        disabled — legacy --disable-gpu (kept for compat only)
    """
    mode = (gpu_mode or "auto").strip().lower()

    if mode == "software":
        return [
            "--disable-gpu",
            "--use-gl=swiftshader",
        ]
    if mode == "disabled":
        return ["--disable-gpu"]
    if mode == "angle":
        return [
            "--use-gl=angle",
            "--use-angle=gl",
        ]
    # auto
    if _probe_gpu_available():
        return [
            "--use-gl=angle",
            "--use-angle=gl",
        ]
    # No GPU available — use software renderer but be honest about it
    log.info("GPU not detected, using SwiftShader software renderer")
    return [
        "--disable-gpu",
        "--use-gl=swiftshader",
    ]


# ---------------------------------------------------------------------------
# CSD shadow suppression (for edge-to-edge tiling)
# ---------------------------------------------------------------------------

def ensure_no_client_frame_shadow(user_data_dir: Path) -> None:
    """Turn off Chrome CSD drop-shadows for tiled windows.

    Custom frames draw a translucent shadow *inside* the outer bounds, so the
    desktop shows between tiles even when CDP rectangles touch. Setting
    browser.custom_chrome_frame=false uses system title bar/borders instead
    (Chrome's 'Use system title bar and borders'), which keeps the opaque
    frame flush.
    """
    prefs_path = Path(user_data_dir) / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if prefs_path.exists():
        try:
            loaded = json.loads(prefs_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = {}
    browser = data.get("browser")
    if not isinstance(browser, dict):
        browser = {}
        data["browser"] = browser
    if browser.get("custom_chrome_frame") is False:
        return
    browser["custom_chrome_frame"] = False
    tmp = prefs_path.with_name(prefs_path.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(prefs_path)


# ---------------------------------------------------------------------------
# Extension and Chrome discovery
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Extension preparation
# ---------------------------------------------------------------------------

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

        inline = f"var __MP_INLINE_CFG__ = {cfg_json};\nwindow.__MP_PROFILE__ = __MP_INLINE_CFG__;\n"
        if "/*__PROFILE_CONFIG_JSON__*/" in text or "__PROFILE_CONFIG_JSON__" in text:
            text = inline + text
            text = text.replace('"__PROFILE_CONFIG_JSON__"', "null")
            text = text.replace("'__PROFILE_CONFIG_JSON__'", "null")
        else:
            text = inline + text
        evasion.write_text(text, encoding="utf-8")

    # cookies.json for background to apply
    cookies = load_cookies(profile["id"])
    (dest / "cookies.json").write_text(json.dumps(cookies, indent=2), encoding="utf-8")

    # proxy.json — written from NetworkConfig so extension can handle proxy auth
    net = network_config_for_profile(profile)
    if net.proxy and net.mode != NetworkMode.DIRECT:
        proxy_data = {
            "host": net.proxy.host,
            "port": net.proxy.port,
            "type": net.proxy.scheme,
            "username": net.proxy.username or "",
            "password": net.proxy.password or "",
        }
        (dest / "proxy.json").write_text(json.dumps(proxy_data, indent=2), encoding="utf-8")

    return dest


# ---------------------------------------------------------------------------
# Main launch function
# ---------------------------------------------------------------------------

def launch_profile(
    profile_id: str,
    url: str | None = None,
    direct: bool = False,
    cdp: bool = False,
    _slot_held: bool = False,
    window_rect: Rect | None = None,
) -> dict[str, Any]:
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

    notes: list[str] = []

    # ------------------------------------------------------------------
    # NETWORK CONFIG — build from structured field or migrate legacy proxy
    # ------------------------------------------------------------------
    if direct:
        # Explicit override: user wants DIRECT regardless of profile config
        from ..network import NetworkConfig as _NC, NetworkMode as _NM
        net = _NC(mode=_NM.DIRECT)
        notes.append("Network: DIRECT (caller override)")
    else:
        net = network_config_for_profile(profile)

    # GitHub proxy type is NOT a network route — block clearly
    if (profile.get("proxyType") or "").lower() == "github" and not direct:
        if slot_held:
            browser_manager.release_slot(profile_id)
        return {
            "ok": False,
            "error": (
                "GitHub Account is not a network gateway. "
                "Configure a Custom Proxy (HTTP/SOCKS5) or a Remote Gateway for a different egress IP. "
                "Use Direct Connection to launch without a proxy."
            ),
            "errors": ["github_not_a_network_route"],
        }

    # ------------------------------------------------------------------
    # PRE-LAUNCH PROXY HEALTH CHECK — block on failure (default policy)
    # ------------------------------------------------------------------
    proxy_health: ProxyHealthResult | None = None
    settings = get_settings()
    if settings.proxy_check_enabled and net.needs_proxy_check():
        allowed, block_reason, proxy_health = check_proxy_before_launch(
            net, timeout=float(settings.proxy_check_timeout)
        )
        if not allowed:
            if slot_held:
                browser_manager.release_slot(profile_id)
            audit_log(
                "launch_blocked_proxy_failed",
                profile_id=profile_id,
                ok=False,
                detail={"error": block_reason, "proxy": net.proxy.masked_str() if net.proxy else None},
            )
            return {
                "ok": False,
                "error": block_reason,
                "errors": ["proxy_health_failed"],
                "proxyHealth": proxy_health.to_dict() if proxy_health else None,
            }
        if proxy_health:
            notes.append(
                f"Proxy OK: {net.proxy.masked_str() if net.proxy else ''} "
                f"latency={proxy_health.latency_ms}ms egress={proxy_health.egress_ip or 'unknown'}"
            )
    elif net.mode != NetworkMode.DIRECT:
        notes.append(f"Network: {net.mode.value} (proxy check disabled by config)")

    # ------------------------------------------------------------------
    # Fingerprint
    # ------------------------------------------------------------------
    fingerprint = profile.get("fingerprint") if isinstance(profile.get("fingerprint"), dict) else None
    if not fingerprint:
        fingerprint = build_fingerprint(profile)
        try:
            update_profile(profile_id, {"fingerprint": fingerprint, "seed": fingerprint.get("seed")})
            profile = get_profile(profile_id) or profile
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Chrome binary + extension
    # ------------------------------------------------------------------
    try:
        chrome = find_chrome()
        ext = _prepare_extension(profile, fingerprint)
    except FileNotFoundError as e:
        if slot_held:
            browser_manager.release_slot(profile_id)
        return {"ok": False, "error": str(e), "errors": ["dependency_missing"]}

    user_dir = chrome_profile_dir(profile_id) / "user-data"
    user_dir.mkdir(parents=True, exist_ok=True)
    ensure_no_client_frame_shadow(user_dir)
    start = url or profile.get("startUrl") or "https://example.com"
    ua = profile.get("userAgent") or ""

    # ------------------------------------------------------------------
    # Sandbox
    # ------------------------------------------------------------------
    sandbox_args, sandbox_status = sandbox_chrome_args(chrome)
    notes.append(f"Sandbox: {sandbox_status.reason}")

    # ------------------------------------------------------------------
    # Build Chrome command
    # ------------------------------------------------------------------
    cmd = [chrome, f"--user-data-dir={user_dir}", *sandbox_args]

    # Extension exclusivity
    if settings.allow_webstore_extensions:
        notes.append("Web Store extensions allowed (exclusivity OFF — opt-in only)")
    else:
        cmd.append(f"--disable-extensions-except={ext}")
        notes.append("Anti-detect exclusivity ON (--disable-extensions-except)")

    cmd.extend([
        f"--load-extension={ext}",
        "--no-first-run",
        "--no-default-browser-check",
        # Suppress automation-advertised Blink features
        "--disable-blink-features=AutomationControlled",
        chrome_disable_features_arg(),
        "--new-window",
        *chrome_window_platform_args(),
    ])

    # GPU flags — probe environment, never claim hardware while using software
    gpu_flags = chrome_gpu_args(settings.gpu_mode)
    cmd.extend(gpu_flags)
    notes.append(f"GPU flags: {' '.join(gpu_flags)}")

    # Window tiling geometry hint
    if window_rect is not None and settings.window_tiling_enabled:
        cmd.extend(chrome_geometry_args(window_rect))
        notes.append(f"Window tile: {window_rect.w}x{window_rect.h}+{window_rect.x}+{window_rect.y}")

    # ------------------------------------------------------------------
    # Network flags from NetworkConfig (replaces scattered proxy/webrtc blocks)
    # ------------------------------------------------------------------
    net_flags = chrome_network_flags(net)
    cmd.extend(net_flags)
    if net_flags:
        notes.append(f"Network flags: {' '.join(net_flags)}")
    notes.append(f"Network mode: {net.mode.value}")

    # User-agent (flag-level; CDP override applied after CDP is ready)
    if ua:
        cmd.append(f"--user-agent={ua}")

    # CDP — always localhost-only
    debug_port = allocate_localhost_port()
    cmd.extend([
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        f"--remote-debugging-port={debug_port}",
    ])
    notes.append(f"CDP localhost-only on 127.0.0.1:{debug_port}")

    cmd.append(start)

    # ------------------------------------------------------------------
    # Spawn Chrome
    # ------------------------------------------------------------------
    env = os.environ.copy()
    env["OZONE_PLATFORM"] = "x11"
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
    log.info("launched profile %s pid=%s url=%s cdp=%s network=%s", profile_id, proc.pid, start, debug_port, net.mode.value)

    # ------------------------------------------------------------------
    # CDP ready + UA override
    # ------------------------------------------------------------------
    try:
        wait_for_cdp(debug_port, timeout_s=8.0)
        set_launch_state(profile_id, LaunchState.CDP_READY, pid=proc.pid, cdp_port=debug_port)
        notes.append(f"CDP ready on 127.0.0.1:{debug_port}")

        # Apply UA/client-hints override via CDP for HTTP-layer consistency
        try:
            from .cdp_identity import apply_ua_override
            apply_ua_override(debug_port, fingerprint, ua or fingerprint.get("userAgent") or "")
            notes.append("CDP UA/client-hints override applied")
        except Exception as e:
            notes.append(f"CDP UA override skipped: {e}")

        # Store egress IP in runtime if proxy health gave us one
        if proxy_health and proxy_health.egress_ip:
            upsert_runtime(
                profile_id,
                proxy_ok=True,
                egress_ip=proxy_health.egress_ip,
                last_proxy_check=proxy_health.checked_at,
            )

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

    set_launch_state(profile_id, LaunchState.RUNNING, pid=proc.pid, cdp_port=debug_port)

    try:
        if proc.stderr:
            proc.stderr.close()
    except Exception:
        pass

    audit_log(
        "profile_launch",
        profile_id=profile_id,
        ok=True,
        detail={
            "pid": proc.pid,
            "network": net.mode.value,
            "cdpPort": debug_port,
            "egressIp": proxy_health.egress_ip if proxy_health else None,
        },
    )

    out: dict[str, Any] = {
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
        },
        "network": {
            "mode": net.mode.value,
            "egressIp": proxy_health.egress_ip if proxy_health else None,
            "proxyHealth": proxy_health.to_dict() if proxy_health else None,
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


# ---------------------------------------------------------------------------
# Stop profile
# ---------------------------------------------------------------------------

def stop_profile(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    pid = (profile or {}).get("pid")
    if not pid:
        set_runtime(profile_id, "idle", None)
        clear_runtime(profile_id)
        browser_manager.release_slot(profile_id)
        audit_log("profile_stop", profile_id=profile_id, ok=True, detail={"pid": None})
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
    audit_log("profile_stop", profile_id=profile_id, ok=True, detail={"pid": pid})
    return {"ok": True, "stopped": pid}


def _allocate_debug_port() -> int:
    """Bind to port 0 to discover a free TCP port for Chrome remote debugging."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
