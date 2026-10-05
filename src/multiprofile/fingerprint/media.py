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
    # One salt per kind. All three used to share ``_rng(seed, 20)`` and
    # therefore the same 64-char id -- a microphone, a camera and a speaker
    # with one identifier between them, which no real browser has ever
    # produced. Distinct salts, so the three differ the way real ones do.
    return {
        "status": "STABLE",
        "seed": seed,
        "deviceIds": {
            "audioinput": _hex_id(_rng(seed, 20)),
            "audiooutput": _hex_id(_rng(seed, 21)),
            "videoinput": _hex_id(_rng(seed, 22)),
        },
        # Chrome groups devices that belong to the same physical unit -- a
        # webcam's camera and microphone share a groupId. Two groups is the
        # ordinary shape: the audio pair together, the camera apart.
        "groupIds": {
            "audioinput": _hex_id(_rng(seed, 23)),
            "audiooutput": _hex_id(_rng(seed, 23)),
            "videoinput": _hex_id(_rng(seed, 24)),
        },
        "mechanism": (
            "evasion.js replaces enumerateDevices ids with these stable seeded "
            "values -- but ONLY when the browser is already exposing real ones"
        ),
        "note": (
            "Substituting unconditionally was worse than not spoofing: with no "
            "media permission Chrome returns deviceId='' for every device, so a "
            "profile handing out a stable 64-char id beside an empty label and "
            "an empty groupId announced itself as modified, and handed every "
            "site the same persistent identifier"
        ),
        "source": ctx["source"],
    }
