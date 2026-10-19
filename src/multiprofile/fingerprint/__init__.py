"""Fingerprint package (façade).

Backward-compatible façade over the component package.  The historical
``fingerprint`` module names all keep working with identical behaviour:

  - ``normalize_seed(profile)``      — stable int seed from profile
  - ``build_fingerprint(profile)``   — legacy deterministic fingerprint dict
    (byte-identical to the old implementation; consumed by the launcher,
    the extension config and existing tests)
  - ``fingerprint_report(profile)``  — UPGRADED: returns the inspector's
    component format (EXPECTED / RUNTIME / STATUS per property) plus the
    legacy ``warnings`` / ``consistent`` / ``summary`` keys the API uses.

Component modules (each exposes ``build(seed, profile, device_preset=None)``):
  navigator, screen, client_hints, canvas, webgl, audio, fonts, media,
  plugins, network_info, battery, touch, hardware, mobile, inspector.
"""
from __future__ import annotations

import re
from typing import Any

from . import (
    audio,
    battery,
    canvas,
    client_hints,
    fonts,
    hardware,
    inspector,
    media,
    mobile,
    navigator,
    network_info,
    plugins,
    screen,
    touch,
    webgl,
)
from ._core import _ua_platform_mismatch, build_fingerprint, device_preset_for_profile, normalize_seed
from .inspector import fingerprint_inspector_data
from .mobile import MobileProfileConfig, apply_cdp_plan

__all__ = [
    # legacy public API (identical behaviour)
    "normalize_seed",
    "build_fingerprint",
    "effective_fingerprint",
    "fingerprint_report",
    "_ua_platform_mismatch",
    # new API
    "fingerprint_inspector_data",
    "device_preset_for_profile",
    "MobileProfileConfig",
    "apply_cdp_plan",
    # component modules
    "audio", "battery", "canvas", "client_hints", "fonts", "hardware",
    "inspector", "media", "mobile", "navigator", "network_info",
    "plugins", "screen", "touch", "webgl",
]


def effective_fingerprint(
    profile: dict[str, Any], fingerprint: dict[str, Any] | None = None
) -> dict[str, Any]:
    """This profile's fingerprint as it should appear on the wire today.

    The stored fingerprint, with its Chromium version claims rewritten to the
    engine that is installed now. Everything seed-derived is untouched: the
    identity is the same identity, it simply stops announcing a browser build
    the host has not had for a month.

    One function so the console and the wire cannot disagree. The launcher
    reads this for ``Network.setUserAgentOverride``; the fingerprint report
    behind ``GET /api/profiles/<id>/fingerprint`` reads it so the operator is
    shown what will actually be claimed.

    A UA the operator pinned themselves is returned unaligned, version and
    all. See ``api.user_agents.operator_pinned_user_agent`` for why that
    distinction is version-insensitive.
    """
    fp = fingerprint
    if not isinstance(fp, dict) or not fp:
        stored = profile.get("fingerprint")
        fp = stored if isinstance(stored, dict) and stored else build_fingerprint(profile)
    try:
        from ..api.user_agents import operator_pinned_user_agent

        pinned = operator_pinned_user_agent(profile)
    except Exception:  # noqa: BLE001 - never renumber on a catalogue failure
        pinned = bool((profile.get("userAgent") or "").strip())
    if pinned:
        return dict(fp)
    from ..engine import align_fingerprint

    return align_fingerprint(fp)


def fingerprint_report(profile: dict[str, Any]) -> dict[str, Any]:
    """Inspector-style fingerprint report: EXPECTED/RUNTIME/STATUS per property.

    Upgrades the legacy report: the ``components`` list holds the 15
    inspector components (Navigator, Screen, Client Hints, Canvas, WebGL,
    Audio, Media, Fonts, Plugins, Network, Timezone, Language, Hardware,
    Touch, Battery) each with expected/runtime/status/note.  The legacy
    ``warnings``, ``consistent`` and ``summary`` keys are kept so
    ``GET /api/profiles/<id>/fingerprint`` and its warning helper keep
    working unchanged.

    Runtime values require a live CDP session; with none running every
    component reports UNKNOWN — values are never invented.
    """
    fp = effective_fingerprint(profile)

    # Legacy mismatch warnings (kept for the API surface).
    warnings = _ua_platform_mismatch(
        fp.get("userAgent") or profile.get("userAgent") or "",
        fp.get("platform", ""),
    )
    touch_points = fp.get("touch", 0)
    is_mobile_fp = touch_points > 0
    ua_mobile = bool(profile.get("isMobile")) or bool(
        re.search(r"Mobile|Android|iPhone|iPad", profile.get("userAgent") or "", re.I)
    )
    if is_mobile_fp != ua_mobile and profile.get("userAgent"):
        if is_mobile_fp and not ua_mobile:
            warnings.append("Fingerprint is mobile (touch>0) but profile isMobile/UA looks desktop")
        if not is_mobile_fp and ua_mobile:
            warnings.append("Fingerprint is desktop (touch=0) but UA looks mobile")

    screen_d = fp.get("screen") or {}
    summary = (
        f"{fp.get('platform')} | {screen_d.get('w')}x{screen_d.get('h')}@{screen_d.get('dpr')} | "
        f"{fp.get('cores')} cores / {fp.get('memory')}GB | "
        f"touch={touch_points} | tz={fp.get('timezone')} | "
        f"webrtc={fp.get('webrtcMode') or fp.get('webrtcPolicy')}"
    )

    data = fingerprint_inspector_data(profile)

    return {
        "profile_id": profile.get("id"),
        # legacy keys (API compatibility)
        "ok": True,
        "seed": fp.get("seed"),
        "summary": summary,
        "fingerprint": fp,
        "warnings": warnings,
        "consistent": len(warnings) == 0,
        # upgraded inspector format
        "components": data["components"],
        "generated_at": data["generated_at"],
    }
