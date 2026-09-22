"""Deterministic fingerprint builder for profile consistency (Phase 2 foundation)."""
from __future__ import annotations

import hashlib
import re
from typing import Any


def normalize_seed(profile: dict[str, Any]) -> int:
    """Return a stable positive int seed from profile.seed or profile id."""
    raw = profile.get("seed")
    if raw is not None:
        try:
            return abs(int(raw)) % (2**31 - 1) or 1
        except (TypeError, ValueError):
            pass
    pid = str(profile.get("id") or "default")
    h = hashlib.sha256(pid.encode("utf-8")).hexdigest()
    return (int(h[:8], 16) % (2**31 - 1)) or 1


def _rng(seed: int, salt: int = 0) -> float:
    """Deterministic 0..1 float from seed+salt."""
    h = hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _pick(seed: int, salt: int, choices: list):
    idx = int(_rng(seed, salt) * len(choices)) % len(choices)
    return choices[idx]


def _hex_id(rng_val: float) -> str:
    """Generate a stable hex media device ID from a deterministic float.

    Converts a 0..1 float to a 64-char hex string — same format as real
    browser-generated device IDs.
    """
    import struct
    raw = struct.pack(">d", rng_val)
    return hashlib.sha256(raw).hexdigest()


# Platform pools — UA must agree with platform/touch/screen
_DESKTOP_WIN = {
    "platform": "Win32",
    "touch": 0,
    "screens": [(1920, 1080, 1040, 1), (2560, 1440, 1400, 1), (1366, 768, 728, 1), (1536, 864, 824, 1.25)],
    "cores": [4, 6, 8, 12, 16],
    "memory": [4, 8, 16],
    "gpus": [
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ],
    "ch_platform": "Windows",
    "architecture": "x86",
    "bitness": "64",
    "platformVersion": "15.0.0",
    "model": "",
}

_DESKTOP_MAC = {
    "platform": "MacIntel",
    "touch": 0,
    "screens": [(1440, 900, 877, 2), (1680, 1050, 1027, 2), (1728, 1117, 1080, 2), (2560, 1600, 1570, 2)],
    "cores": [8, 10, 12],
    "memory": [8, 16],
    "gpus": [
        ("Google Inc. (Apple)", "ANGLE (Apple, Apple M1, OpenGL ES 3.0)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, Apple M2 Pro, OpenGL ES 3.0)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, Apple M3, OpenGL ES 3.0)"),
    ],
    "ch_platform": "macOS",
    "architecture": "arm",
    "bitness": "64",
    "platformVersion": "14.5.0",
    "model": "",
}

_DESKTOP_LINUX = {
    "platform": "Linux x86_64",
    "touch": 0,
    "screens": [(1920, 1080, 1040, 1), (2560, 1440, 1400, 1), (1366, 768, 728, 1)],
    "cores": [4, 8, 12, 16],
    "memory": [8, 16],
    "gpus": [
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 OpenGL 4.5)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Mesa Intel(R) UHD Graphics 620)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Series)"),
    ],
    "ch_platform": "Linux",
    "architecture": "x86",
    "bitness": "64",
    "platformVersion": "6.5.0",
    "model": "",
}

_MOBILE_ANDROID = {
    "platform": "Linux armv8l",
    "touch": 5,
    "screens": [(412, 915, 915, 2.625), (360, 800, 800, 3), (393, 851, 851, 2.75), (412, 892, 892, 2.625)],
    "cores": [8],
    "memory": [4, 6, 8],
    "gpus": [
        ("Google Inc. (Qualcomm)", "ANGLE (Qualcomm, Adreno (TM) 740, OpenGL ES 3.2)"),
        ("Google Inc. (Qualcomm)", "ANGLE (Qualcomm, Adreno (TM) 650, OpenGL ES 3.2)"),
        ("Google Inc. (ARM)", "ANGLE (ARM, Mali-G78, OpenGL ES 3.2)"),
    ],
    "ch_platform": "Android",
    "architecture": "",
    "bitness": "",
    "platformVersion": "13.0.0",
    "model": "Pixel 7",
}

_MOBILE_IPHONE = {
    "platform": "iPhone",
    "touch": 5,
    "screens": [(393, 852, 852, 3), (390, 844, 844, 3), (428, 926, 926, 3)],
    "cores": [6],
    "memory": [None],  # iOS does not expose deviceMemory
    "gpus": [
        ("Google Inc. (Apple)", "ANGLE (Apple, Apple GPU, OpenGL ES 3.0)"),
    ],
    "ch_platform": "iOS",
    "architecture": "arm",
    "bitness": "64",
    "platformVersion": "17.4.0",
    "model": "iPhone",
}


def _detect_pool(ua: str, is_mobile: bool) -> dict:
    ua_l = (ua or "").lower()
    if "iphone" in ua_l or "ipad" in ua_l:
        return _MOBILE_IPHONE
    if "android" in ua_l:
        return _MOBILE_ANDROID
    if "macintosh" in ua_l or "mac os x" in ua_l:
        return _DESKTOP_MAC
    if "linux" in ua_l and "android" not in ua_l:
        return _DESKTOP_LINUX
    if is_mobile and "android" not in ua_l and "iphone" not in ua_l:
        # mobile flag but desktop-looking UA — prefer Android pool for consistency
        return _MOBILE_ANDROID
    return _DESKTOP_WIN


def _chrome_version(ua: str) -> str:
    m = re.search(r"Chrome/([\d.]+)", ua or "")
    if m:
        return m.group(1)
    return "131.0.0.0"


def build_fingerprint(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic fingerprint dict from seed + UA/isMobile."""
    seed = normalize_seed(profile)
    ua = profile.get("userAgent") or ""
    is_mobile = bool(profile.get("isMobile"))
    pool = _detect_pool(ua, is_mobile)

    screen = _pick(seed, 1, pool["screens"])
    w, h, avail_h, dpr = screen
    cores = _pick(seed, 2, pool["cores"])
    memory = _pick(seed, 3, pool["memory"])
    gpu = _pick(seed, 4, pool["gpus"])

    lang = profile.get("language") or "en-US"
    languages = [lang]
    if lang.startswith("en") and lang != "en":
        languages.append("en")
    elif "-" in lang:
        languages.append(lang.split("-")[0])

    tz = profile.get("timezone") or "America/New_York"
    webrtc = profile.get("webrtcMode") or "default"
    if webrtc not in ("default", "disable_non_proxied_udp", "disable"):
        webrtc = "default"

    ua_full = _chrome_version(ua)
    major = ua_full.split(".")[0] if ua_full else "131"
    try:
        major_int = int(major)
    except ValueError:
        major_int = 131

    ch_brands = [
        {"brand": "Google Chrome", "version": str(major_int)},
        {"brand": "Chromium", "version": str(major_int)},
        {"brand": "Not-A.Brand", "version": "99"},
    ]
    ch_full_version_list = [
        {"brand": "Google Chrome", "version": ua_full},
        {"brand": "Chromium", "version": ua_full},
        {"brand": "Not-A.Brand", "version": "99.0.0.0"},
    ]

    # Stable per-seed media device IDs — deterministic, not random on each call
    rng_dev = _rng(seed, 20)
    media_device_ids = {
        "audioinput": _hex_id(rng_dev),
        "audiooutput": _hex_id(rng_dev),
        "videoinput": _hex_id(rng_dev),
    }

    fp = {
        "seed": seed,
        "platform": pool["platform"],
        "cores": cores,
        "memory": memory,
        "screen": {"w": w, "h": h, "availH": avail_h, "dpr": dpr},
        "touch": pool["touch"],
        "gpu": {"vendor": gpu[0], "renderer": gpu[1]},
        "languages": languages,
        "language": lang,
        "timezone": tz,
        "webrtcPolicy": webrtc,
        "webrtcMode": webrtc,
        # Top-level client hint fields for CDP identity module
        "ch_platform": pool["ch_platform"],
        "ch_brands": ch_brands,
        "ch_full_version_list": ch_full_version_list,
        "architecture": pool["architecture"],
        "bitness": pool["bitness"],
        "platformVersion": pool["platformVersion"],
        "model": pool.get("model") or "",
        # Stable media device IDs per profile
        "mediaDeviceIds": media_device_ids,
        # Nested client hints (legacy field, kept for extension compatibility)
        "clientHints": {
            "platform": pool["ch_platform"],
            "mobile": pool["touch"] > 0,
            "architecture": pool["architecture"],
            "bitness": pool["bitness"],
            "platformVersion": pool["platformVersion"],
            "model": pool.get("model") or "",
            "uaFullVersion": ua_full,
            "brands": ch_brands,
        },
        "userAgent": ua,
        "isMobile": pool["touch"] > 0,
    }
    return fp


def _ua_platform_mismatch(ua: str, platform: str) -> list[str]:
    warnings: list[str] = []
    ua_l = (ua or "").lower()
    if "iphone" in ua_l or "ipad" in ua_l:
        if platform != "iPhone":
            warnings.append(f"UA suggests iPhone/iPad but platform is {platform}")
    elif "android" in ua_l:
        if "arm" not in platform.lower() and "linux" not in platform.lower():
            warnings.append(f"UA suggests Android but platform is {platform}")
    elif "macintosh" in ua_l or "mac os x" in ua_l:
        if platform != "MacIntel":
            warnings.append(f"UA suggests Mac but platform is {platform}")
    elif "windows" in ua_l or "win64" in ua_l or "win32" in ua_l:
        if platform != "Win32":
            warnings.append(f"UA suggests Windows but platform is {platform}")
    elif "linux" in ua_l and "android" not in ua_l:
        if "Linux" not in platform:
            warnings.append(f"UA suggests Linux but platform is {platform}")
    # iPhone UA with Win32 is the classic inconsistency
    if ("iphone" in ua_l or "ipad" in ua_l) and platform == "Win32":
        warnings.append("CRITICAL: iPhone/iPad UA paired with Win32 platform")
    if "android" in ua_l and platform == "Win32":
        warnings.append("CRITICAL: Android UA paired with Win32 platform")
    return warnings


def fingerprint_report(profile: dict[str, Any]) -> dict[str, Any]:
    """Human-readable fingerprint report including mismatch warnings."""
    fp = profile.get("fingerprint") if isinstance(profile.get("fingerprint"), dict) else None
    if not fp:
        fp = build_fingerprint(profile)
    warnings = _ua_platform_mismatch(fp.get("userAgent") or profile.get("userAgent") or "", fp.get("platform", ""))
    touch = fp.get("touch", 0)
    is_mobile_fp = touch > 0
    ua_mobile = bool(profile.get("isMobile")) or bool(
        re.search(r"Mobile|Android|iPhone|iPad", profile.get("userAgent") or "", re.I)
    )
    if is_mobile_fp != ua_mobile and profile.get("userAgent"):
        # soft check — UA may not have Mobile token on some desktop-emulation cases
        if is_mobile_fp and not ua_mobile:
            warnings.append("Fingerprint is mobile (touch>0) but profile isMobile/UA looks desktop")
        if not is_mobile_fp and ua_mobile:
            warnings.append("Fingerprint is desktop (touch=0) but UA looks mobile")

    screen = fp.get("screen") or {}
    summary = (
        f"{fp.get('platform')} | {screen.get('w')}x{screen.get('h')}@{screen.get('dpr')} | "
        f"{fp.get('cores')} cores / {fp.get('memory')}GB | "
        f"touch={touch} | tz={fp.get('timezone')} | webrtc={fp.get('webrtcMode') or fp.get('webrtcPolicy')}"
    )
    return {
        "ok": True,
        "seed": fp.get("seed"),
        "summary": summary,
        "fingerprint": fp,
        "warnings": warnings,
        "consistent": len(warnings) == 0,
    }
