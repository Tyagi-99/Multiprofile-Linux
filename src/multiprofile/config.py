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
    # How many profiles may be *stored*. Distinct from the concurrency
    # limit above: the fleet is the address book, concurrency is how many
    # of them are running. 150 is the size this product is built and
    # measured for; raise MAX_PROFILES to grow it.
    max_profiles: int = 150
    # How long a trashed profile is kept before it is purged for good.
    # 0 disables the automatic purge, so the trash only empties when the
    # operator says so.
    trash_retention_days: int = 30
    # DEFAULT FALSE — AdsPower-class anti-detect exclusivity.
    allow_webstore_extensions: bool = False
    force_no_sandbox: bool = False
    prefer_sandbox: bool = True
    window_tile_gap: int = 0
    window_tile_margin: int = 0
    window_tile_tolerance_px: int = 4
    window_tile_max_retries: int = 3
    window_tile_settle_ms: int = 200
    window_tiling_enabled: bool = True
    # One window per profile: close session-restore leftovers that belong to
    # the profile's own user-data-dir before tiling. Set False to leave every
    # window open and accept that only one of them is positioned.
    window_reap_extras: bool = True
    # Network isolation settings
    proxy_check_timeout: int = 8           # seconds for pre-launch proxy probe
    proxy_check_enabled: bool = True       # set False to skip in offline/dev environments
    # GPU settings: "auto" = probe at launch, "angle" = force angle, "software" = force swiftshader
    gpu_mode: str = "auto"
    # Launch-time wire-identity assertion. The launcher makes the browser
    # fetch a loopback probe and compares the headers it really sent against
    # the identity it was given; a PROVEN mismatch fails the launch. Default
    # ON: shipping a profile whose headers contradict its JavaScript is worse
    # than shipping no profile, and this bug (F1) was silent for months
    # precisely because nothing checked. Set False only to diagnose the
    # assertion itself.
    identity_assert_enabled: bool = True
    # Rewrite client hints on a new tab's FIRST document request, which the
    # browser process forms before any renderer exists (F1's residual). Costs
    # ~0.5s per launch, measured: arming Fetch at browser scope drops a
    # Chromium navigation fast-path, and the cost is the same whether or not a
    # request is actually paused. On by default -- an identity that
    # contradicts itself once per tab is the defect this product exists to
    # avoid -- but switchable for anyone who needs the launch budget more.
    first_request_hints: bool = True
    # evasion.js diagnostics. OFF by default: the script used to log
    # "[Evasion] evasion.js active on: <url>" on every page and in every frame,
    # which is a plain-text confession in any DevTools session, screen share or
    # support screenshot. Turning it on is how you count the script's
    # executions per document — the measurement that catches double injection.
    evasion_debug: bool = False
    # Supervisor: watches running browsers, bounded-restarts dead ones
    supervisor_enabled: bool = True
    supervisor_interval_s: int = 10
    supervisor_max_restart_attempts: int = 3
    # A relaunched browser must stay alive this long before its restart counts
    # as a recovery. Deaths inside the window continue the same crash episode
    # and keep spending the same restart budget.
    supervisor_restart_stability_s: int = 60
    # Durable job queue
    jobs_max_workers: int = 4
    jobs_max_concurrent_launches: int = 2
    # Idle parking ([parking])
    parking_enabled: bool = True           # park idle profiles (graceful stop, data kept)
    parking_idle_timeout_s: int = 1800     # idle seconds before a profile may be parked
    # Health-check staggering ([health])
    health_stagger: bool = True            # jitter health checks so N profiles don't fire at once
    health_max_concurrent: int = 4         # max health checks in flight per wave
    health_jitter_s: int = 30              # max random delay (s) added per profile
    # Metrics sampling ([metrics])
    metrics_cache_ttl_s: int = 5           # point-in-time sample cache, never longer than 5s
    # Linux network namespaces ([netns]) — EXPERIMENTAL, default OFF.
    # Must be explicitly enabled AND gateway.netns.can_use_netns() must pass
    # before any namespace is created. See src/multiprofile/gateway/netns.py
    # and docs/NETNS_ROADMAP.md. NOT consumed by the launcher (no wiring).
    netns_enabled: bool = False


def get_settings() -> Settings:
    return Settings(
        max_concurrent_profiles=_env_int("MAX_CONCURRENT_PROFILES", 20, minimum=1, maximum=64),
        max_profiles=_env_int("MAX_PROFILES", 150, minimum=1, maximum=10_000),
        trash_retention_days=_env_int(
            "MULTIPROFILE_TRASH_RETENTION_DAYS", 30, minimum=0, maximum=3650
        ),
        allow_webstore_extensions=_env_bool("MULTIPROFILE_ALLOW_WEBSTORE_EXTENSIONS", False),
        force_no_sandbox=_env_bool("MULTIPROFILE_FORCE_NO_SANDBOX", False),
        prefer_sandbox=_env_bool("MULTIPROFILE_PREFER_SANDBOX", True),
        window_tile_gap=_env_int("MULTIPROFILE_WINDOW_TILE_GAP", 0, minimum=0, maximum=64),
        window_tile_margin=_env_int("MULTIPROFILE_WINDOW_TILE_MARGIN", 0, minimum=0, maximum=64),
        window_tile_tolerance_px=_env_int("MULTIPROFILE_WINDOW_TILE_TOLERANCE_PX", 4, minimum=0, maximum=32),
        window_tile_max_retries=_env_int("MULTIPROFILE_WINDOW_TILE_MAX_RETRIES", 3, minimum=1, maximum=10),
        window_tile_settle_ms=_env_int("MULTIPROFILE_WINDOW_TILE_SETTLE_MS", 200, minimum=0, maximum=5000),
        window_tiling_enabled=_env_bool("MULTIPROFILE_WINDOW_TILING", True),
        window_reap_extras=_env_bool("MULTIPROFILE_WINDOW_REAP_EXTRAS", True),
        proxy_check_timeout=_env_int("MULTIPROFILE_PROXY_CHECK_TIMEOUT", 8, minimum=2, maximum=60),
        proxy_check_enabled=_env_bool("MULTIPROFILE_PROXY_CHECK_ENABLED", True),
        gpu_mode=(os.environ.get("MULTIPROFILE_GPU_MODE") or "auto").strip().lower(),
        identity_assert_enabled=_env_bool("MULTIPROFILE_IDENTITY_ASSERT", True),
        first_request_hints=_env_bool("MULTIPROFILE_FIRST_REQUEST_HINTS", True),
        evasion_debug=_env_bool("MULTIPROFILE_EVASION_DEBUG", False),
        # [supervisor]
        supervisor_enabled=_env_bool("MULTIPROFILE_SUPERVISOR_ENABLED", True),
        supervisor_interval_s=_env_int("MULTIPROFILE_SUPERVISOR_INTERVAL_S", 10, minimum=2, maximum=3600),
        supervisor_max_restart_attempts=_env_int(
            "MULTIPROFILE_SUPERVISOR_MAX_RESTARTS", 3, minimum=0, maximum=10
        ),
        supervisor_restart_stability_s=_env_int(
            "MULTIPROFILE_SUPERVISOR_STABILITY_S", 60, minimum=0, maximum=3600
        ),
        # [jobs]
        jobs_max_workers=_env_int("MULTIPROFILE_JOBS_MAX_WORKERS", 4, minimum=1, maximum=32),
        jobs_max_concurrent_launches=_env_int(
            "MULTIPROFILE_JOBS_MAX_LAUNCHES", 2, minimum=1, maximum=16
        ),
        # [parking]
        parking_enabled=_env_bool("MULTIPROFILE_PARKING_ENABLED", True),
        parking_idle_timeout_s=_env_int("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", 1800, minimum=1, maximum=86400),
        # [health]
        health_stagger=_env_bool("MULTIPROFILE_HEALTH_STAGGER", True),
        health_max_concurrent=_env_int("MULTIPROFILE_HEALTH_MAX_CONCURRENT", 4, minimum=1, maximum=64),
        health_jitter_s=_env_int("MULTIPROFILE_HEALTH_JITTER_S", 30, minimum=0, maximum=3600),
        # [metrics]
        metrics_cache_ttl_s=_env_int("MULTIPROFILE_METRICS_CACHE_TTL_S", 5, minimum=1, maximum=5),
        # [netns] — EXPERIMENTAL, default OFF (see gateway.netns)
        netns_enabled=_env_bool("MULTIPROFILE_NETNS_ENABLED", False),
    )


def max_concurrent_profiles() -> int:
    return get_settings().max_concurrent_profiles


def max_profiles() -> int:
    return get_settings().max_profiles


def trash_retention_days() -> int:
    return get_settings().trash_retention_days
