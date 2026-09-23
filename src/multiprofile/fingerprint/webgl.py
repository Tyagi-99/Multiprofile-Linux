"""WebGL surface: REAL diagnostics only.

The expected side states *policy* only — never vendor/renderer strings.
``vendor`` / ``renderer`` are populated exclusively from actual runtime
probing (a live CDP session reading WebGLRenderingContext parameters).
Fabricating them would be a lie about the user's hardware, so the expected
config deliberately contains no such strings.
"""
from __future__ import annotations

from typing import Any

from ._core import resolve_context

_EXPECTED_POLICY = (
    "use real GPU if present else SwiftShader; "
    "never pass --disable-gpu when a GPU is available"
)


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    return {
        "expected": {
            "policy": _EXPECTED_POLICY,
            "note": (
                "vendor/renderer are runtime diagnostics only; they are never "
                "set from expected config"
            ),
        },
        # Filled by the inspector from runtime_data, never invented here.
        "runtime": None,
        "status": "UNKNOWN",
        "note": "no runtime probe data — vendor/renderer unknown until a live session probes WebGL",
    }


def runtime_from_probe(result: dict[str, Any]) -> dict[str, Any]:
    """Normalise a real WebGL probe result into the runtime dict shape.

    Only call with values actually read from a live browser context
    (e.g. via CDP Runtime.evaluate reading UNMASKED_VENDOR_WEBGL /
    UNMASKED_RENDERER_WEBGL).  Never pass invented strings here.
    """
    out = {
        "status": result.get("status") or "REAL_GPU",
        "vendor": result.get("vendor"),
        "renderer": result.get("renderer"),
        "version": result.get("version"),
    }
    # Drop empty values rather than carrying placeholders.
    return {k: v for k, v in out.items() if v is not None}
