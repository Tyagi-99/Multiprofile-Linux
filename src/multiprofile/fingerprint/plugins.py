"""Plugins surface: existing desktop-Chrome plugin set behaviour kept.

Desktop Chrome exposes 3 built-in PDF-viewer plugin entries; mobile
profiles expose none.  This matches the extension behaviour in
evasion.js ("NAVIGATOR.PLUGINS + MIMETYPES CONSISTENCY": plugins are only
added for desktop profiles where touch == 0).
"""
from __future__ import annotations

from typing import Any

from ._core import resolve_context

_DESKTOP_PLUGIN_SET = [
    {"name": "PDF Viewer", "filename": "internal-pdf-viewer",
     "description": "Portable Document Format",
     "mimeTypes": ["application/pdf"]},
    {"name": "Chrome PDF Viewer", "filename": "internal-pdf-viewer",
     "description": "Portable Document Format",
     "mimeTypes": ["application/x-google-chrome-pdf"]},
    {"name": "Chromium PDF Viewer", "filename": "internal-pdf-viewer",
     "description": "Portable Document Format",
     "mimeTypes": ["application/pdf"]},
]


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    desktop = ctx["pool"]["touch"] == 0
    plugins = [dict(p) for p in _DESKTOP_PLUGIN_SET] if desktop else []
    return {
        "status": "STABLE",
        "seed": ctx["seed"],
        "plugins": plugins,
        "mechanism": (
            "extension adds the 3 desktop-Chrome PDF-viewer entries for "
            "desktop profiles (touch=0); mobile profiles expose none"
        ),
        "source": ctx["source"],
    }
