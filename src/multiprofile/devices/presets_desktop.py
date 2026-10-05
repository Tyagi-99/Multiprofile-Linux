"""Desktop presets (3): Windows 11 Chrome, Ubuntu Linux Chrome, macOS-class Chrome.

macOS-class is labeled honestly: macOS GPU/media characteristics are not
reproducible on Linux, so the capability matrix records the relevant entries
as PARTIAL with the reason stated.
"""
from __future__ import annotations

from .preset import (
    DESKTOP,
    LINUX,
    MACOS,
    PARTIAL,
    SUPPORTED,
    WINDOWS,
    DevicePreset,
)

_CRIOS_FULL = "131.0.6778.73"


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


def _desktop_base(extra: dict) -> dict:
    base = {
        "engine": {"status": SUPPORTED, "note": "Native Chromium on Linux; same engine line as desktop Chrome."},
        "user_agent": {"status": SUPPORTED, "note": "UA string applied verbatim at launch."},
        "client_hints": {
            "status": SUPPORTED,
            "note": "Sec-CH-UA* headers and navigator.userAgentData values applied via CDP.",
        },
        "viewport_emulation": {"status": SUPPORTED, "note": "Window geometry applied at launch."},
        "touch_input": {"status": SUPPORTED, "note": "Desktop presets use mouse/keyboard; touch disabled."},
        "webgl": {
            "status": SUPPORTED,
            "note": "WebGL runs on the real host GPU (or SwiftShader); webgl_policy is 'real', "
            "never a fabricated renderer string.",
        },
        "media": {"status": SUPPORTED, "note": "Desktop media pipeline matches the Linux Chromium build."},
        "fonts": {"status": SUPPORTED, "note": "Host Linux font stack; desktop sites render with standard fallbacks."},
    }
    base.update(extra)
    return base


DESKTOP_PRESETS = [
    DevicePreset(
        id="win11-chrome",
        name="Windows 11 Chrome",
        manufacturer="Generic",
        model="Windows 11 PC",
        category=DESKTOP,
        platform=WINDOWS,
        browser_family="Chrome",
        browser_version_policy="match-engine",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        client_hints={
            "brands": _ch_brands(),
            "full_version_list": _ch_full_version_list(),
            "platform": "Windows",
            "platform_version": "15.0.0",
            "architecture": "x86",
            "model": "",
            "mobile": False,
        },
        viewport={"width": 1920, "height": 1080},
        screen={"width": 1920, "height": 1080},
        device_scale_factor=1.0,
        mobile=False,
        touch=False,
        max_touch_points=0,
        hardware_concurrency=8,
        device_memory_gb=16,
        language_defaults={"language": "en-US", "accept_language": "en-US,en;q=0.9"},
        timezone_policy="auto",
        webgl_policy="real",
        media_capabilities={"video_codecs": ["h264", "vp8", "vp9", "av1"], "audio_codecs": ["aac", "opus"]},
        capability_matrix=_desktop_base({}),
        limitations=[
            "hardware_concurrency and device_memory_gb are representative class values; they vary by unit.",
            "Windows-specific behaviors (e.g. ClearType, DirectWrite text rendering) are not reproduced on Linux.",
        ],
    ),
    DevicePreset(
        id="ubuntu-chrome",
        name="Ubuntu Linux Chrome",
        manufacturer="Generic",
        model="Ubuntu 24.04 PC",
        category=DESKTOP,
        platform=LINUX,
        browser_family="Chrome",
        browser_version_policy="match-engine",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        client_hints={
            "brands": _ch_brands(),
            "full_version_list": _ch_full_version_list(),
            "platform": "Linux",
            "platform_version": "",
            "architecture": "x86",
            "model": "",
            "mobile": False,
        },
        viewport={"width": 1920, "height": 1080},
        screen={"width": 1920, "height": 1080},
        device_scale_factor=1.0,
        mobile=False,
        touch=False,
        max_touch_points=0,
        hardware_concurrency=8,
        device_memory_gb=16,
        language_defaults={"language": "en-US", "accept_language": "en-US,en;q=0.9"},
        timezone_policy="auto",
        webgl_policy="real",
        media_capabilities={"video_codecs": ["h264", "vp8", "vp9", "av1"], "audio_codecs": ["aac", "opus"]},
        capability_matrix=_desktop_base({}),
        limitations=[
            "hardware_concurrency and device_memory_gb are representative class values; they vary by unit.",
        ],
    ),
    DevicePreset(
        id="macos-chrome",
        name="macOS-class Chrome",
        manufacturer="Apple",
        model="Mac (macOS-class)",
        category=DESKTOP,
        platform=MACOS,
        browser_family="Chrome",
        browser_version_policy="match-engine",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        client_hints={
            "brands": _ch_brands(),
            "full_version_list": _ch_full_version_list(),
            "platform": "macOS",
            "platform_version": "14.7.0",
            "architecture": "x86",
            "model": "",
            "mobile": False,
        },
        viewport={"width": 1440, "height": 900},
        screen={"width": 2880, "height": 1800},
        device_scale_factor=2.0,
        mobile=False,
        touch=False,
        max_touch_points=0,
        hardware_concurrency=8,
        device_memory_gb=16,
        language_defaults={"language": "en-US", "accept_language": "en-US,en;q=0.9"},
        timezone_policy="auto",
        webgl_policy="real",
        media_capabilities={"video_codecs": ["h264", "vp8", "vp9", "av1"], "audio_codecs": ["aac", "opus"]},
        capability_matrix=_desktop_base(
            {
                "webgl": {
                    "status": PARTIAL,
                    "note": "macOS GPU/media characteristics not reproducible on Linux — "
                    "WebGL runs on the Linux host GPU, not Apple silicon/AMD macOS drivers.",
                },
                "media": {
                    "status": PARTIAL,
                    "note": "macOS GPU/media characteristics not reproducible on Linux — "
                    "hardware decode behavior follows the Linux Chromium build.",
                },
                "fonts": {
                    "status": PARTIAL,
                    "note": "macOS font rendering (Core Text) is not reproducible on Linux; "
                    "metrics approximate but do not match.",
                },
            }
        ),
        limitations=[
            "Platform is macOS-class, not macOS: macOS GPU/media characteristics not reproducible on Linux.",
            "hardware_concurrency and device_memory_gb are representative class values; they vary by unit.",
            "Core Text font rendering is not reproduced.",
        ],
    ),
]

assert len(DESKTOP_PRESETS) == 3, f"expected 3 desktop presets, got {len(DESKTOP_PRESETS)}"
