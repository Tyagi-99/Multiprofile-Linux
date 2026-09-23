"""Canvas surface: per-profile stable canvas identity.

Mechanism (kept from the existing extension): evasion.js injects
deterministic pixel noise keyed by PROFILE_SEED — the launcher replaces
``__PROFILE_SEED__`` with the profile seed, and the noise is added to a
copy at read time (getImageData / toDataURL / toBlob), so the page's own
canvas is never changed.  Same profile -> same drawing -> same read,
every restart.  Different seeds -> different noise.

Honest framing: this is per-profile rendering *stability*, not a hardware
claim and not "undetectable".
"""
from __future__ import annotations

from typing import Any

from ._core import _hex_id, _rng, resolve_context


def build(seed: int | None, profile: dict[str, Any], device_preset: Any = None) -> dict[str, Any]:
    ctx = resolve_context(profile, device_preset, seed)
    seed = ctx["seed"]
    # Dedicated salt so the noise key is independent of other surfaces.
    noise_salt = _hex_id(_rng(seed, 40))

    return {
        "mode": "per-profile-stable",
        "seed": seed,
        "noiseSalt": noise_salt,
        "mechanism": (
            "extension evasion.js injects deterministic pixel noise keyed by "
            "PROFILE_SEED at read time (getImageData/toDataURL/toBlob); the "
            "noise is applied to a copy, the page canvas is untouched"
        ),
        "note": "stable per-profile rendering noise via extension; not a hardware claim",
        "source": ctx["source"],
        "status": "STABLE",
    }
