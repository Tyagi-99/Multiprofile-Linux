"""Tablet presets (3): iPad Pro 11", iPad Air, Galaxy Tab S9.

iPad entries are platform "iPadOS-class" (same WebKit honesty caveat as
iOS-class phones). Galaxy Tab S9 is a genuine Android tablet.
"""
from __future__ import annotations

from .preset import (
    ANDROID,
    IPADOS,
    PARTIAL,
    SUPPORTED,
    TABLET,
    DevicePreset,
)

_CRIOS_FULL = "131.0.6778.73"
_CHROME_ANDROID = "Chrome/131.0.0.0"


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


_IPAD_CAPABILITIES = {
    "engine": {
        "status": PARTIAL,
        "note": "Real iPads run WebKit; Linux Chromium cannot reproduce WebKit — "
        "UA/Client-Hints/viewport/touch are applied, the engine remains Chromium.",
    },
    "user_agent": {"status": SUPPORTED, "note": "CriOS iPad UA string applied verbatim at launch."},
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
        "precision differ from iPadOS WebKit's WebGL implementation.",
    },
    "media": {
        "status": PARTIAL,
        "note": "iPadOS WebKit media behaviors are not reproducible in Chromium on Linux.",
    },
    "fonts": {
        "status": PARTIAL,
        "note": "San Francisco and the iPadOS system font stack are not present on Linux.",
    },
}

_IPAD_LIMITATIONS = [
    "Platform is iPadOS-class, not iPadOS: the rendering engine remains Chromium (Blink), not WebKit.",
    "hardware_concurrency and device_memory_gb are representative class values; they vary by unit/SKU.",
]


def _ipad(*, id: str, name: str, ipados_version: str, vw: int, vh: int,
          sw: int, sh: int, cores: int, mem_gb: float) -> DevicePreset:
    return DevicePreset(
        id=id,
        name=name,
        manufacturer="Apple",
        model=name,
        category=TABLET,
        platform=IPADOS,
        browser_family="Chrome",
        browser_version_policy="match-engine",
        user_agent=(
            f"Mozilla/5.0 (iPad; CPU OS {ipados_version} like Mac OS X) "
            f"AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/{_CRIOS_FULL} "
            "Mobile/15E148 Safari/604.1"
        ),
        client_hints={
            "brands": _ch_brands(),
            "full_version_list": _ch_full_version_list(),
            "platform": "iOS",
            "platform_version": ipados_version.replace("_", "."),
            "architecture": "arm",
            "model": "iPad",
            "mobile": False,
        },
        viewport={"width": vw, "height": vh},
        screen={"width": sw, "height": sh},
        device_scale_factor=2.0,
        mobile=False,
        touch=True,
        max_touch_points=5,
        hardware_concurrency=cores,
        device_memory_gb=mem_gb,
        language_defaults={"language": "en-US", "accept_language": "en-US,en;q=0.9"},
        timezone_policy="auto",
        webgl_policy="real",
        media_capabilities={"video_codecs": ["h264", "hevc"], "audio_codecs": ["aac"]},
        capability_matrix=dict(_IPAD_CAPABILITIES),
        limitations=list(_IPAD_LIMITATIONS),
    )


TABLET_PRESETS = [
    # iPad Pro 11": 1668x2424 @2 -> 834x1212
    _ipad(id="ipad-pro-11", name='iPad Pro 11"', ipados_version="18_0",
          vw=834, vh=1212, sw=1668, sh=2424, cores=10, mem_gb=8),
    # iPad Air: 1640x2360 @2 -> 820x1180
    _ipad(id="ipad-air", name="iPad Air", ipados_version="18_0",
          vw=820, vh=1180, sw=1640, sh=2360, cores=8, mem_gb=8),
    # Galaxy Tab S9: 1600x2560 @2 -> 800x1280 (Android tablet, desktop-style UA without "Mobile")
    DevicePreset(
        id="galaxy-tab-s9",
        name="Galaxy Tab S9",
        manufacturer="Samsung",
        model="Galaxy Tab S9",
        category=TABLET,
        platform=ANDROID,
        browser_family="Chrome",
        browser_version_policy="match-engine",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 13; SM-X710) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) {_CHROME_ANDROID} Safari/537.36"
        ),
        client_hints={
            "brands": _ch_brands(),
            "full_version_list": _ch_full_version_list(),
            "platform": "Android",
            "platform_version": "13.0.0",
            "architecture": "arm",
            "model": "SM-X710",
            "mobile": False,
        },
        viewport={"width": 800, "height": 1280},
        screen={"width": 1600, "height": 2560},
        device_scale_factor=2.0,
        mobile=False,
        touch=True,
        max_touch_points=10,
        hardware_concurrency=8,
        device_memory_gb=8,
        language_defaults={"language": "en-US", "accept_language": "en-US,en;q=0.9"},
        timezone_policy="auto",
        webgl_policy="real",
        media_capabilities={"video_codecs": ["h264", "vp8", "vp9"], "audio_codecs": ["aac", "opus"]},
        capability_matrix={
            "engine": {
                "status": SUPPORTED,
                "note": "Chromium on Linux runs the same Blink/V8 engine line as Chrome for Android.",
            },
            "user_agent": {"status": SUPPORTED, "note": "Tablet UA string applied verbatim at launch."},
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
                "note": "WebGL works, but the renderer string comes from the host Linux GPU — "
                "never the tablet's real GPU. webgl_policy is 'real'.",
            },
            "media": {
                "status": PARTIAL,
                "note": "Codec support follows the Linux Chromium build, not the device's hardware decoders.",
            },
            "fonts": {
                "status": PARTIAL,
                "note": "System fonts come from the Linux host, not the device's OEM font stack.",
            },
        },
        limitations=[
            "hardware_concurrency and device_memory_gb are representative class values; they vary by unit/SKU.",
            "WebGL renderer string reflects the Linux host GPU, not the tablet's GPU.",
        ],
    ),
]

assert len(TABLET_PRESETS) == 3, f"expected 3 tablet presets, got {len(TABLET_PRESETS)}"
