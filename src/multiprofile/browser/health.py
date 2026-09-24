"""Profile health monitoring.

ProfileHealth aggregates process, CDP, network, storage, and extension
status into a single object per profile.  Used by the API and UI to
surface actionable health status without requiring the user to diagnose
multiple subsystems manually.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("multiprofile.browser.health")


@dataclass
class ProfileHealth:
    profile_id: str
    # Process state
    process_alive: bool = False
    pid: int | None = None
    uptime_s: float | None = None
    # CDP
    cdp_alive: bool = False
    cdp_port: int | None = None
    # Network
    network_mode: str = "DIRECT"
    proxy_ok: bool | None = None
    egress_ip: str | None = None
    last_proxy_check: float | None = None
    # Storage
    storage_accessible: bool = False
    user_data_dir: str | None = None
    # Extension
    extension_loaded: bool | None = None
    # Fingerprint
    fingerprint_loaded: bool = False
    # Timestamps
    last_launch: float | None = None
    last_error: str | None = None
    launch_state: str | None = None
    checked_at: float = field(default_factory=time.time)

    @property
    def overall_ok(self) -> bool:
        """True when the profile appears fully healthy."""
        if not self.process_alive:
            return False
        if self.proxy_ok is False:  # explicitly failed (not None = unknown)
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["overallOk"] = self.overall_ok
        d["status"] = "healthy" if self.overall_ok else (
            "proxy_failed" if self.proxy_ok is False else
            "stopped" if not self.process_alive else
            "degraded"
        )
        return d


def get_profile_health(profile_id: str) -> ProfileHealth:
    """Build a ProfileHealth snapshot for a profile."""
    from ..network import network_config_for_profile
    from ..storage.profiles import get_profile, pid_alive
    from ..storage.xdg import chrome_profile_dir
    from .cdp_window import wait_for_cdp
    from .runtime_state import get_runtime

    health = ProfileHealth(profile_id=profile_id)

    # --- Profile data ---
    profile = get_profile(profile_id)
    if not profile:
        health.last_error = "profile_not_found"
        return health

    health.fingerprint_loaded = isinstance(profile.get("fingerprint"), dict)

    # --- Network config ---
    try:
        net = network_config_for_profile(profile)
        health.network_mode = net.mode.value
    except Exception as e:
        health.last_error = f"network_config_error: {e}"

    # --- Runtime state (in-memory) ---
    rt = get_runtime(profile_id)
    if rt:
        health.launch_state = rt.launch_state.value
        health.pid = rt.pid
        health.cdp_port = rt.cdp_port
        health.proxy_ok = rt.proxy_ok
        health.egress_ip = rt.egress_ip
        health.last_proxy_check = rt.last_proxy_check
        if rt.error:
            health.last_error = rt.error

    # Fallback pid from profile dict
    if not health.pid:
        health.pid = profile.get("pid")

    # --- Process alive check ---
    if health.pid:
        health.process_alive = pid_alive(health.pid)
        if health.process_alive and rt and rt.updated_at:
            health.uptime_s = round(time.time() - rt.updated_at, 1)

    # --- CDP alive check ---
    if health.cdp_port and health.process_alive:
        try:
            wait_for_cdp(health.cdp_port, timeout_s=1.0)
            health.cdp_alive = True
        except Exception:
            health.cdp_alive = False

    # --- Storage accessible ---
    try:
        udd = chrome_profile_dir(profile_id) / "user-data"
        health.user_data_dir = str(udd)
        health.storage_accessible = udd.exists()
    except Exception:
        health.storage_accessible = False

    # Extension loaded: check per-profile extension copy exists
    try:
        ext_dir = chrome_profile_dir(profile_id) / "extension"
        health.extension_loaded = (ext_dir / "manifest.json").exists()
    except Exception:
        health.extension_loaded = None

    health.checked_at = time.time()
    return health


def get_all_health(profile_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return health snapshots for all (or specified) profiles."""
    from ..storage.profiles import load_profiles

    if profile_ids is not None:
        ids = profile_ids
    else:
        ids = [p["id"] for p in load_profiles()]

    results = []
    for pid in ids:
        try:
            h = get_profile_health(pid)
            results.append(h.to_dict())
        except Exception as e:
            results.append({
                "profileId": pid,
                "overallOk": False,
                "status": "error",
                "last_error": str(e),
            })
    return results
