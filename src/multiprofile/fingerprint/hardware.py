"""Hardware surface: deviceMemory, hardwareConcurrency, platform.

Derived from the same pool as navigator (identical picks: cores salt 2,
memory salt 3), so the two surfaces can never disagree.
"""
from __future__ import annotations

from typing import Any

from ._core import _pick, resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    pool = ctx["pool"]
    seed = ctx["seed"]
    memory = _pick(seed, 3, pool["memory"])
    note = None
    if memory is None:
        note = "iOS does not expose deviceMemory"
    return {
        "status": "STABLE",
        "seed": seed,
        "hardwareConcurrency": _pick(seed, 2, pool["cores"]),
        "deviceMemory": memory,
        "platform": pool["platform"],
        "note": note,
        "source": ctx["source"],
    }
