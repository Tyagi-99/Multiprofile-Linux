"""Media surface: stable per-seed device IDs (existing behaviour kept).

Same values as legacy ``fingerprint.mediaDeviceIds``: three 64-char hex
IDs derived deterministically from the seed (salt 20).  The extension
(evasion.js, enumerateDevices hook) replaces device IDs with these stable
seeded values at runtime.
"""
from __future__ import annotations

from typing import Any

from ._core import _hex_id, _rng, resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    seed = ctx["seed"]
    rng_dev = _rng(seed, 20)
    return {
        "status": "STABLE",
        "seed": seed,
        "deviceIds": {
            "audioinput": _hex_id(rng_dev),
            "audiooutput": _hex_id(rng_dev),
            "videoinput": _hex_id(rng_dev),
        },
        "mechanism": "extension replaces enumerateDevices IDs with these stable seeded values",
        "note": "identical values to legacy fingerprint.mediaDeviceIds",
        "source": ctx["source"],
    }
