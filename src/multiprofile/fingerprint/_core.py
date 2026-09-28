"""Shared deterministic internals for the fingerprint package.

This is the verbatim logic that used to live in ``fingerprint.py``:
platform pools, seed normalisation, deterministic picks, pool detection,
``build_fingerprint`` and ``_ua_platform_mismatch``.  Every component module
derives its values from the same ``resolve_context()`` source so the
per-surface outputs (navigator, screen, client hints, ...) stay coherent.

Public names that existed on the old ``fingerprint`` module are re-exported
from ``multiprofile.fingerprint`` unchanged; ``build_fingerprint`` keeps
byte-identical behaviour so the launcher, the extension config and the
existing tests see exactly the same dict as before.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from ..engine import align_ua_string, detect_engine_version


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


# Platform pools — UA must agree with platform/touch/screen.
#
# Each pool carries a default "userAgent". A profile created without one used
# to get an EMPTY UA: the launcher then passed no --user-agent, the CDP
# override had nothing to claim, and the browser went on sending the host's
# own User-Agent and sec-ch-ua-platform while the injected script told the
# page it was Windows. That contradiction is the root of F1 — the pool knew
# it was pretending to be Win32/Windows/Chrome-131 and simply never said so
# in a UA string. The defaults below are the same strings the UA picker in
# api/user_agents.py offers, and their Chrome major matches the ch_brands the
# same pool builds, so the wire and the page agree by construction.
_DESKTOP_WIN = {
    "pool_name": "win",
    "platform": "Win32",
    "userAgent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
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
    "pool_name": "mac",
    "platform": "MacIntel",
    "userAgent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
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
    "pool_name": "linux",
    "platform": "Linux x86_64",
    "userAgent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
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
    "pool_name": "android",
    "platform": "Linux armv8l",
    "userAgent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "touch": 5,
    "screens": [(412, 915, 915, 2.625), (360, 800, 800, 3), (393, 851, 851, 2.75), (412, 892, 892, 2.625)],
    "cores": [8],
    "memory": [4, 6, 8],
    "gpus": [
        ("Google Inc. (Qualcomm)", "ANGLE (Qualcomm, Adreno (TM) 740, OpenGL ES 3.2)"),
        ("Google Inc. (Qualcomm)", "ANGLE (ARM, Mali-G78, OpenGL ES 3.2)"),
        ("Google Inc. (ARM)", "ANGLE (ARM, Mali-G78, OpenGL ES 3.2)"),
    ],
    "ch_platform": "Android",
    "architecture": "",
    "bitness": "",
    "platformVersion": "13.0.0",
    "model": "Pixel 7",
}

_MOBILE_IPHONE = {
    "pool_name": "iphone",
    "platform": "iPhone",
    # CriOS, not Safari: the same pool claims Chrome brands in its client
    # hints, and a Safari UA beside Chrome hints is the contradiction this
    # whole area exists to avoid.
    "userAgent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.0.0 "
        "Mobile/15E148 Safari/604.1"
    ),
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

# Named lookup for device presets. "ios" is an alias for "iphone".
POOL_MAP = {
    "win": _DESKTOP_WIN,
    "windows": _DESKTOP_WIN,
    "mac": _DESKTOP_MAC,
    "macos": _DESKTOP_MAC,
    "linux": _DESKTOP_LINUX,
    "android": _MOBILE_ANDROID,
    "iphone": _MOBILE_IPHONE,
    "ios": _MOBILE_IPHONE,
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


# Every token a Chromium build puts its version behind. Matching only
# "Chrome/" read the iPhone pool -- whose UA says CriOS/ -- as unversioned
# and fell back to the 131 literal, so its brands disagreed with its own UA
# string the moment either one moved.
_UA_VERSION_TOKEN = re.compile(r"\b(?:Chrome|Chromium|CriOS|Edg|EdgA|EdgiOS|HeadlessChrome)/([\d.]+)")

_FALLBACK_CHROME_VERSION = "131.0.0.0"


def _chrome_version(ua: str) -> str:
    m = _UA_VERSION_TOKEN.search(ua or "")
    if m:
        return m.group(1)
    return _FALLBACK_CHROME_VERSION


def _chrome_major(ua: str) -> int:
    full = _chrome_version(ua)
    try:
        return int(full.split(".")[0])
    except (ValueError, IndexError):
        return int(_FALLBACK_CHROME_VERSION.split(".")[0])


def _languages(lang: str) -> list[str]:
    languages = [lang]
    if lang.startswith("en") and lang != "en":
        languages.append("en")
    elif "-" in lang:
        languages.append(lang.split("-")[0])
    return languages


def _apply_preset(pool: dict, preset: dict[str, Any]) -> dict:
    """Return a pool dict with device_preset fields overlaid.

    A preset is coherent because it overrides the *same* pool dict every
    surface reads from, instead of letting each surface guess separately.
    Only known pool keys are honoured; unknown keys are ignored.

    A device preset additionally pins the exact viewport: the Screen
    component must report the preset's geometry (the same values the
    launcher applies via Emulation.setDeviceMetricsOverride), not a
    generic pool pick.
    """
    merged = dict(pool)
    for key in ("platform", "touch", "screens", "cores", "memory", "gpus",
                "ch_platform", "architecture", "bitness", "platformVersion", "model"):
        if preset.get(key) is not None:
            merged[key] = preset[key]
    vp = preset.get("viewport") or {}
    if isinstance(vp.get("width"), int) and isinstance(vp.get("height"), int):
        dsf = preset.get("device_scale_factor") or 1.0
        try:
            dsf = float(dsf)
        except (TypeError, ValueError):
            dsf = 1.0
        merged["screens"] = [(vp["width"], vp["height"], vp["height"], dsf)]
    return merged


def resolve_context(
    profile: dict[str, Any],
    device_preset: dict[str, Any] | str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Resolve the ONE coherent source every component build reads from.

    Returns {"seed", "pool", "pool_name", "userAgent", "isMobile", "language",
    "languages", "timezone", "webrtcMode", "source"}.

    device_preset may be:
      - None: pool detected from profile UA / isMobile (legacy behaviour)
      - str:  a POOL_MAP key ("win", "mac", "linux", "android", "iphone")
      - dict: {"pool": <key>} for a named pool, and/or explicit pool fields
              ("platform", "touch", "screens", "cores", "memory", "ch_platform",
              "architecture", "bitness", "platformVersion", "model",
              "user_agent") overlaid on the detected pool.
    """
    seed = seed if seed is not None else normalize_seed(profile)
    preset_dict: dict[str, Any] = {}
    pool_name: str | None = None
    if isinstance(device_preset, str):
        preset_dict = {"pool": device_preset}
    elif isinstance(device_preset, dict):
        preset_dict = dict(device_preset)

    explicit_ua = preset_dict.get("user_agent") or profile.get("userAgent") or ""
    ua = explicit_ua
    is_mobile = bool(preset_dict.get("mobile", profile.get("isMobile")))

    pool = _detect_pool(ua, is_mobile)
    pool_name = pool.get("pool_name", "detected")
    source = f"pool:{pool_name}"
    if "pool" in preset_dict:
        named = POOL_MAP.get(str(preset_dict["pool"]).lower())
        if named is not None:
            pool = named
            pool_name = pool.get("pool_name", str(preset_dict["pool"]))
            source = f"device_preset:{pool_name}"
    if any(k in preset_dict for k in ("platform", "touch", "screens", "cores",
                                      "memory", "gpus", "ch_platform", "architecture",
                                      "bitness", "platformVersion", "model")):
        pool = _apply_preset(pool, preset_dict)
        source = f"device_preset:overlay({pool_name})"

    # No explicit UA anywhere: claim the pool's own. Every other surface is
    # already built from this pool, so this is the string that agrees with
    # them — and an empty UA agrees with nothing (F1).
    if not ua:
        ua = pool.get("userAgent") or ""

    lang = profile.get("language") or "en-US"
    tz = profile.get("timezone") or "America/New_York"
    # One vocabulary, shared with network.py and evasion.js. The console's
    # own words ("disabled", "proxy") used to land here as unknown and be
    # coerced to "default" -- the JS half of the same gap that left the
    # Chrome flags off.
    from ..network import normalize_webrtc_mode

    webrtc = normalize_webrtc_mode(profile.get("webrtcMode"))

    return {
        "seed": seed,
        "pool": pool,
        "pool_name": pool_name,
        "userAgent": ua,
        "isMobile": is_mobile,
        "language": lang,
        "languages": _languages(lang),
        "timezone": tz,
        "webrtcMode": webrtc,
        "source": source,
    }


def _build_brands(chrome_major: int) -> list[dict[str, str]]:
    return [
        {"brand": "Google Chrome", "version": str(chrome_major)},
        {"brand": "Chromium", "version": str(chrome_major)},
        {"brand": "Not-A.Brand", "version": "99"},
    ]


def _build_full_version_list(chrome_full: str) -> list[dict[str, str]]:
    return [
        {"brand": "Google Chrome", "version": chrome_full},
        {"brand": "Chromium", "version": chrome_full},
        {"brand": "Not-A.Brand", "version": "99.0.0.0"},
    ]


def device_preset_for_profile(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the device preset dict for ``profile["deviceId"]``, or None.

    Guarded import: returns None when the devices package is unavailable
    or the id is unknown, so callers degrade gracefully.

    "Degrade gracefully" means the profile is built *without the preset it
    asked for* -- the exact shape of the bug F8 was about, where a device
    selection was accepted and then silently never applied. A profile naming a
    deviceId and not getting one is a fault, not a detail, so each route out of
    here records one (F11). No deviceId at all is not a fault: that is a
    profile that never asked.
    """
    device_id = (profile or {}).get("deviceId")
    if not device_id:
        return None

    from ..browser.identity_faults import record

    profile_id = str((profile or {}).get("id") or "") or None
    try:
        from ..devices.registry import DeviceRegistry
        preset = DeviceRegistry().get(device_id)
    except Exception as e:  # noqa: BLE001 - recorded, not hidden
        record(
            "preset_unreadable",
            f"device preset '{device_id}' could not be read ({e}); the profile is "
            "being built without it and will not look like that device",
            where="fingerprint._core.device_preset_for_profile",
            profile_id=profile_id,
        )
        return None
    if preset is None:
        record(
            "preset_unknown",
            f"device preset '{device_id}' is not in the registry; the profile is "
            "being built without it and will not look like that device",
            where="fingerprint._core.device_preset_for_profile",
            profile_id=profile_id,
        )
        return None
    try:
        return preset.to_dict()
    except Exception as e:  # noqa: BLE001 - recorded, not hidden
        record(
            "preset_unserializable",
            f"device preset '{device_id}' could not be serialised ({e}); the "
            "profile is being built without it",
            where="fingerprint._core.device_preset_for_profile",
            profile_id=profile_id,
        )
        return None


def build_fingerprint(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic fingerprint dict from seed + UA/isMobile.

    Kept byte-identical to the legacy implementation: the launcher, the
    extension config (which reads seed/platform/screen/cores/memory/touch),
    CDP identity (_extract_ua_metadata) and the existing tests all consume
    this exact dict shape.
    """
    seed = normalize_seed(profile)
    explicit_ua = (profile.get("userAgent") or "").strip()
    ua = explicit_ua
    is_mobile = bool(profile.get("isMobile"))
    pool = _detect_pool(ua, is_mobile)
    # The pool decides what this profile pretends to be; without an explicit
    # UA it must still SAY so, or the launcher has nothing to put on the wire
    # and the profile browses as the host (F1).
    #
    # A pool default is OUR claim, so it tracks the engine that will actually
    # run it instead of the Chrome 131 these literals were written against
    # (F5). An explicit operator UA is left exactly as given: they may be
    # matching a fingerprint bought elsewhere, and silently renumbering it
    # would break the pairing they came for. `engine_version` below records
    # the disagreement instead.
    if not ua:
        ua = align_ua_string(pool.get("userAgent") or "")

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
    # One vocabulary, shared with network.py and evasion.js. The console's
    # own words ("disabled", "proxy") used to land here as unknown and be
    # coerced to "default" -- the JS half of the same gap that left the
    # Chrome flags off.
    from ..network import normalize_webrtc_mode

    webrtc = normalize_webrtc_mode(profile.get("webrtcMode"))

    # Two different claims, because real Chrome makes them differently: the
    # UA string carries the reduced `<major>.0.0.0` form, while
    # Sec-CH-UA-Full-Version-List carries the real build. Publishing the
    # reduced form in both -- what this used to do -- claims a build number
    # no Chrome release has ever had.
    engine = detect_engine_version()
    ua_full = _chrome_version(ua)
    major_int = _chrome_major(ua)
    if not explicit_ua and engine.known:
        ua_full = engine.full or ua_full
        major_int = engine.major or major_int

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

    # Stable per-seed media device IDs — deterministic, not random on each
    # call. One salt PER KIND: all three used to share _rng(seed, 20) and so
    # carried the same 64-char id, which is a microphone, a camera and a
    # speaker with one identifier between them. No real browser does that.
    #
    # evasion.js only substitutes these when the browser is already exposing
    # real ids; see the enumerateDevices hook for why handing them out
    # unconditionally was worse than not spoofing at all.
    media_device_ids = {
        "audioinput": _hex_id(_rng(seed, 20)),
        "audiooutput": _hex_id(_rng(seed, 21)),
        "videoinput": _hex_id(_rng(seed, 22)),
    }
    # Chrome groups devices belonging to one physical unit: a webcam's camera
    # and microphone share a groupId. The audio pair together, the camera
    # apart, is the ordinary shape.
    media_device_group_ids = {
        "audioinput": _hex_id(_rng(seed, 23)),
        "audiooutput": _hex_id(_rng(seed, 23)),
        "videoinput": _hex_id(_rng(seed, 24)),
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
        "mediaDeviceGroupIds": media_device_group_ids,
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
        # What the claim was measured against, so a launch note, /api/health
        # and the consistency check can all say whether the version on the
        # wire was verified or merely inherited from a literal.
        "engineVersion": engine.to_dict(),
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
