"""Fonts surface: enumeration is system-determined.

Chromium exposes the host's installed fonts; there is no per-profile
override mechanism on Linux.  The expected value is therefore "whatever
the host reports" — stable across restarts on the same machine (STABLE),
but not something we set per profile.
"""
from __future__ import annotations

from typing import Any

from ._core import resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    return {
        "status": "STABLE",
        "seed": ctx["seed"],
        "expected": {
            "policy": "surface host font enumeration as-is; no per-profile override",
        },
        "detail": (
            "Font enumeration is system-determined on Linux Chromium; the set "
            "is stable across restarts on the same machine but cannot be "
            "customised per profile."
        ),
        "source": ctx["source"],
    }
