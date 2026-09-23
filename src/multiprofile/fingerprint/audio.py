"""Audio surface: honest PARTIAL status.

On Linux Chromium, AudioContext output (sampleRate, channel data) is
largely determined by the OS/driver stack, not by page-level JS — there is
no reliable per-profile override.  We report PARTIAL rather than invent a
per-profile "fingerprint" the browser cannot actually honour.
"""
from __future__ import annotations

from typing import Any

from ._core import resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    return {
        "status": "PARTIAL",
        "seed": ctx["seed"],
        "detail": (
            "Linux Chromium AudioContext rendering is largely driver-determined; "
            "no reliable per-profile override exists. Sample-rate and channel "
            "properties reflect the host stack, stable per machine but not "
            "per profile."
        ),
        "controllable": [],
        "source": ctx["source"],
    }
