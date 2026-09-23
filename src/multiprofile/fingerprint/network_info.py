"""Network surface: navigator.connection from effective network type.

Honest framing: the connection values are deterministic per seed and
*coherent* with the profile, but they are not a claim about the host's
actual link — Chromium derives them from the real network, and page-level
overrides only shape what the profile consistently reports.
"""
from __future__ import annotations

from typing import Any

from ._core import _pick, resolve_context

_CONNECTION_PROFILES = [
    {"type": "wifi", "effectiveType": "4g", "rtt": 50, "downlink": 10.0, "saveData": False},
    {"type": "cellular", "effectiveType": "4g", "rtt": 70, "downlink": 9.0, "saveData": False},
    {"type": "ethernet", "effectiveType": "4g", "rtt": 30, "downlink": 12.0, "saveData": False},
]


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    connection = dict(_pick(ctx["seed"], 30, _CONNECTION_PROFILES))
    return {
        "status": "STABLE",
        "seed": ctx["seed"],
        "connection": connection,
        "detail": (
            "navigator.connection values derived deterministically from the "
            "profile seed; coherent per profile, not a claim about the host's "
            "actual network link"
        ),
        "source": ctx["source"],
    }
