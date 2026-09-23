"""Android device presets (20). Specs from public manufacturer data sheets.

Viewport/DPR pairs are the standard CSS viewports Chrome reports for these
devices (physical / DPR rounded). hardware_concurrency/device_memory_gb are
representative class values (varies by unit/SKU).
"""
from __future__ import annotations

from .preset import (
    ANDROID,
    MOBILE,
    PARTIAL,
    SUPPORTED,
    DevicePreset,
)

_CHROME_ANDROID = "Chrome/131.0.0.0"
_CHROME_ANDROID_CRIOS_FULL = "131.0.6778.73"

_ANDROID_CAPABILITIES = {
    "engine": {
        "status": SUPPORTED,
        "note": "Chromium on Linux runs the same Blink/V8 engine line as Chrome for Android.",
    },
    "user_agent": {"status": SUPPORTED, "note": "UA string applied verbatim at launch."},
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
        "note": "WebGL works, but the renderer string comes from the host Linux GPU (or SwiftShader) — "
        "never the device's real Adreno/Mali/Apple GPU. webgl_policy is 'real': no fabricated renderer strings.",
    },
    "media": {
        "status": PARTIAL,
        "note": "Codec/container support follows the Linux Chromium build, not the device's hardware decoders "
        "(e.g. device-specific HDR/AV1 behavior differs).",
    },
    "fonts": {
        "status": PARTIAL,
        "note": "System fonts come from the Linux host; Roboto/Noto fallbacks approximate but do not match "
        "the device's OEM font stack exactly.",
    },
    "sensors": {
        "status": PARTIAL,
        "note": "Orientation/motion sensors are emulated via CDP, not real hardware sensors.",
    },
}

_ANDROID_LIMITATIONS = [
    "hardware_concurrency and device_memory_gb are representative class values; they vary by unit/SKU.",
    "WebGL renderer string reflects the Linux host GPU, not the device's mobile GPU.",
    "OEM font stack and device-specific media pipeline are not reproduced.",
]


def _ch_brands() -> list:
    return [
        {"brand": "Chromium", "version": "131"},
        {"brand": "Google Chrome", "version": "131"},
        {"brand": "Not-A.Brand", "version": "99"},
    ]


def _ch_full_version_list() -> list:
    return [
        {"brand": "Chromium", "fullVersion": _CHROME_ANDROID_CRIOS_FULL},
        {"brand": "Google Chrome", "fullVersion": _CHROME_ANDROID_CRIOS_FULL},
        {"brand": "Not-A.Brand", "fullVersion": "99.0.0.0"},
    ]


def _android(
    *,
    id: str,
    name: str,
    manufacturer: str,
    model: str,
    ua_model: str,
    android_version: str,
    vw: int,
    vh: int,
    sw: int,
    sh: int,
    dpr: float,
    cores: int,
    mem_gb: float,
) -> DevicePreset:
    return DevicePreset(
        id=id,
        name=name,
        manufacturer=manufacturer,
        model=model,
        category=MOBILE,
        platform=ANDROID,
        browser_family="Chrome",
        browser_version_policy="pinned:131",
        user_agent=(
            f"Mozilla/5.0 (Linux; Android {android_version}; {ua_model}) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) {_CHROME_ANDROID} Mobile Safari/537.36"
        ),
        client_hints={
            "brands": _ch_brands(),
            "full_version_list": _ch_full_version_list(),
            "platform": "Android",
            "platform_version": f"{android_version}.0.0",
            "architecture": "arm",
            "model": ua_model,
            "mobile": True,
        },
        viewport={"width": vw, "height": vh},
        screen={"width": sw, "height": sh},
        device_scale_factor=dpr,
        mobile=True,
        touch=True,
        max_touch_points=10,
        hardware_concurrency=cores,
        device_memory_gb=mem_gb,
        language_defaults={"language": "en-US", "accept_language": "en-US,en;q=0.9"},
        timezone_policy="auto",
        webgl_policy="real",
        media_capabilities={"video_codecs": ["h264", "vp8", "vp9"], "audio_codecs": ["aac", "opus"]},
        capability_matrix=dict(_ANDROID_CAPABILITIES),
        limitations=list(_ANDROID_LIMITATIONS),
    )


ANDROID_PRESETS = [
    # Pixel 9: 1080x2424 @3.0 -> 412x915
    _android(id="pixel-9", name="Pixel 9", manufacturer="Google", model="Pixel 9",
             ua_model="Pixel 9", android_version="14", vw=411, vh=923, sw=1080, sh=2424,
             dpr=2.626, cores=8, mem_gb=12),
    # Pixel 9 Pro: 1008x2244 @3.0 -> 336x748
    _android(id="pixel-9-pro", name="Pixel 9 Pro", manufacturer="Google", model="Pixel 9 Pro",
             ua_model="Pixel 9 Pro", android_version="14", vw=336, vh=748, sw=1008, sh=2244,
             dpr=3.0, cores=8, mem_gb=16),
    # Pixel 9 Pro XL: 1344x2992 @3.0 -> 448x997
    _android(id="pixel-9-pro-xl", name="Pixel 9 Pro XL", manufacturer="Google", model="Pixel 9 Pro XL",
             ua_model="Pixel 9 Pro XL", android_version="14", vw=448, vh=997, sw=1344, sh=2992,
             dpr=3.0, cores=8, mem_gb=16),
    # Pixel 8: 1080x2400 @2.625 -> 412x915
    _android(id="pixel-8", name="Pixel 8", manufacturer="Google", model="Pixel 8",
             ua_model="Pixel 8", android_version="14", vw=411, vh=914, sw=1080, sh=2400,
             dpr=2.626, cores=9, mem_gb=8),
    # Pixel 8 Pro: 1344x2992 @3.5 -> 384x854
    _android(id="pixel-8-pro", name="Pixel 8 Pro", manufacturer="Google", model="Pixel 8 Pro",
             ua_model="Pixel 8 Pro", android_version="14", vw=384, vh=855, sw=1344, sh=2992,
             dpr=3.5, cores=9, mem_gb=12),
    # Pixel 7: 1080x2400 @2.625 -> 412x915
    _android(id="pixel-7", name="Pixel 7", manufacturer="Google", model="Pixel 7",
             ua_model="Pixel 7", android_version="14", vw=411, vh=914, sw=1080, sh=2400,
             dpr=2.626, cores=8, mem_gb=8),
    # Pixel 7 Pro: 1440x3120 @3.5 -> 411x891
    _android(id="pixel-7-pro", name="Pixel 7 Pro", manufacturer="Google", model="Pixel 7 Pro",
             ua_model="Pixel 7 Pro", android_version="14", vw=411, vh=891, sw=1440, sh=3120,
             dpr=3.502, cores=8, mem_gb=12),
    # Galaxy S24: 1080x2340 @3.0 -> 360x780
    _android(id="galaxy-s24", name="Galaxy S24", manufacturer="Samsung", model="Galaxy S24",
             ua_model="SM-S921B", android_version="14", vw=360, vh=780, sw=1080, sh=2340,
             dpr=3.0, cores=8, mem_gb=8),
    # Galaxy S24 Ultra: 1440x3120 @3.5 -> 411x891
    _android(id="galaxy-s24-ultra", name="Galaxy S24 Ultra", manufacturer="Samsung", model="Galaxy S24 Ultra",
             ua_model="SM-S928B", android_version="14", vw=411, vh=891, sw=1440, sh=3120,
             dpr=3.502, cores=8, mem_gb=12),
    # Galaxy S23: 1080x2340 @3.0 -> 360x780
    _android(id="galaxy-s23", name="Galaxy S23", manufacturer="Samsung", model="Galaxy S23",
             ua_model="SM-S911B", android_version="13", vw=360, vh=780, sw=1080, sh=2340,
             dpr=3.0, cores=8, mem_gb=8),
    # Galaxy S23 Ultra: 1440x3088 @3.5 -> 411x882
    _android(id="galaxy-s23-ultra", name="Galaxy S23 Ultra", manufacturer="Samsung", model="Galaxy S23 Ultra",
             ua_model="SM-S918B", android_version="13", vw=411, vh=882, sw=1440, sh=3088,
             dpr=3.502, cores=8, mem_gb=12),
    # Galaxy A55: 1080x2340 @2.625 -> 412x891
    _android(id="galaxy-a55", name="Galaxy A55", manufacturer="Samsung", model="Galaxy A55",
             ua_model="SM-A556B", android_version="14", vw=411, vh=891, sw=1080, sh=2340,
             dpr=2.626, cores=8, mem_gb=8),
    # Galaxy A35: 1080x2340 @2.625 -> 412x891
    _android(id="galaxy-a35", name="Galaxy A35", manufacturer="Samsung", model="Galaxy A35",
             ua_model="SM-A356B", android_version="14", vw=411, vh=891, sw=1080, sh=2340,
             dpr=2.626, cores=8, mem_gb=6),
    # OnePlus 12: 1440x3168 @3.5 -> 411x905
    _android(id="oneplus-12", name="OnePlus 12", manufacturer="OnePlus", model="OnePlus 12",
             ua_model="CPH2581", android_version="14", vw=412, vh=906, sw=1440, sh=3168,
             dpr=3.497, cores=8, mem_gb=16),
    # OnePlus 12R: 1264x2780 @3.25 -> 389x855
    _android(id="oneplus-12r", name="OnePlus 12R", manufacturer="OnePlus", model="OnePlus 12R",
             ua_model="CPH2609", android_version="14", vw=389, vh=855, sw=1264, sh=2780,
             dpr=3.251, cores=8, mem_gb=8),
    # OnePlus 11: 1440x3216 @3.5 -> 411x919
    _android(id="oneplus-11", name="OnePlus 11", manufacturer="OnePlus", model="OnePlus 11",
             ua_model="CPH2449", android_version="13", vw=411, vh=918, sw=1440, sh=3216,
             dpr=3.503, cores=8, mem_gb=16),
    # Xiaomi 14: 1200x2670 @2.75 -> 436x971
    _android(id="xiaomi-14", name="Xiaomi 14", manufacturer="Xiaomi", model="Xiaomi 14",
             ua_model="23127PN0CG", android_version="14", vw=436, vh=971, sw=1200, sh=2670,
             dpr=2.75, cores=8, mem_gb=12),
    # Xiaomi 14 Ultra: 1440x3200 @3.5 -> 411x914
    _android(id="xiaomi-14-ultra", name="Xiaomi 14 Ultra", manufacturer="Xiaomi", model="Xiaomi 14 Ultra",
             ua_model="24030PN60G", android_version="14", vw=411, vh=914, sw=1440, sh=3200,
             dpr=3.502, cores=8, mem_gb=16),
    # Redmi Note 13 Pro: 1220x2712 @2.75 -> 444x986
    _android(id="redmi-note-13-pro", name="Redmi Note 13 Pro", manufacturer="Xiaomi", model="Redmi Note 13 Pro",
             ua_model="2312DRA50G", android_version="13", vw=444, vh=986, sw=1220, sh=2712,
             dpr=2.75, cores=8, mem_gb=8),
    # Nothing Phone (2): 1080x2412 @2.625 -> 411x919
    _android(id="nothing-phone-2", name="Nothing Phone (2)", manufacturer="Nothing", model="Nothing Phone (2)",
             ua_model="A065", android_version="13", vw=411, vh=918, sw=1080, sh=2412,
             dpr=2.627, cores=8, mem_gb=12),
]

assert len(ANDROID_PRESETS) == 20, f"expected 20 Android presets, got {len(ANDROID_PRESETS)}"
