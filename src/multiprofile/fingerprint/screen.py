"""Screen surface: CSS size, available size, depths, pixel ratio, orientation.

Derived from the same pool pick as ``build_fingerprint`` (seed salt 1), so
a rebuilt profile always gets the same screen it had before.
"""
from __future__ import annotations

from typing import Any

from ._core import _pick, resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    pool = ctx["pool"]

    w, h, avail_h, dpr = _pick(ctx["seed"], 1, pool["screens"])
    orientation = "portrait-primary" if h >= w else "landscape-primary"

    return {
        "width": w,
        "height": h,
        "availWidth": w,
        "availHeight": avail_h,
        "colorDepth": 24,
        "pixelDepth": 24,
        "devicePixelRatio": dpr,
        "orientation": {"angle": 0, "type": orientation},
        "source": ctx["source"],
        "status": "STABLE",
    }
