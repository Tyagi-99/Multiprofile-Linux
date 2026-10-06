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
import re
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..api import events
from ..audit import audit_log
from ..config import get_settings
from ..cookies_store import load_cookies
from ..engine import detect_engine_version, find_browser_binary
from ..fingerprint import build_fingerprint, normalize_seed
from ..network import (
    NetworkMode,
    ProxyHealthResult,
    check_proxy_before_launch,
    chrome_network_flags,
    network_config_for_profile,
    unsupported_route_reason,
)
from ..storage import db as _db
from ..storage.profiles import get_profile, set_runtime, update_profile
from ..storage.state import InvalidStateTransition, ProfileState
from ..storage.xdg import chrome_profile_dir, purge_legacy_extension_staging
from . import fontconfig, identity_faults
from . import manager as browser_manager
from .cdp_window import allocate_localhost_port, release_localhost_port, wait_for_cdp
from .grid import Rect
from .runtime_state import LaunchState, clear_runtime, set_launch_state, upsert_runtime
from .sandbox import sandbox_chrome_args
from .window_layout import chrome_geometry_args  # weak hint only; no x11 place

log = logging.getLogger("multiprofile.browser")


# ---------------------------------------------------------------------------
# Control-plane state recording (durable truth in SQLite)
# ---------------------------------------------------------------------------

def _db_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _record_state(    profile_id: str,
    new_state: ProfileState,
    *,
    note: str | None = None,
    **fields: Any,
) -> None:
    """Record a profile state transition in the durable store.

    Fail-open bookkeeping: a DB/write failure is logged and never breaks the
    data-plane launch path. An invalid transition is forced (with an audit
    trail) rather than aborting an in-flight browser launch.
    """
    try:
        try:
            change = _db.set_profile_state(profile_id, new_state, note=note)
        except InvalidStateTransition:
            log.warning(
                "launcher: forcing invalid transition for %s (%s)",
                profile_id,
                new_state.value,
            )
            change = _db.set_profile_state(profile_id, new_state, note=note, force=True)
        events.publish(
            "profile.status",
            {
                "profile_id": profile_id,
                "old_state": change["old"],
                "new_state": change["new"],
                "note": note,
            },
        )
        if fields:
            _db.upsert_profile_runtime(profile_id, **fields)
    except Exception:  # noqa: BLE001 - bookkeeping must not break launch
        log.debug("launcher: durable state write failed for %s", profile_id, exc_info=True)


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
    # A display is NOT a GPU.
    #
    # This used to return True whenever DISPLAY or WAYLAND_DISPLAY was set,
    # on the reasoning that "at minimum Xvfb may work with ANGLE". It does
    # not: Xvfb provides a window server, not a graphics device, so ANGLE has
    # nothing to bind and WebGL fails outright -- and because this branch
    # claimed hardware, the software path with its SwiftShader flag was never
    # taken. Measured under Xvfb: getContext('webgl') null, and every
    # GPU-related spoof inert.
    #
    # Any headless server with a virtual display hits this, which is a large
    # share of the machines this product is likely to run on unattended. The
    # docstring above says "a real GPU device is accessible"; a display is
    # evidence of neither, so the guess is gone rather than refined.
    return False


def chrome_enabled_features() -> list[str]:
    """The single ``--enable-features`` value for a launch.

    Chrome takes the **last** ``--enable-features`` on the command line rather
    than the union of them, so every feature this product needs has to be
    composed into one flag. Adding a second one elsewhere silently drops this
    list, which is exactly the kind of failure that looks like it works.

    ``WebBluetooth`` is here because this host's Chromium build ships it
    disabled while upstream Chrome on Windows and macOS does not. Measured on
    stock Chromium 151:

        default                          navigator.bluetooth undefined,
                                         Bluetooth undefined,
                                         BluetoothDevice undefined
        --enable-features=WebBluetooth   all three present

    A profile whose User-Agent says Windows and whose navigator has no
    Bluetooth at all is describing a browser that does not exist. The flag
    turns the genuine interface family back on, which is better than
    synthesising one: a fake navigator.bluetooth without the matching
    Bluetooth and BluetoothDevice constructors would trade one contradiction
    for a more detailed one.
    """
    features = ["WebBluetooth"]
    features.extend(_webgpu_features())
    return features


def _webgpu_features() -> list[str]:
    """Keep WebGPU working under the x11 ozone platform we force.

    Window placement needs ``--ozone-platform=x11`` (on Wayland,
    ``getWindowBounds`` echoes back whatever we just set while the compositor
    leaves the window in its default cascade), and that costs the WebGPU
    adapter. Measured on this host against stock Chromium 151:

        --ozone-platform=x11 --use-gl=angle --use-angle=gl
            requestAdapter() -> null
        --ozone-platform=wayland --use-gl=angle --use-angle=gl
            requestAdapter() -> {vendor: "intel", architecture: "gen-12lp"}
        --ozone-platform=x11 ... --enable-features=Vulkan
            requestAdapter() -> {vendor: "intel", architecture: "gen-12lp"}

    A profile claiming an RTX 4060 in WebGL while WebGPU says the machine has
    no adapter at all is a contradiction inside one page, and it was ours, not
    the host's. Vulkan restores the adapter without giving up the window
    control, so both surfaces can describe the same card.

    ``MULTIPROFILE_WEBGPU=0`` turns it off for a host whose Vulkan stack
    misbehaves; the profile then launches without a WebGPU adapter.
    """
    if os.environ.get("MULTIPROFILE_WEBGPU", "1").strip().lower() in {"0", "false", "no"}:
        return []
    return ["Vulkan"]


#: Software rendering flags. `--enable-unsafe-swiftshader` is not optional.
#:
#: Chrome gates SwiftShader behind it from 121 onward, and without it WebGL
#: does not fall back -- it is simply absent. Measured on Chromium 151:
#:
#:     --disable-gpu --use-gl=swiftshader
#:         canvas.getContext('webgl')  ->  null
#:     ...plus --enable-unsafe-swiftshader
#:         ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device ...), SwiftShader driver)
#:
#: This is not a headless-CI detail. Any host without /dev/dri takes this
#: path -- a server, a VM, a container -- and a profile that claims an
#: "NVIDIA GeForce RTX 4060" while `getContext('webgl')` returns null is a
#: contradiction no real browser produces. It is the F26 shape again: the
#: GPU story disagreeing with itself. The "unsafe" in the flag name is about
#: SwiftShader's own hardening posture, not about this product's use of it;
#: the alternative on such a host is no WebGL at all, which is worse and more
#: conspicuous.
_SOFTWARE_GPU_FLAGS = [
    "--disable-gpu",
    "--use-gl=swiftshader",
    "--enable-unsafe-swiftshader",
]


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
        return _SOFTWARE_GPU_FLAGS[:]
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
    return _SOFTWARE_GPU_FLAGS[:]


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
# Identity-script bundle and Chrome discovery
# ---------------------------------------------------------------------------

def extension_src() -> Path:
    """Where ``evasion.js`` lives.

    This bundle is no longer LOADED as a browser extension — it is the source
    the CDP injector reads (see :mod:`multiprofile.browser.cdp_inject`). Google
    Chrome refuses ``--load-extension`` outright, while Chromium honoured it,
    so the launcher used to hook every page twice on one browser and once on
    the other: the canvas noise the config asked for was applied two times and
    the result was not reproducible across engines. The flags and the
    per-profile staging copy are gone; the manifest stays because it documents
    what ``evasion.js`` is and the credential-exposure guards check it.
    """
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
    # The candidate list lives in ``engine`` so that the binary we launch and
    # the binary we ask for a version are the same one, in the same order.
    path = find_browser_binary()
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

def _assigned_extension_payloads(profile_id: str) -> list[tuple[str, str]]:
    """``(ext_uuid, payload_path)`` for this profile's enabled extensions.

    Imported lazily and guarded: the extension manager is an optional part of
    the control plane, and a profile must still launch if it is unavailable or
    if its tables have not been created yet. A launch that fails because the
    extension feature is broken would be a worse bug than an extension that
    does not load.
    """
    try:
        from ..extensions.manager import payload_paths_for_profile

        return payload_paths_for_profile(profile_id)
    except Exception as e:  # noqa: BLE001 - never block a launch over this
        log.warning("extension assignments unavailable for %s: %s", profile_id, e)
        return []


def _observe_extension_ids(debug_port: int, timeout_s: float = 6.0) -> list[str]:
    """Extension IDs the browser itself reports, read off its target list.

    An MV3 extension with a service worker shows up as a target whose URL is
    ``chrome-extension://<id>/...``. Asking the browser is the point: a path
    on a command line and a row in a database both say what we intended, and
    neither says what happened.

    Polled rather than read once, because the extension's service worker
    starts asynchronously after the browser does.
    """
    import json as _json
    import urllib.request as _url

    deadline = time.time() + timeout_s
    seen: set[str] = set()
    while time.time() < deadline:
        try:
            req = _url.Request(
                f"http://127.0.0.1:{debug_port}/json",
                headers={"Host": f"localhost:{debug_port}"},
            )
            targets = _json.loads(_url.urlopen(req, timeout=2).read())
        except Exception:  # noqa: BLE001 - the browser may not be listening yet
            time.sleep(0.3)
            continue
        for t in targets:
            url = str(t.get("url") or "")
            if url.startswith("chrome-extension://"):
                ext_id = url[len("chrome-extension://"):].split("/")[0]
                if ext_id:
                    seen.add(ext_id)
        if seen:
            return sorted(seen)
        time.sleep(0.3)
    return sorted(seen)


def _verify_assigned_extensions(
    profile_id: str, debug_port: int, assigned: list[tuple[str, str]]
) -> dict[str, Any]:
    """Mark the assignments applied, then reconcile against the browser."""
    try:
        from ..extensions.manager import mark_applied, verify_profile

        mark_applied(profile_id, [u for u, _ in assigned])
        observed = _observe_extension_ids(debug_port)
        results = verify_profile(profile_id, observed)
        return {
            "assigned": len(assigned),
            "observedIds": observed,
            "results": [r.to_dict() for r in results],
            "verified": sum(1 for r in results if r.ok),
        }
    except Exception as e:  # noqa: BLE001 - reporting must not fail a launch
        log.warning("extension verification failed for %s: %s", profile_id, e)
        return {"assigned": len(assigned), "error": str(e)}


def _reconcile_with_preset(
    fingerprint: dict[str, Any], preset_cfg: dict[str, Any]
) -> dict[str, Any]:
    """Make the injected script agree with the device preset.

    Two systems spoof the same properties: ``Emulation.*`` from the device
    preset, and the injected identity script from the fingerprint. They must
    not disagree -- ``consistency.py`` exists precisely to catch a mobile
    preset wearing a desktop fingerprint, and once identity injection
    actually reached the page the disagreement became visible as touch
    emulation being silently undone.

    Only the overlapping keys are copied. Everything else the seed decided
    stays as it is, so a profile remains deterministic.
    """
    if not preset_cfg:
        return fingerprint
    out = dict(fingerprint)
    if preset_cfg.get("mobile"):
        out["isMobile"] = True
    touch = preset_cfg.get("max_touch_points")
    if touch is not None:
        out["touch"] = int(touch)
    cores = preset_cfg.get("hardware_concurrency")
    if cores:
        out["cores"] = int(cores)
    memory = preset_cfg.get("device_memory_gb")
    if memory:
        out["memory"] = int(memory)
    return out


def _cdp_cookies(stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert stored (extension-shaped) cookies to CDP CookieParam.

    The stored form is what chrome.cookies.set takes: ``expirationDate``
    seconds and a leading-dot ``domain``. CDP wants ``expires`` and is happy
    with the dotted domain, but rejects entries with no name or no domain, so
    those are dropped rather than sent and silently refused.
    """
    out: list[dict[str, Any]] = []
    for c in stored or []:
        name = c.get("name")
        domain = c.get("domain") or ""
        if not name or not domain:
            continue
        param: dict[str, Any] = {
            "name": str(name),
            "value": str(c.get("value") or ""),
            "domain": domain,
            "path": c.get("path") or "/",
            "secure": bool(c.get("secure")),
            "httpOnly": bool(c.get("httpOnly")),
        }
        if c.get("sameSite"):
            # CDP capitalises these; chrome.cookies does not always.
            param["sameSite"] = str(c["sameSite"]).capitalize()
        if c.get("expirationDate"):
            param["expires"] = float(c["expirationDate"])
        out.append(param)
    return out


def _resolve_wire_identity(
    profile: dict[str, Any],
    fingerprint: dict[str, Any],
    device_preset: Any,
) -> tuple[str, dict[str, Any] | None]:
    """Decide, once, what this profile claims to be on the wire.

    Returns ``(ua_string, cdp_override_params)``. Both the ``--user-agent``
    launch flag, the CDP override and the launch-time assertion read this
    one answer, so the flag, the headers and the check cannot disagree.

    User-Agent precedence:
      1. ``profile["userAgent"]`` — an explicit operator override wins.
      2. the device preset's UA.
      3. the fingerprint's UA.

    (3) is the case F1 dropped on the floor: with no preset and no explicit
    UA the launcher passed no ``--user-agent`` at all and the one-shot CDP
    override evaporated, so the profile browsed as the host while its
    JavaScript claimed Windows. There is always a fingerprint UA, so there
    is now always a UA to claim.

    Client hints come from the device preset when there is one — it is the
    authority on what the device is — and from the fingerprint otherwise.

    Version: the claimed Chrome major tracks the engine that will actually
    run the profile (F5). Pool UAs are aligned in ``build_fingerprint``;
    a preset UA and the preset's own brand lists are aligned here, so the
    catalogue's Chrome-131 literals do not reach the wire on a 151 engine.
    An explicit operator UA is never renumbered — and when it is present
    its version also governs the hints, so the two cannot be aligned to
    different browsers.
    """
    from ..engine import align_ua_metadata, align_ua_string
    from .cdp_identity import build_ua_override_params
    from .identity_faults import record as _record_fault

    engine = detect_engine_version()
    profile_id = str(profile.get("id") or "") or None
    explicit_ua = (profile.get("userAgent") or "").strip()
    preset_cfg: dict[str, Any] = {}
    if device_preset is not None:
        try:
            preset_cfg = device_preset.to_mobile_config_dict() or {}
        except Exception as e:  # noqa: BLE001 - a broken preset must not lose the UA
            log.debug("preset config unreadable while resolving identity", exc_info=True)
            _record_fault(
                "preset_config_unreadable",
                f"device preset '{getattr(device_preset, 'id', '?')}' could not be read "
                f"({e}); falling back to the pool UA, so this profile will not claim "
                "the device it was given",
                where="browser.launcher._resolve_wire_identity",
                profile_id=profile_id,
            )
    preset_ua = (preset_cfg.get("user_agent") or "").strip()

    if explicit_ua:
        ua = explicit_ua
    elif preset_ua:
        ua = align_ua_string(preset_ua, engine)
    else:
        # Already aligned by build_fingerprint, which owns the pool defaults.
        ua = (fingerprint.get("userAgent") or "").strip()

    metadata = None
    if preset_cfg:
        try:
            from ..fingerprint.mobile import MobileProfileConfig

            metadata = MobileProfileConfig.from_preset_dict(preset_cfg).user_agent_metadata()
        except Exception as e:  # noqa: BLE001 - fall back to fingerprint-derived hints
            log.debug("preset client hints unreadable; using fingerprint", exc_info=True)
            metadata = None
            # The UA above may already be the preset's while these hints are
            # not, which is the one combination F5 works to prevent: a UA and
            # a set of client hints describing different browsers.
            _record_fault(
                "preset_hints_unreadable",
                f"device preset client hints could not be built ({e}); the hints now "
                "come from the fingerprint and may disagree with the preset's UA",
                where="browser.launcher._resolve_wire_identity",
                profile_id=profile_id,
            )
        if metadata is not None and not explicit_ua:
            metadata = align_ua_metadata(metadata, engine)

    return ua, build_ua_override_params(fingerprint, ua, metadata=metadata)


_LAUNCHER_SCOPE = "browser.launcher"


def _release_cdp_port(profile_id: str) -> None:
    """Return this profile's debugging port to the pool.

    Ports are remembered when handed out so two concurrent launches are never
    given the same number. Nothing gave them back, so a long-running process
    accumulated every port it had ever used -- and the only thing keeping that
    bounded was a backstop that pruned by probing, which is a TCP connect this
    module has no other reason to make.

    Called at every teardown, so the set tracks what is actually in use.
    """
    try:
        from .runtime_state import get_runtime

        rt = get_runtime(profile_id)
        release_localhost_port(getattr(rt, "cdp_port", None) if rt else None)
    except Exception:  # noqa: BLE001,S110 - never let bookkeeping block a stop
        pass


def _cdp_port_open(port: int) -> bool:
    """Is something listening on the CDP port yet?

    A bare TCP connect, not an HTTP request: this only needs to know that
    Chrome got far enough to bind, and ``wait_for_cdp`` does the real
    handshake a moment later.
    """
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.15):
            return True
    except OSError:
        return False


def _settle_after_spawn(proc: Any, port: int, timeout_s: float = 1.2) -> int | None:
    """Wait only as long as it takes to learn whether Chrome survived.

    Returns the exit code if it died, or None if it is up.

    This used to be a flat ``time.sleep(1.2)`` before checking ``poll()``,
    which meant every launch paid 1.2 s to answer a question that is usually
    settled in a fraction of that -- a browser that is going to die on a bad
    flag or a locked profile does so almost immediately, and one that is
    going to live has bound its debugging port. Measured, launch was ~1.9 s
    of which this was 1.2 s, and bulk launch is sequential, so starting 40
    profiles spent about 48 seconds here doing nothing.

    The timeout is unchanged, so a slow-but-healthy start is treated exactly
    as before. What changes is that the common cases stop waiting for the
    clock once the answer is known.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            return rc
        if _cdp_port_open(port):
            return None
        time.sleep(0.02)
    return proc.poll()


def _fault(notes: list[str], code: str, detail: str, *, profile_id: str | None) -> None:
    """Record an identity fault and write it into this launch's notes (F11).

    Recording carries the failure to ``/api/health``, for an operator who is
    not reading one profile's notes; the note carries it to the operator who
    is. Both, from one call, so the two cannot describe different things.

    The ``where`` is deliberately the bare ``browser.launcher``: that exact
    value is what marks a fault as *already noted*, so the sweep at the end of
    a launch can add the ones raised elsewhere without printing these twice. A
    launcher fault recorded outside this helper uses a more specific ``where``
    (``browser.launcher._resolve_wire_identity``) and is swept like any other,
    because nothing noted it at its call site.
    """
    fault = identity_faults.record(
        code, detail, where=_LAUNCHER_SCOPE, profile_id=profile_id
    )
    notes.append(fault.note() if fault else f"IDENTITY FAULT [{code}] {detail}")


def _engine_version_notes(ua: str, explicit_ua: str) -> list[str]:
    """Say, in the launch notes, what version was claimed and on what authority.

    The point of F5 is not only that the claim moves with the engine, but
    that an operator can see whether it did. A claim nobody checked is
    recorded as unverified, never as a pass — the same rule the launch-time
    identity assertion follows.
    """
    engine = detect_engine_version()
    claimed = _chrome_major_of(ua)

    if not engine.known:
        if engine.source == "disabled":
            return [f"Claimed Chrome version: {claimed or 'unknown'} (engine alignment off by configuration)"]
        return [
            f"WARNING: claimed Chrome version {claimed or 'unknown'} is UNVERIFIED — "
            f"{engine.detail}. The profile is claiming a version nobody compared to the engine."
        ]

    notes = [
        f"Claimed Chrome version: {claimed or 'unknown'} "
        f"(engine {engine.full} via {engine.source}"
        + (f": {engine.binary}" if engine.binary else "")
        + ")"
    ]
    if explicit_ua and claimed is not None and claimed != engine.major:
        notes.append(
            f"WARNING: explicit User-Agent claims Chrome {claimed} but the engine is "
            f"{engine.major} — left as given; clear profile.userAgent to track the engine"
        )
    return notes


def _chrome_major_of(ua: str) -> int | None:
    m = re.search(r"\b(?:Chrome|Chromium|CriOS|Edg|EdgA|EdgiOS|HeadlessChrome)/(\d+)", ua or "")
    return int(m.group(1)) if m else None


def _screen_metrics_override(fingerprint: dict[str, Any]) -> dict[str, Any] | None:
    """``Emulation.setDeviceMetricsOverride`` params for the claimed screen.

    CSS ``device-width``/``device-height`` come from the compositor and no
    JavaScript can reach them, so defining ``screen.width`` as a getter --
    which is all ``evasion.js`` can do -- leaves a contradiction a page reads
    in one line::

        screen.width/height       1920 x 1080   the claim
        CSS device-width/height   2560 x 1600   the display

    Stock Chromium on the same host agrees with itself at 2560 x 1600, so the
    disagreement is the product's.

    ``width``, ``height`` and ``deviceScaleFactor`` are 0, which CDP reads as
    *do not override the viewport*. Verified on Chromium 151: sending
    ``screenHeight`` alone moved CSS ``device-height`` from 1600 to 1440
    while ``innerWidth``, ``innerHeight``, ``outerWidth`` and ``outerHeight``
    stayed byte-identical. That is what makes this safe to apply to every
    profile rather than only the preset ones -- a viewport override letterboxes
    and scales the operator's window, which is why the no-preset path skipped
    it and carried the contradiction instead.

    Device presets keep their own richer plan (``fingerprint.mobile``), which
    sets the viewport deliberately because emulating a phone is supposed to
    change it.
    """
    screen = fingerprint.get("screen") or {}
    try:
        width = int(screen.get("w") or 0)
        height = int(screen.get("h") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {
        # 0 = leave the viewport alone. See above.
        "width": 0,
        "height": 0,
        "deviceScaleFactor": 0,
        "mobile": bool(fingerprint.get("touch")),
        "screenWidth": width,
        "screenHeight": height,
    }


def _note_engine_policy(notes: list[str], profile_id: str) -> None:
    """Record which build ran this profile, and say so when it is off policy.

    A profile's identity is only reproducible against the engine that
    produced it: relaunching under a different Chromium presents a different
    browser with the same stored seed. The note is the record.

    A pin violation is a fault rather than a note: the operator asked for one
    build and got another, which is a broken expectation, not a choice.
    """
    try:
        from ..engine_policy import (
            VERDICT_SUPPORTED,
            identify_engine,
            verify_engine,
        )

        verdict = verify_engine(identify_engine())
        record = verdict.record
        notes.append(
            f"Engine: {record.version_full or 'unknown'} "
            f"({record.origin}, digest {(record.digest or '')[:12] or 'n/a'})"
        )
        if verdict.pin_violated:
            _fault(notes, "engine_pin_violated",
                   verdict.reasons[0] if verdict.reasons else
                   "the pinned engine was not the one used",
                   profile_id=profile_id)
            return
        if verdict.status != VERDICT_SUPPORTED:
            notes.append(
                f"engine policy: {verdict.status.upper()} — "
                + (verdict.reasons[0] if verdict.reasons else "no detail")
            )
    except Exception as e:  # noqa: BLE001 - never fatal to a launch
        log.debug("engine policy note unavailable for %s: %s", profile_id, e)


def _note_identity_matrix(notes: list[str], fingerprint: dict[str, Any],
                          profile_id: str) -> None:
    """Tell the operator, at launch, what this profile's claim can be backed by.

    The matrix is consulted at create time to pick a default, and reported in
    ``/api/health`` and on the profile detail page. Neither of those is where
    an operator is looking when they press Launch, and this is the moment the
    decision becomes live -- so an experimental claim says so here as well.

    Deliberately a plain note and **not** an identity fault. A fault means
    something on the identity path failed; a cross-OS claim has not failed,
    it is working exactly as asked on every surface the product controls,
    with one surface known to contradict it. Recording it as a fault would
    put a permanent entry in ``/api/health`` for a configuration the operator
    chose on purpose, and would devalue the faults that do mean something
    broke.
    """
    try:
        from ..fingerprint.identity_matrix import EXPERIMENTAL, verdict_for

        claimed = " ".join(
            str(fingerprint.get(k) or "") for k in ("platform", "ch_platform")
        ).lower()
        if "win" in claimed:
            claim = "windows"
        elif "mac" in claimed:
            claim = "macos"
        elif "android" in claimed:
            claim = "android"
        elif "iphone" in claimed or "ios" in claimed:
            claim = "iphone"
        else:
            claim = "linux"
        verdict = verdict_for(claim)
        if verdict.status != EXPERIMENTAL:
            return
        notes.append(
            f"identity claim '{claim}' is EXPERIMENTAL on this host: "
            f"{', '.join(verdict.contradicts)} contradict it. "
            f"{verdict.reasons[0] if verdict.reasons else ''}".strip()
        )
    except Exception as e:  # noqa: BLE001 - never fatal to a launch
        log.debug("identity matrix note unavailable for %s: %s", profile_id, e)


def _start_identity_injection(
    profile_id: str,
    port: int,
    profile: dict[str, Any],
    fingerprint: dict[str, Any],
    net: Any,
    ua_override: dict[str, Any] | None = None,
    geo_plan: dict[str, Any] | None = None,
) -> list[str]:
    """Install fingerprint, proxy auth, cookies and UA over CDP. Returns notes.

    ``geo_plan`` is the place-and-language decision the caller already made
    (see ``geo.align_to_exit``). It is passed in rather than recomputed so
    that the fingerprint evasion.js reads, the Client Hints the UA override
    carries and the CDP overrides installed here can only ever describe one
    place. Recomputing it here was how a page ended up answering pt-BR to
    Intl and en-US to navigator.language at the same time.
    """
    from .cdp_inject import (
        ProxyAuth,
        build_identity_script,
        build_worker_identity_script,
        start_injection,
    )

    notes: list[str] = []

    evasion = extension_src() / "evasion.js"
    script = ""
    if evasion.exists():
        # The config the page sees. webrtcMode and the __PROFILE_* tokens used
        # to be substituted by the extension staging step; that step is gone,
        # so they are supplied here or they are lost.
        cfg = dict(fingerprint)
        cfg["webrtcMode"] = (
            profile.get("webrtcMode") or fingerprint.get("webrtcMode") or "default"
        )
        # Only present when asked for: the default page config gains no extra
        # key, and the script stays silent on every page.
        if get_settings().evasion_debug:
            cfg["debug"] = True
            notes.append(
                "evasion.js DEBUG logging is ON — the script announces itself "
                "on every page (MULTIPROFILE_EVASION_DEBUG)"
            )
        script = build_identity_script(
            cfg,
            evasion.read_text(encoding="utf-8", errors="ignore"),
            replacements={
                "__PROFILE_TIMEZONE__": str(
                    profile.get("timezone") or fingerprint.get("timezone")
                    or "America/New_York"
                ),
                "__PROFILE_ALLOCATED_IP__": str(profile.get("allocatedIp") or ""),
                "__PROFILE_SEED__": str(normalize_seed(profile)),
            },
        )
    else:
        notes.append("identity script MISSING: evasion.js not found")

    auth = None
    if net is not None and getattr(net, "proxy", None) and net.proxy.username:
        auth = ProxyAuth(username=net.proxy.username, password=net.proxy.password or "")

    cookies = _cdp_cookies(load_cookies(profile_id))

    aligned = geo_plan or {}
    status = start_injection(
        profile_id, port, script=script, proxy_auth=auth, cookies=cookies,
        ua_override=ua_override,
        worker_script=build_worker_identity_script(fingerprint),
        locale=aligned.get("language") or fingerprint.get("language") or None,
        geolocation=aligned.get("geolocation"),
        screen_metrics=_screen_metrics_override(fingerprint),
        timezone_id=str(
            fingerprint.get("timezone") or profile.get("timezone") or ""
        ).strip() or None,
    )
    upsert_runtime(profile_id, inject_ok=status.ok, inject_detail=status.to_dict())

    if status.error:
        notes.append(f"identity injection FAILED: {status.error}")
        return notes
    notes.append(
        "Identity injection: script="
        f"{'installed' if status.script_installed else 'NOT INSTALLED'}, "
        f"proxy auth={'armed' if status.auth_armed else 'not needed'}, "
        f"cookies={status.cookies_applied}, "
        f"UA/client-hints="
        f"{'applied' if status.ua_override_applied else 'NOT APPLIED'}"
    )
    if ua_override and not status.ua_override_applied:
        # Loud on purpose: this is the profile's real headers on the wire.
        notes.append(
            "WARNING: UA/client-hints override did not install — the wire "
            "carries the host identity while the page claims otherwise"
        )
    if script and not status.script_installed:
        # Loud on purpose: this is the profile browsing as the host.
        notes.append("WARNING: fingerprint script did not install — profile is NOT masked")
    return notes


# ---------------------------------------------------------------------------
# Device-emulation CDP session holder
# ---------------------------------------------------------------------------

def _hold_device_session(profile_id: str, pid: int, sess: Any) -> None:
    """Keep a device-emulation CDP session open for the browser's lifetime.

    Chromium drops ``Network.setUserAgentOverride`` and
    ``Emulation.setTouchEmulationEnabled`` when the DevTools session that
    set them disconnects (verified empirically: override ACKed and visible
    while the session is open, gone after it closes; only
    ``Emulation.setDeviceMetricsOverride`` survives a disconnect). A daemon
    thread therefore holds the session until the browser process exits,
    then closes it. Never fatal, never blocking.
    """

    def _watch() -> None:
        try:
            while True:
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(10)
        finally:
            try:
                sess.close()
            except Exception:  # noqa: BLE001,S110 - best effort
                pass

    threading.Thread(
        target=_watch, daemon=True, name=f"mpl-cdp-hold-{profile_id}"
    ).start()


# ---------------------------------------------------------------------------
# Grid re-tile (CDP is the only tiling authority)
# ---------------------------------------------------------------------------

def _retile_running_best_effort(note: str) -> None:
    """Re-tile every running profile after a launch or a stop.

    The remaining windows must keep filling the work area. Import is local so
    the launcher never creates an import cycle. Failure is best-effort and
    never breaks the data plane.
    """
    try:
        from .layout_manager import get_layout_manager

        result = get_layout_manager().tile_running()
        log.debug("re-tile after %s: %s", note, result.get("ok"))
    except Exception:  # noqa: BLE001 - re-tile must never break launch/stop
        log.debug("re-tile after %s failed (best effort)", note, exc_info=True)


def _abort_launch(
    profile_id: str,
    pid: int,
    *,
    slot_held: bool,
    error_code: str,
    error: str,
    notes: list[str],
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tear down a browser we have decided must not be handed to the operator.

    Used when a check after spawn says the profile is not what it claims —
    today only the wire-identity assertion. The browser is killed rather than
    left running, because a half-masked profile that the operator can still
    click on is the situation the assertion exists to prevent.

    Every step is best-effort: the return value must reach the caller even if
    the process is already gone or bookkeeping fails.
    """
    try:
        from .cdp_inject import stop_injection
        stop_injection(profile_id)
    except Exception:  # noqa: BLE001 - teardown must not mask the real error
        log.debug("injection teardown failed during abort for %s", profile_id, exc_info=True)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    _wait_pid_gone(pid, timeout_s=5.0)
    set_runtime(profile_id, "idle", None)
    _release_cdp_port(profile_id)
    clear_runtime(profile_id)
    set_launch_state(
        profile_id, LaunchState.VERIFY_FAILED, error=error, error_code=error_code
    )
    if slot_held:
        browser_manager.release_slot(profile_id)
    _record_state(profile_id, ProfileState.ERROR, note=f"{error_code}: {error}")
    audit_log(
        "launch_blocked_identity_mismatch",
        profile_id=profile_id,
        ok=False,
        detail={"pid": pid, "error": error, "assertion": detail},
    )
    log.error("launch aborted for %s (%s): %s", profile_id, error_code, error)
    return {
        "ok": False,
        "error": error,
        "errors": [error_code],
        "notes": notes,
        "identityAssertion": detail,
    }


def _wait_pid_gone(pid: int, timeout_s: float = 10.0) -> bool:
    """Wait until ``pid`` is gone. A zombie counts as gone (already dead)."""
    from .supervisor import pid_alive  # local import: supervisor lazily imports launcher

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


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

    _record_state(profile_id, ProfileState.STARTING)

    # This launch reports its own identity faults, not the last one's (F11).
    identity_faults.clear_for(profile_id)

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
    # Which route the *identity* should describe. Normally the same as the
    # route the traffic takes, and different only under the caller override
    # below -- where the two genuinely disagree and saying so is better than
    # picking one silently.
    identity_mode: NetworkMode = NetworkMode.DIRECT

    if direct:
        # Explicit override: user wants DIRECT regardless of profile config
        from ..network import NetworkConfig as _NC
        from ..network import NetworkMode as _NM
        net = _NC(mode=_NM.DIRECT)
        notes.append("Network: DIRECT (caller override)")
        # The override says where the packets go. It does not say what the
        # profile is configured to be, and the identity follows the
        # configuration -- otherwise bypassing a proxy for one launch would
        # silently rewrite the profile's timezone and position as a
        # side-effect. `commit=False` keeps this a look, not a launch, so no
        # pool assignment is recorded by asking.
        try:
            identity_mode = network_config_for_profile(profile, commit=False).mode
        except Exception:  # noqa: BLE001 - an unreadable route is DIRECT for identity
            identity_mode = NetworkMode.DIRECT
        if identity_mode != NetworkMode.DIRECT:
            notes.append(
                "geo: route overridden to DIRECT, so the traffic leaves from "
                "the host while the identity still describes the configured "
                f"{identity_mode.value} route"
            )
    else:
        # commit=True: this is the one call that is a launch rather than a
        # look, so it is the one allowed to record the pool's decision -- the
        # sticky assignment that keeps this profile's egress stable, or the
        # rotate cursor that moves the next one along.
        net = network_config_for_profile(profile, commit=True)
        identity_mode = net.mode

    # GitHub proxy type is NOT a network route — block clearly
    if (profile.get("proxyType") or "").lower() == "github" and not direct:
        if slot_held:
            browser_manager.release_slot(profile_id)
        _record_state(profile_id, ProfileState.ERROR, note="github_not_a_network_route: GitHub Account is not a network gateway")
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
    # UNRESOLVED ROUTE — block unconditionally.
    #
    # A route that was REQUIRED and could not be resolved is recorded as
    # blocked_reason on a DIRECT-looking config: mode DIRECT, proxy None. That
    # shape is deliberate (see NetworkConfig.blocked_reason), and it means
    # needs_proxy_check() is False, so this used to be checked only inside a
    # branch a blocked config could never enter -- and proxy_check_enabled
    # could switch it off entirely. The result was that a profile in a pool
    # with no available member launched on the HOST IP, reporting ok=True with
    # a note reading "Network mode: DIRECT" as though that had been the
    # intent, and the wire assertion happily confirming the identity.
    #
    # Disabling the proxy *probe* is a choice about network calls at launch. It
    # is not a choice to browse from the host IP when a route was demanded, so
    # this check sits outside that setting and ahead of it.
    # ------------------------------------------------------------------
    proxy_health: ProxyHealthResult | None = None
    settings = get_settings()
    if net.blocked_reason:
        if slot_held:
            browser_manager.release_slot(profile_id)
        audit_log(
            "launch_blocked_route_unresolved",
            profile_id=profile_id,
            ok=False,
            detail={"error": net.blocked_reason, "mode": net.mode.value},
        )
        _record_state(
            profile_id,
            ProfileState.ERROR,
            note=f"route_unresolved: {net.blocked_reason}",
        )
        return {
            "ok": False,
            "error": net.blocked_reason,
            "errors": ["route_unresolved"],
        }

    # An unusable *kind* of route, checked unconditionally. This is not a
    # health question -- "is this proxy up right now" and "can this engine use
    # this kind of route at all" are different, and
    # MULTIPROFILE_PROXY_CHECK_ENABLED=0 answers only the first. Putting this
    # inside the health gate meant disabling the probe also disabled it, and
    # an authenticated SOCKS5 profile launched green into a browser that
    # could not load a page.
    unsupported_reason = unsupported_route_reason(net)
    if unsupported_reason:
        if slot_held:
            browser_manager.release_slot(profile_id)
        audit_log("launch_blocked_route_unsupported", profile_id=profile_id,
                  ok=False, detail={"error": unsupported_reason})
        _record_state(profile_id, ProfileState.ERROR,
                      note="route_unsupported: this engine cannot use this route")
        return {
            "ok": False,
            "error": unsupported_reason,
            "errors": ["route_unsupported"],
        }

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
            _record_state(profile_id, ProfileState.ERROR, note="proxy_health_failed: proxy health check blocked launch")
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
        except Exception as e:  # noqa: BLE001 - this launch is still viable
            # F11, on the identity path. The launch continues with the
            # fingerprint in hand, so this browser is fine -- but nothing was
            # written, so the *next* launch rebuilds it. That is only harmless
            # while the seed is stable, and a profile whose seed was generated
            # here and never stored is a profile whose identity can change
            # between runs. Being the same machine every time is the promise
            # the whole product rests on, so a failure to persist it is not a
            # detail to swallow.
            identity_faults.record(
                "fingerprint_not_persisted",
                f"generated fingerprint could not be saved ({e}); the next "
                "launch will rebuild it and may not match this one",
                where="browser.launcher",
                profile_id=profile_id,
            )

    # ------------------------------------------------------------------
    # Chrome binary + identity-script bundle
    #
    # The bundle is only READ here (the CDP injector needs evasion.js); it is
    # no longer copied per profile nor loaded as an extension. A missing
    # bundle still fails the launch rather than producing an unmasked profile.
    # ------------------------------------------------------------------
    try:
        chrome = find_chrome()
        ext = extension_src()
    except FileNotFoundError as e:
        if slot_held:
            browser_manager.release_slot(profile_id)
        _record_state(
            profile_id,
            ProfileState.ERROR,
            note="dependency_missing: Chrome or the identity script bundle not found",
        )
        return {"ok": False, "error": str(e), "errors": ["dependency_missing"]}

    # An earlier version staged a copy of the extension here, holding the
    # cleartext proxy password and the cookie jar at 0644. Nothing writes or
    # reads it now; a launch is the natural moment to be sure this profile's
    # copy is gone, for a process that started before the startup sweep ran.
    purge_legacy_extension_staging(profile_id)

    user_dir = chrome_profile_dir(profile_id) / "user-data"
    user_dir.mkdir(parents=True, exist_ok=True)
    ensure_no_client_frame_shadow(user_dir)
    start = url or profile.get("startUrl") or "https://example.com"

    # ------------------------------------------------------------------
    # Device preset — resolve early so the preset UA can ride the
    # --user-agent launch flag (persistent for HTTP + JS), not just the
    # session-scoped CDP override applied later.
    # ------------------------------------------------------------------
    device_id = profile.get("deviceId") or ""
    device_preset = None
    if device_id:
        try:
            from ..devices.registry import DeviceRegistry
            device_preset = DeviceRegistry().get(device_id)
        except Exception as e:  # noqa: BLE001 - preset lookup never breaks launch
            log.debug("device preset lookup failed for %s: %s", device_id, e)
        if device_preset is None:
            notes.append(f"device preset not found: {device_id}")
        else:
            preset_cfg = device_preset.to_mobile_config_dict() or {}
            # Reconcile the fingerprint with the preset on the properties BOTH
            # control. build_fingerprint only reads profile["isMobile"], so a
            # Pixel 9 preset used to yield a desktop fingerprint: the injected
            # script then set maxTouchPoints to 0 and quietly overrode the
            # preset's own touch emulation. The preset is authoritative here,
            # as the CDP emulation block below already states.
            reconciled = _reconcile_with_preset(fingerprint, preset_cfg)
            if reconciled != fingerprint:
                fingerprint = reconciled
                notes.append(f"Fingerprint reconciled with device preset ({device_preset.id})")

    # ------------------------------------------------------------------
    # Place and language, decided once, before anything reads them.
    # ------------------------------------------------------------------
    # This has to happen here rather than at injection time, because the
    # timezone and language are not only CDP overrides: they are baked into
    # the fingerprint that evasion.js reads and into the Client Hints the UA
    # override carries. Aligning only the overrides produced a page that
    # answered pt-BR to Intl and en-US to navigator.language -- the profile
    # disagreeing with itself in the surface meant to fix it.
    from ..geo import align_to_exit

    # The route decides who the authority is. On DIRECT the host is the exit,
    # so the host's own zone fills an unset timezone instead of the creation
    # default announcing New York from this machine's address (F43).
    geo_plan = align_to_exit(
        profile,
        fingerprint,
        profile.get("lastNetworkDiagnostics"),
        direct=(identity_mode == NetworkMode.DIRECT),
    )
    if geo_plan["timezone"]:
        fingerprint = {**fingerprint, "timezone": geo_plan["timezone"]}
    if geo_plan["language"]:
        fingerprint = {**fingerprint, "language": geo_plan["language"],
                       "languages": geo_plan["languages"]}
    for conflict in geo_plan["conflicts"]:
        notes.append(f"geo conflict: {conflict}")
    if geo_plan["source"] == "exit_ip":
        notes.append(
            "geo: timezone, language and position taken from the exit IP "
            f"country {geo_plan['country']}"
        )
    elif geo_plan["source"] == "host":
        notes.append(
            f"geo: no proxy, so the host's own timezone {geo_plan['timezone']} "
            "is what this profile claims"
        )
    if geo_plan["geolocation"]:
        notes.append(f"geo: position claimed near {geo_plan['geolocation']['city']}")

    # ------------------------------------------------------------------
    # Wire identity — ONE answer for the flag, the CDP override and the
    # launch-time assertion. Resolved after preset reconciliation so the
    # client hints it carries are the ones the preset asked for.
    # ------------------------------------------------------------------
    ua, ua_override = _resolve_wire_identity(profile, fingerprint, device_preset)
    if ua_override is None:
        notes.append("WARNING: no User-Agent to claim — profile will browse as the host")
    notes.extend(_engine_version_notes(ua, (profile.get("userAgent") or "").strip()))

    # ------------------------------------------------------------------
    # Sandbox
    # ------------------------------------------------------------------
    sandbox_args, sandbox_status = sandbox_chrome_args(chrome)
    notes.append(f"Sandbox: {sandbox_status.reason}")

    # ------------------------------------------------------------------
    # Build Chrome command
    # ------------------------------------------------------------------
    cmd = [chrome, f"--user-data-dir={user_dir}", *sandbox_args]

    # Extension exclusivity.
    #
    # Our own identity script no longer arrives as an extension, so there is
    # nothing to make an exception for: exclusivity is now simply "no
    # extensions". The flags this replaces were worse than redundant — Google
    # Chrome refuses --load-extension outright ("--disable-extensions-except is
    # not allowed in Google Chrome, ignoring") while Chromium honours it, so
    # evasion.js ran twice on Chromium and once on Chrome. Measured on
    # Chromium 141: the canvas hook's source contained "copy = null" twice, so
    # the noise was applied twice and the rendered fingerprint was neither the
    # one the config asked for nor reproducible across engines.
    #
    # A profile with extensions ASSIGNED is the third case, and it cannot have
    # both. Measured on Chromium 151 with an unpacked MV3 fixture, using the
    # marker its content script writes into a page as the evidence:
    #
    #     --load-extension=<dir>                           ran
    #     --load-extension + --disable-extensions          did NOT run
    #     --load-extension + --disable-extensions-except   ran
    #
    # So `--disable-extensions` wins, and a profile that is supposed to load an
    # extension has to be launched without it. That is a real change to the
    # profile's posture rather than a detail: an extension is enumerable, it
    # can inject into pages, and exclusivity existed to stop exactly that. The
    # note says so on every launch it applies to, per profile, so an operator
    # reading the launch record can see which profiles traded exclusivity for
    # an extension and which did not.
    assigned_extensions = _assigned_extension_payloads(profile_id)
    if assigned_extensions:
        paths = ",".join(p for _, p in assigned_extensions)
        cmd.append(f"--load-extension={paths}")
        # Pinning the allow-list to exactly our payloads means a Web Store
        # extension left in the profile directory by a previous run does not
        # silently come back with it.
        cmd.append(f"--disable-extensions-except={paths}")
        notes.append(
            f"Extensions ON for this profile: {len(assigned_extensions)} assigned "
            f"(--load-extension). Anti-detect exclusivity is OFF while they load."
        )
    elif settings.allow_webstore_extensions:
        notes.append("Web Store extensions allowed (exclusivity OFF — opt-in only)")
    else:
        cmd.append("--disable-extensions")
        notes.append("Anti-detect exclusivity ON (--disable-extensions)")

    cmd.extend([
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

    # One --enable-features for the whole launch: Chrome honours the last such
    # flag rather than merging them, so a second one anywhere would silently
    # drop this list.
    software_gpu = "--disable-gpu" in gpu_flags
    features = [f for f in chrome_enabled_features()
                if not (software_gpu and f == "Vulkan")]
    if features:
        cmd.append("--enable-features=" + ",".join(features))
        notes.append(f"Features: {','.join(features)}")

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

    # User-agent at the flag level — set on EVERY launch, not only when the
    # profile carries an explicit UA or a device preset. The flag is the only
    # part of the identity that survives with no CDP at all, so it is the
    # floor under the override: without it a no-preset profile shipped the
    # host's UA to every site it visited (F1).
    if ua:
        cmd.append(f"--user-agent={ua}")
        notes.append(f"User-Agent flag: {ua}")

    # The language, at the flag level, for the same reason the UA is here: it
    # is the part of the claim that exists before any CDP session does.
    # Chromium reads --lang at startup to pick its UI language and the initial
    # Accept-Language, and nothing was passing it, so a profile claiming
    # de-DE still asked servers for the host's languages on the very first
    # request -- the same shape of gap as F1, on a different header.
    _lang = geo_plan.get("language")
    if _lang:
        cmd.append(f"--lang={_lang}")
        if geo_plan.get("languages"):
            cmd.append("--accept-lang=" + ",".join(geo_plan["languages"]))
        notes.append(f"Language flags: --lang={_lang}")

    # CDP — always localhost-only
    debug_port = allocate_localhost_port()
    cmd.extend([
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={debug_port}",
    ])
    notes.append(f"CDP localhost-only on 127.0.0.1:{debug_port}")

    cmd.append(start)

    # ------------------------------------------------------------------
    # Spawn Chrome
    # ------------------------------------------------------------------
    env = os.environ.copy()
    env["OZONE_PLATFORM"] = "x11"
    # The process's own idea of local time. Chromium reads TZ at startup, so
    # this anchors the browser process itself -- not just the renderers the
    # CDP override reaches -- and it costs nothing. Without it the profile's
    # timezone existed only as a JavaScript patch, and any surface that patch
    # did not cover reported the host's zone: measured on this machine,
    # Date.prototype.toLocaleString returned Asia/Kolkata time while
    # Intl.DateTimeFormat().resolvedOptions().timeZone said America/New_York
    # -- a contradiction inside one page.
    _tz = str(profile.get("timezone") or fingerprint.get("timezone") or "").strip()
    if _tz:
        env["TZ"] = _tz

    # The font set the engine can see. Chromium resolves local fonts through
    # fontconfig, so pointing the child at a per-profile config is what stops
    # a profile claiming Windows from enumerating this host's Linux fonts --
    # and unlike a measureText hook it moves canvas, DOM, document.fonts,
    # workers and actual rendering together, because none of them is lying.
    try:
        _fc = fontconfig.prepare_for_profile(
            profile_id, fingerprint, chrome_profile_dir(profile_id), notes
        )
        if _fc is not None:
            env["FONTCONFIG_FILE"] = str(_fc)
    except Exception as e:  # never let font policy stop a launch  # noqa: BLE001 - the failure is recorded in the value this assigns
        _fault(notes, "font_policy_failed", str(e), profile_id=profile_id)
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        if slot_held:
            browser_manager.release_slot(profile_id)
        _record_state(profile_id, ProfileState.ERROR, note="spawn_failed: could not spawn Chrome process")
        return {"ok": False, "error": f"Failed to spawn Chrome: {e}", "errors": ["spawn_failed"], "cmd": cmd}

    rc = _settle_after_spawn(proc, debug_port)
    if rc is not None:
        err = (proc.stderr.read() or b"").decode("utf-8", "ignore")[-2500:]
        set_runtime(profile_id, "idle", None)
        release_localhost_port(debug_port)
        clear_runtime(profile_id)
        set_launch_state(profile_id, LaunchState.LAUNCH_FAILED, error=f"chrome exit {rc}", error_code="chrome_exit_immediate")
        _record_state(profile_id, ProfileState.ERROR, note=f"chrome exited immediately with code {rc}")
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
    _record_state(
        profile_id,
        ProfileState.STARTING,
        cdp_port=debug_port,
        pid=proc.pid,
        device_preset_id=(profile.get("deviceId") or None),
        network_config_id=(net.gateway_id or None),
        started_at=_db_now(),
    )
    log.info("launched profile %s pid=%s url=%s cdp=%s network=%s", profile_id, proc.pid, start, debug_port, net.mode.value)

    # ------------------------------------------------------------------
    # CDP ready + UA override
    # ------------------------------------------------------------------
    try:
        wait_for_cdp(debug_port, timeout_s=8.0)
        set_launch_state(profile_id, LaunchState.CDP_READY, pid=proc.pid, cdp_port=debug_port)
        notes.append(f"CDP ready on 127.0.0.1:{debug_port}")

        # Identity injection over CDP — fingerprint script, proxy auth and
        # cookies. This replaces the MV3 extension, which Google Chrome
        # refuses to load ("--disable-extensions-except is not allowed in
        # Google Chrome, ignoring"), leaving every one of those three jobs
        # undone while the console reported the profile healthy. Runs first
        # so a page is never unfrozen before its identity is installed.
        _note_identity_matrix(notes, fingerprint, profile_id)
        _note_engine_policy(notes, profile_id)

        try:
            notes.extend(_start_identity_injection(
                profile_id, debug_port, profile, fingerprint, net,
                ua_override=ua_override, geo_plan=geo_plan,
            ))
        except Exception as e:  # noqa: BLE001 - never fatal to a launch
            notes.append(f"identity injection skipped: {e}")
            log.warning("identity injection failed for %s: %s", profile_id, e)

        # Emulation on ONE pinned CDP session, HELD OPEN for the browser's
        # lifetime. Chromium drops Network.setUserAgentOverride and
        # Emulation.setTouchEmulationEnabled when the session that set them
        # disconnects — only Emulation.setDeviceMetricsOverride survives — so
        # a one-shot "send and close" can never make them persist. That is
        # exactly how the UA override came to report success while the wire
        # carried the host identity (F1).
        #
        # The device-preset plan runs first and the resolved wire identity
        # goes last, so the same values the assertion below checks are the
        # last thing written. Never fatal: failures land in notes and, for
        # the UA override, in the assertion.
        try:
            from .cdp_identity import apply_ua_override, open_pinned_cdp_session

            _sess = open_pinned_cdp_session(debug_port)
            if _sess is None:
                _fault(
                    notes,
                    "cdp_session_unavailable",
                    "no CDP page target for the identity session; the UA/client-hints "
                    "override rests on the injection session alone",
                    profile_id=profile_id,
                )
            else:
                if device_preset is not None:
                    from ..fingerprint.mobile import MobileProfileConfig, apply_cdp_plan
                    _cfg = MobileProfileConfig.from_preset_dict(
                        device_preset.to_mobile_config_dict()
                    )
                    _results = apply_cdp_plan(_sess.send, _cfg.to_cdp_plan())
                    _ok = sum(1 for r in _results if r.get("ok"))
                    if _ok == len(_results) and _ok > 0:
                        notes.append(
                            f"CDP device emulation applied ({device_preset.id}: "
                            f"{_ok}/{len(_results)} steps)"
                        )
                    else:
                        _fault(
                            notes,
                            "device_emulation_incomplete",
                            f"device emulation for '{device_preset.id}' applied "
                            f"{_ok}/{len(_results)} steps; the profile is part-way "
                            "between the device it claims and the host",
                            profile_id=profile_id,
                        )
                    for _r in _results:
                        if not _r.get("ok"):
                            notes.append(
                                f"CDP emulation step failed: {_r.get('method')}: {_r.get('error')}"
                            )
                if apply_ua_override(_sess, fingerprint, ua, metadata=(ua_override or {}).get("userAgentMetadata")):
                    notes.append(
                        "CDP UA/client-hints override applied on a held session "
                        "(closed only when the browser exits)"
                    )
                else:
                    _fault(
                        notes,
                        "ua_override_failed",
                        "the held-session UA/client-hints override did not apply; "
                        "the profile may be sending the host's own identity (F1)",
                        profile_id=profile_id,
                    )
                # Held for the browser's lifetime, not for the length of this
                # function: the overrides above die with this session.
                _hold_device_session(profile_id, proc.pid, _sess)
        except Exception as e:  # noqa: BLE001 - never fatal to a launch
            _fault(
                notes,
                "identity_session_error",
                f"the whole CDP identity step was skipped ({e}); neither the device "
                "emulation nor the UA/client-hints override was applied",
                profile_id=profile_id,
            )

        # Faults raised anywhere else on the identity path -- resolving the wire
        # identity, reading a preset, attaching to targets -- have no route to
        # this profile's notes on their own (F11). Only the ones _fault() already
        # wrote are skipped, matched on the exact scope it stamps them with.
        for _f in identity_faults.faults_for(profile_id):
            if _f.where != _LAUNCHER_SCOPE:
                notes.append(_f.note())

        # ------------------------------------------------------------------
        # Prove it. Compare the headers the browser actually sends against
        # the identity we intended, and REFUSE the launch when they
        # disagree: a profile whose headers contradict its JavaScript is
        # more identifiable than an unmodified browser, and reporting one as
        # healthy is the defect this whole block exists to end.
        # An inconclusive probe is recorded, not fatal — see identity_assert.
        # ------------------------------------------------------------------
        if settings.identity_assert_enabled:
            try:
                from .identity_assert import verify_wire_identity

                assertion = verify_wire_identity(debug_port, ua_override)
            except Exception as e:  # noqa: BLE001 - a broken probe is inconclusive
                from .identity_assert import IdentityAssertion

                assertion = IdentityAssertion(reason=f"probe error: {e}")
                log.warning("identity probe failed for %s: %s", profile_id, e)
            notes.append(assertion.summary())
            upsert_runtime(
                profile_id,
                identity_ok=(assertion.ok if assertion.conclusive else None),
                identity_detail=assertion.to_dict(),
            )
            if assertion.failed:
                return _abort_launch(
                    profile_id,
                    proc.pid,
                    slot_held=slot_held,
                    error_code="identity_mismatch",
                    error=(
                        "Refusing to run this profile: its HTTP headers do not match "
                        "the identity it claims in JavaScript. "
                        + "; ".join(assertion.mismatches)
                    ),
                    notes=notes,
                    detail=assertion.to_dict(),
                )

        # Store egress IP in runtime if proxy health gave us one
        if proxy_health and proxy_health.egress_ip:
            upsert_runtime(
                profile_id,
                proxy_ok=True,
                egress_ip=proxy_health.egress_ip,
                last_proxy_check=proxy_health.checked_at,
            )

    except Exception as e:  # noqa: BLE001 - the failure is recorded in the value this assigns
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
    _record_state(profile_id, ProfileState.RUNNING)

    # The new window changed the grid: re-tile every running profile via CDP.
    _retile_running_best_effort("launch")

    try:
        if proc.stderr:
            proc.stderr.close()
    except Exception:  # noqa: BLE001,S110 - teardown; a failure here cannot be acted on
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

    # Extensions: record that the launch CARRIED them, then ask the browser
    # what it actually loaded. Those are different facts and the second one is
    # the only one allowed to say "installed" -- see extensions/manager.py.
    extension_report: dict[str, Any] = {}
    if assigned_extensions:
        extension_report = _verify_assigned_extensions(
            profile_id, debug_port, assigned_extensions)

    out: dict[str, Any] = {
        "ok": True,
        "pid": proc.pid,
        "url": start,
        "userDataDir": str(user_dir),
        "extensions": extension_report,
        # The identity-script SOURCE, not a loaded extension — nothing is
        # staged per profile any more.
        "extension": str(ext),
        "identityScript": str(ext / "evasion.js"),
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
        # The wire evidence travels with the launch result, so a caller never
        # has to take "override applied" on trust again.
        out["identityAssertion"] = rt.identity_detail
    return out


# ---------------------------------------------------------------------------
# Stop profile
# ---------------------------------------------------------------------------

def stop_profile(profile_id: str) -> dict[str, Any]:
    """Explicit user stop. Records a user-stop the supervisor will not restart.

    Sequence: STOPPING (noted as a user stop) -> SIGTERM -> wait until the pid
    is actually gone -> STOPPED -> release slot -> re-tile the remaining
    windows. The supervisor may restart only an unexpected exit from RUNNING,
    so a stopped profile stays stopped until the user presses Launch again.
    """
    # Drop the injection session first: it holds an open CDP socket and a
    # proxy password in memory, and neither should outlive the browser.
    try:
        from .cdp_inject import stop_injection
        stop_injection(profile_id)
    except Exception as e:  # noqa: BLE001 - teardown must not block a stop
        log.debug("injection teardown failed for %s: %s", profile_id, e)

    profile = get_profile(profile_id)
    pid = (profile or {}).get("pid")
    if not pid:
        set_runtime(profile_id, "idle", None)
        _release_cdp_port(profile_id)
        clear_runtime(profile_id)
        browser_manager.release_slot(profile_id)
        _record_state(profile_id, ProfileState.STOPPED, note="user stop (no pid recorded)")
        audit_log("profile_stop", profile_id=profile_id, ok=True, detail={"pid": None})
        return {"ok": True, "stopped": None, "note": "No running pid recorded"}
    # Record the user-stop FIRST: between listing RUNNING runtimes and its
    # restart check, the supervisor re-reads state, and anything that left
    # RUNNING is never treated as a crash.
    _record_state(profile_id, ProfileState.STOPPING, note="user stop requested")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        _record_state(profile_id, ProfileState.ERROR, note=f"cannot stop pid {pid}: {e}")
        return {"error": f"Cannot stop pid {pid}: {e}", "errors": ["permission"]}
    gone = _wait_pid_gone(pid)
    set_runtime(profile_id, "idle", None)
    _release_cdp_port(profile_id)
    clear_runtime(profile_id)
    browser_manager.release_slot(profile_id)
    if not gone:
        # Honest failure: SIGTERM was ignored. Do not claim STOPPED; leave
        # the profile in error so an explicit action can resolve it.
        _record_state(profile_id, ProfileState.ERROR, note=f"user stop: pid {pid} still alive after SIGTERM")
        audit_log("profile_stop", profile_id=profile_id, ok=False, detail={"pid": pid, "reason": "sigterm_ignored"})
        return {
            "ok": False,
            "stopped": pid,
            "error": f"pid {pid} did not exit after SIGTERM",
            "errors": ["stop_timeout"],
        }
    _record_state(profile_id, ProfileState.STOPPED, note="user stop")
    # The grid changed: re-tile whoever is still running so windows still
    # fill the work area.
    _retile_running_best_effort("stop")
    audit_log("profile_stop", profile_id=profile_id, ok=True, detail={"pid": pid})
    return {"ok": True, "stopped": pid}


def _allocate_debug_port() -> int:
    """Bind to port 0 to discover a free TCP port for Chrome remote debugging."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
