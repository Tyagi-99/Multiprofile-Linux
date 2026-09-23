"""Battery surface: ChargingManager values, reported honestly.

The Battery Status API is deprecated/restricted in Chromium and there is
no per-profile override: whatever the host reports is surfaced as-is.  We
therefore never fabricate charging/level values in the expected config —
status is PARTIAL with the reason stated.
"""
from __future__ import annotations

from typing import Any

from ._core import resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    return {
        "status": "PARTIAL",
        "seed": ctx["seed"],
        "expected": {
            "policy": "surface host Battery Status API values as-is; no per-profile override",
        },
        "detail": (
            "The Battery Status API is deprecated/restricted in Chromium and "
            "cannot be controlled per profile; reported values reflect the "
            "host machine. No charging/level values are set from config."
        ),
        "source": ctx["source"],
    }
