"""Central runtime configuration for Multiprofile Native.

Single source of truth for concurrency and launch policy.
Override via environment variables — do not scatter magic numbers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 10_000) -> int:
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

    max_concurrent_profiles: int = 20
    # DEFAULT FALSE — AdsPower-class anti-detect exclusivity.
    allow_webstore_extensions: bool = False
    force_no_sandbox: bool = False
    prefer_sandbox: bool = True
    window_tile_gap: int = 6
    window_tile_margin: int = 0
    window_tile_tolerance_px: int = 4
    window_tile_max_retries: int = 3
    window_tile_settle_ms: int = 200
    window_tiling_enabled: bool = True


def get_settings() -> Settings:
    return Settings(
        max_concurrent_profiles=_env_int("MAX_CONCURRENT_PROFILES", 20, minimum=1, maximum=64),
        allow_webstore_extensions=_env_bool("MULTIPROFILE_ALLOW_WEBSTORE_EXTENSIONS", False),
        force_no_sandbox=_env_bool("MULTIPROFILE_FORCE_NO_SANDBOX", False),
        prefer_sandbox=_env_bool("MULTIPROFILE_PREFER_SANDBOX", True),
        window_tile_gap=_env_int("MULTIPROFILE_WINDOW_TILE_GAP", 6, minimum=0, maximum=64),
        window_tile_margin=_env_int("MULTIPROFILE_WINDOW_TILE_MARGIN", 0, minimum=0, maximum=64),
        window_tile_tolerance_px=_env_int("MULTIPROFILE_WINDOW_TILE_TOLERANCE_PX", 4, minimum=0, maximum=32),
        window_tile_max_retries=_env_int("MULTIPROFILE_WINDOW_TILE_MAX_RETRIES", 3, minimum=1, maximum=10),
        window_tile_settle_ms=_env_int("MULTIPROFILE_WINDOW_TILE_SETTLE_MS", 200, minimum=0, maximum=5000),
        window_tiling_enabled=_env_bool("MULTIPROFILE_WINDOW_TILING", True),
    )


def max_concurrent_profiles() -> int:
    return get_settings().max_concurrent_profiles
