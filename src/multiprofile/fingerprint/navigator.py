"""Navigator surface: userAgent, platform, language(s), hardware, touch.

All values are derived from ONE coherent source — the resolved context
(device preset if given, else the platform pool detected from the profile
UA).  ``client_hints`` and ``touch`` read the same source, so the surfaces
cannot contradict each other.
"""
from __future__ import annotations

from typing import Any

from ._core import _pick, resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    pool = ctx["pool"]
    seed = ctx["seed"]

    cores = _pick(seed, 2, pool["cores"])
    memory = _pick(seed, 3, pool["memory"])
    touch = pool["touch"]

    return {
        "userAgent": ctx["userAgent"],
        "platform": pool["platform"],
        "language": ctx["language"],
        "languages": list(ctx["languages"]),
        "hardwareConcurrency": cores,
        # iOS does not expose deviceMemory — None is honest, not a guess.
        "deviceMemory": memory,
        "maxTouchPoints": touch,
        "source": ctx["source"],
        "status": "STABLE",
    }
