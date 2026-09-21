"""Central runtime configuration for Multiprofile Native.

Single source of truth for concurrency and launch policy.
Override via environment variables — do not scatter magic numbers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Process-wide settings (immutable snapshot)."""

    # Max Chrome profiles running at once (queue holds the rest).
    max_concurrent_profiles: int = 20
    # Allow Chrome Web Store / extra extensions.
    # DEFAULT FALSE — keep AdsPower-class anti-detect exclusivity
    # (--disable-extensions-except). Opt-in later when we design a safe path.
    allow_webstore_extensions: bool = False
    # Force --no-sandbox even when probe says sandbox works (debug only).
    force_no_sandbox: bool = False
    # Prefer real sandbox when probe succeeds.
    prefer_sandbox: bool = True
    # Window tiling gap in pixels.
    window_tile_gap: int = 8
    # Enable window tiling on launch.
    window_tiling_enabled: bool = True


def get_settings() -> Settings:
    return Settings(
        max_concurrent_profiles=_env_int("MAX_CONCURRENT_PROFILES", 20, minimum=1, maximum=64),
        # Explicit opt-in only — exclusivity is the product default
        allow_webstore_extensions=_env_bool("MULTIPROFILE_ALLOW_WEBSTORE_EXTENSIONS", False),
        force_no_sandbox=_env_bool("MULTIPROFILE_FORCE_NO_SANDBOX", False),
        prefer_sandbox=_env_bool("MULTIPROFILE_PREFER_SANDBOX", True),
        window_tile_gap=_env_int("MULTIPROFILE_WINDOW_TILE_GAP", 8, minimum=0, maximum=64),
        window_tiling_enabled=_env_bool("MULTIPROFILE_WINDOW_TILING", True),
    )


def max_concurrent_profiles() -> int:
    return get_settings().max_concurrent_profiles
