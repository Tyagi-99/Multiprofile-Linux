"""iOS-class device presets (7).

HONESTY: these are labeled platform "iOS-class" — never "iOS" — because a
Linux Chromium host cannot reproduce WebKit. UA (CriOS format), Client Hints,
viewport, and touch are applied; the engine remains Chromium. The capability
matrix records engine=PARTIAL with the reason stated plainly.
"""
from __future__ import annotations

from .preset import (
    IOS,
    MOBILE,
    PARTIAL,
    SUPPORTED,
    DevicePreset,
)

_CRIOS_FULL = "131.0.6778.73"

_IOS_CAPABILITIES = {
    "engine": {
        "status": PARTIAL,
        "note": "Real iPhones run WebKit; Linux Chromium cannot reproduce WebKit — "
        "UA/Client-Hints/viewport/touch are applied, the engine remains Chromium.",
    },
    "user_agent": {"status": SUPPORTED, "note": "CriOS UA string applied verbatim at launch."},
    "client_hints": {
        "status": SUPPORTED,
        "note": "Sec-CH-UA* headers and navigator.userAgentData values applied via CDP.",
    },
    "viewport_emulation": {
        "status": SUPPORTED,
        "note": "CSS viewport, DPR and screen geometry applied via Emulation.setDeviceMetricsOverride.",
    },
    "touch_input": {
        "status": SUPPORTED,
        "note": "Touch events and max touch points emulated via CDP input domain.",
    },
    "webgl": {
        "status": PARTIAL,
        "note": "WebGL works through Chromium/ANGLE on the host GPU — feature set and "
        "precision differ from iOS WebKit's WebGL implementation.",
    },
    "media": {
        "status": PARTIAL,
        "note": "iOS WebKit media behaviors (e.g. inline video policies, HEVC handling) "
        "are not reproducible in Chromium on Linux.",
    },
    "fonts": {
        "status": PARTIAL,
        "note": "San Francisco and the iOS system font stack are not present on Linux; "
        "fallbacks approximate metrics but do not match.",
    },
    "sensors": {
        "status": PARTIAL,
        "note": "Orientation/motion sensors are emulated via CDP, not real hardware sensors.",
    },
}

_IOS_LIMITATIONS = [
    "Platform is iOS-class, not iOS: the rendering engine remains Chromium (Blink), not WebKit.",
    "hardware_concurrency and device_memory_gb are representative class values; they vary by unit/SKU.",
    "iOS system fonts (San Francisco) and WebKit media behaviors are not reproduced.",
    "WebGL works but through Chromium/ANGLE, not iOS WebKit's implementation.",
]


def _ch_brands() -> list:
    return [
        {"brand": "Chromium", "version": "131"},
        {"brand": "Google Chrome", "version": "131"},
        {"brand": "Not-A.Brand", "version": "99"},
    ]


def _ch_full_version_list() -> list:
    return [
        {"brand": "Chromium", "fullVersion": _CRIOS_FULL},
        {"brand": "Google Chrome", "fullVersion": _CRIOS_FULL},
        {"brand": "Not-A.Brand", "fullVersion": "99.0.0.0"},
    ]


def _iphone(
    *,
    id: str,
    name: str,
    ios_version: str,
    vw: int,
    vh: int,
    sw: int,
    sh: int,
    mem_gb: float,
) -> DevicePreset:
    return DevicePreset(
        id=id,
        name=name,
        manufacturer="Apple",
        model=name,
        category=MOBILE,
        platform=IOS,
        browser_family="Chrome",
        browser_version_policy="match-engine",
        user_agent=(
            f"Mozilla/5.0 (iPhone; CPU iPhone OS {ios_version} like Mac OS X) "
            f"AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/{_CRIOS_FULL} "
            "Mobile/15E148 Safari/604.1"
        ),
        client_hints={
            "brands": _ch_brands(),
            "full_version_list": _ch_full_version_list(),
            "platform": "iOS",
            "platform_version": ios_version.replace("_", "."),
            "architecture": "arm",
            "model": "iPhone",
            "mobile": True,
        },
        viewport={"width": vw, "height": vh},
        screen={"width": sw, "height": sh},
        device_scale_factor=3.0,
        mobile=True,
        touch=True,
        max_touch_points=5,
        hardware_concurrency=6,  # A17 Pro / A18 are 6-core
        device_memory_gb=mem_gb,
        language_defaults={"language": "en-US", "accept_language": "en-US,en;q=0.9"},
        timezone_policy="auto",
        webgl_policy="real",
        media_capabilities={"video_codecs": ["h264", "hevc"], "audio_codecs": ["aac"]},
        capability_matrix=dict(_IOS_CAPABILITIES),
        limitations=list(_IOS_LIMITATIONS),
    )


IOS_PRESETS = [
    # iPhone 16: 1179x2556 @3 -> 393x852
    _iphone(id="iphone-16", name="iPhone 16", ios_version="18_0",
            vw=393, vh=852, sw=1179, sh=2556, mem_gb=8),
    # iPhone 16 Plus: 1290x2796 @3 -> 430x932
    _iphone(id="iphone-16-plus", name="iPhone 16 Plus", ios_version="18_0",
            vw=430, vh=932, sw=1290, sh=2796, mem_gb=8),
    # iPhone 16 Pro: 1206x2622 @3 -> 402x874
    _iphone(id="iphone-16-pro", name="iPhone 16 Pro", ios_version="18_0",
            vw=402, vh=874, sw=1206, sh=2622, mem_gb=8),
    # iPhone 16 Pro Max: 1320x2868 @3 -> 440x956
    _iphone(id="iphone-16-pro-max", name="iPhone 16 Pro Max", ios_version="18_0",
            vw=440, vh=956, sw=1320, sh=2868, mem_gb=8),
    # iPhone 15: 1179x2556 @3 -> 393x852
    _iphone(id="iphone-15", name="iPhone 15", ios_version="17_4",
            vw=393, vh=852, sw=1179, sh=2556, mem_gb=6),
    # iPhone 15 Pro: 1179x2556 @3 -> 393x852
    _iphone(id="iphone-15-pro", name="iPhone 15 Pro", ios_version="17_4",
            vw=393, vh=852, sw=1179, sh=2556, mem_gb=8),
    # iPhone 15 Pro Max: 1290x2796 @3 -> 430x932
    _iphone(id="iphone-15-pro-max", name="iPhone 15 Pro Max", ios_version="17_4",
            vw=430, vh=932, sw=1290, sh=2796, mem_gb=8),
]

assert len(IOS_PRESETS) == 7, f"expected 7 iOS presets, got {len(IOS_PRESETS)}"
