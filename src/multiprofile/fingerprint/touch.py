"""Touch surface: maxTouchPoints and touch-event support.

Coherent with the pool: desktop pools report 0 touch points and no touch
events; mobile pools report 5 points.  Note: CDP cannot set maxTouchPoints
directly — in a live session it comes from touch emulation
(Emulation.setTouchEmulationEnabled) plus UA/client-hint coherence, which
is what mobile.MobileProfileConfig documents.
"""
from __future__ import annotations

from typing import Any

from ._core import resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    points = ctx["pool"]["touch"]
    return {
        "status": "STABLE",
        "seed": ctx["seed"],
        "maxTouchPoints": points,
        "touchEvents": points > 0,
        "note": (
            "maxTouchPoints cannot be set directly via CDP; it is achieved "
            "through touch emulation plus UA/client-hint coherence"
        ),
        "source": ctx["source"],
    }
