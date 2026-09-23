"""Client Hints surface: JS (navigator.userAgentData) + HTTP (Sec-CH-UA-*).

Must not contradict ``navigator``: the same pool supplies platform,
mobile flag and the Chrome version parsed from the same UA string.  Both
the CDP ``userAgentMetadata`` shape (used by cdp_identity) and the HTTP
header rendering are emitted from the one source.
"""
from __future__ import annotations

from typing import Any

from ._core import _build_brands, _build_full_version_list, _chrome_major, _chrome_version, resolve_context


def _quote(value: str) -> str:
    return f'"{value}"'


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    pool = ctx["pool"]
    ua = ctx["userAgent"]
    mobile = pool["touch"] > 0

    chrome_full = _chrome_version(ua)
    chrome_major = _chrome_major(ua)
    brands = _build_brands(chrome_major)
    full_version_list = _build_full_version_list(chrome_full)

    ch_platform = pool["ch_platform"]
    platform_version = pool["platformVersion"]
    architecture = pool["architecture"]
    bitness = pool["bitness"]
    model = pool.get("model") or ""

    sec_ch_ua = ", ".join(f'{_quote(b["brand"])};v={_quote(b["version"])}' for b in brands)

    metadata = {
        "brands": brands,
        "fullVersionList": full_version_list,
        "platform": ch_platform,
        "platformVersion": platform_version,
        "architecture": architecture,
        "model": model,
        "mobile": mobile,
        "bitness": bitness,
    }

    headers = {
        "Sec-CH-UA": sec_ch_ua,
        "Sec-CH-UA-Mobile": "?1" if mobile else "?0",
        "Sec-CH-UA-Platform": _quote(ch_platform),
        "Sec-CH-UA-Platform-Version": _quote(platform_version),
    }
    if architecture:
        headers["Sec-CH-UA-Arch"] = _quote(architecture)
    if bitness:
        headers["Sec-CH-UA-Bitness"] = _quote(bitness)
    if model:
        headers["Sec-CH-UA-Model"] = _quote(model)

    return {
        "brands": brands,
        "fullVersionList": full_version_list,
        "platform": ch_platform,
        "platformVersion": platform_version,
        "architecture": architecture,
        "model": model,
        "mobile": mobile,
        "bitness": bitness,
        "userAgentMetadata": metadata,
        "secChUaHeaders": headers,
        "source": ctx["source"],
        "status": "STABLE",
    }
