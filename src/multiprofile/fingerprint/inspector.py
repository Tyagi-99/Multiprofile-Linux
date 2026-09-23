"""Fingerprint inspector: EXPECTED vs RUNTIME per component.

``fingerprint_inspector_data(profile, runtime_data=None)`` returns
``{"profile_id", "components": [...], "generated_at"}`` where each
component is ``{"name", "expected", "runtime", "status", "note"}`` with
status PASS | FAIL | UNKNOWN.

Honesty rules:
  - runtime_data None  -> every component is UNKNOWN with the note
    "profile not running — runtime values require a live CDP session".
    Runtime values are NEVER invented.
  - runtime_data given -> expected values (built deterministically from the
    profile seed) are compared against the runtime values with a plain
    key-diff; mismatches are FAIL with the differing keys named.
  - WebGL expected config contains policy only, never vendor/renderer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ._core import device_preset_for_profile

from . import audio, battery, canvas, client_hints, fonts, hardware, media, navigator, network_info, plugins, screen, touch, webgl
from ._core import normalize_seed

_UNKNOWN_NOTE = "profile not running — runtime values require a live CDP session"


def _diff(expected: Any, runtime: Any, path: str = "") -> list[str]:
    """Return list of dotted paths where expected and runtime differ."""
    diffs: list[str] = []
    if isinstance(expected, dict) and isinstance(runtime, dict):
        for key in expected:
            sub = f"{path}.{key}" if path else str(key)
            if key not in runtime:
                diffs.append(f"{sub} (missing in runtime)")
            else:
                diffs.extend(_diff(expected[key], runtime[key], sub))
    elif isinstance(expected, list) and isinstance(runtime, list):
        if len(expected) != len(runtime):
            diffs.append(f"{path} (list length {len(expected)} != {len(runtime)})")
        else:
            for i, (e, r) in enumerate(zip(expected, runtime)):
                diffs.extend(_diff(e, r, f"{path}[{i}]"))
    else:
        if expected != runtime:
            diffs.append(f"{path} (expected {expected!r}, runtime {runtime!r})")
    return diffs


def _component(name: str, expected: dict[str, Any], runtime: Any) -> dict[str, Any]:
    if runtime is None:
        return {
            "name": name,
            "expected": expected,
            "runtime": None,
            "status": "UNKNOWN",
            "note": _UNKNOWN_NOTE,
        }
    diffs = _diff(expected, runtime)
    if not diffs:
        return {
            "name": name,
            "expected": expected,
            "runtime": runtime,
            "status": "PASS",
            "note": "runtime matches expected config",
        }
    return {
        "name": name,
        "expected": expected,
        "runtime": runtime,
        "status": "FAIL",
        "note": "runtime differs from expected: " + "; ".join(diffs[:8]),
    }


def _webgl_component(profile: dict[str, Any], runtime_data: dict[str, Any] | None) -> dict[str, Any]:
    expected = webgl.build(None, profile)
    runtime = runtime_data.get("webgl") if runtime_data else None
    if runtime is None:
        return {
            "name": "WebGL",
            "expected": expected["expected"],
            "runtime": None,
            "status": "UNKNOWN",
            "note": _UNKNOWN_NOTE,
        }
    status = (runtime.get("status") or "UNKNOWN").upper()
    note = (
        f"runtime diagnostic captured: status={status}, "
        f"vendor={runtime.get('vendor')!r}, renderer={runtime.get('renderer')!r}"
    )
    return {
        "name": "WebGL",
        "expected": expected["expected"],
        "runtime": runtime,
        "status": "PASS" if status in ("REAL_GPU", "SOFTWARE") else "FAIL",
        "note": note,
    }


def fingerprint_inspector_data(
    profile: dict[str, Any],
    runtime_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the per-component inspector report for a profile."""
    seed = normalize_seed(profile)
    rt = runtime_data or {}
    preset = device_preset_for_profile(profile)

    def _pick_component(expected: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        return {k: expected[k] for k in keys if k in expected}

    nav = navigator.build(seed, profile, preset)
    scr = screen.build(seed, profile, preset)
    ch = client_hints.build(seed, profile, preset)
    cvs = canvas.build(seed, profile, preset)
    aud = audio.build(seed, profile, preset)
    med = media.build(seed, profile, preset)
    fnt = fonts.build(seed, profile, preset)
    plg = plugins.build(seed, profile, preset)
    net = network_info.build(seed, profile, preset)
    twn = {"timezone": profile.get("timezone") or "America/New_York"}
    lan = {"language": profile.get("language") or "en-US"}
    hw = hardware.build(seed, profile, preset)
    tch = touch.build(seed, profile, preset)
    bat = battery.build(seed, profile, preset)

    components = [
        _component("Navigator", _pick_component(nav, [
            "userAgent", "platform", "language", "languages",
            "hardwareConcurrency", "deviceMemory", "maxTouchPoints",
        ]), rt.get("navigator")),
        _component("Screen", _pick_component(scr, [
            "width", "height", "availWidth", "availHeight", "colorDepth",
            "pixelDepth", "devicePixelRatio", "orientation",
        ]), rt.get("screen")),
        _component("Client Hints", _pick_component(ch, [
            "brands", "fullVersionList", "platform", "platformVersion",
            "architecture", "model", "mobile", "bitness",
        ]), rt.get("clientHints")),
        _component("Canvas", _pick_component(cvs, ["mode", "seed", "noiseSalt"]), rt.get("canvas")),
        _webgl_component(profile, runtime_data),
        _component("Audio", _pick_component(aud, ["status", "detail"]), rt.get("audio")),
        _component("Media", _pick_component(med, ["deviceIds"]), rt.get("media")),
        _component("Fonts", _pick_component(fnt, ["status", "expected"]), rt.get("fonts")),
        _component("Plugins", _pick_component(plg, ["plugins"]), rt.get("plugins")),
        _component("Network", _pick_component(net, ["connection"]), rt.get("network")),
        _component("Timezone", twn, rt.get("timezone")),
        _component("Language", lan, rt.get("language")),
        _component("Hardware", _pick_component(hw, [
            "hardwareConcurrency", "deviceMemory", "platform",
        ]), rt.get("hardware")),
        _component("Touch", _pick_component(tch, [
            "maxTouchPoints", "touchEvents",
        ]), rt.get("touch")),
        _component("Battery", _pick_component(bat, ["status", "expected"]), rt.get("battery")),
    ]

    return {
        "profile_id": profile.get("id"),
        "components": components,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
