"""Per-profile process metrics sampled from /proc — never fabricated.

Honest behavior contract:
- If the PID is dead or /proc is unreadable, the result is
  ``{"status": "UNAVAILABLE", "reason": "..."}``. We NEVER return 0 as if it
  were a measured value and NEVER invent numbers.
- Every successful reading is labeled a point-in-time sample
  (``"sample": "point-in-time"``) with ``measured_at``.
- The only cache is a short-lived (<= 5s) holder for the previous CPU tick
  counter, needed to compute an interval CPU%. Nothing is cached longer
  than ``[metrics] cache_ttl_s``.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from .config import get_settings

log = logging.getLogger("multiprofile.metrics")

_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

# pid -> {"cpu_ticks": int, "ts": float}; guarded by _lock; TTL <= 5s.
_last_cpu: dict[int, dict[str, float]] = {}
_lock = threading.Lock()


def _btime() -> float | None:
    """System boot time (epoch seconds) from /proc/stat."""
    try:
        with open("/proc/stat", encoding="utf-8") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return None
    return None


def _parse_stat(pid: int) -> dict[str, int] | None:
    """Parse /proc/<pid>/stat. Returns utime/stime/starttime in ticks, or None."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            text = f.read()
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return None
    try:
        # comm (field 2) may contain spaces/parens: split on the LAST ")".
        rest = text.rsplit(")", 1)[1].split()
        # after comm: state=0, ppid=1, ... utime=11, stime=12, starttime=19 (0-based)
        utime = int(rest[11])
        stime = int(rest[12])
        starttime = int(rest[19])
        return {"utime": utime, "stime": stime, "starttime": starttime}
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return None


def _read_rss_kb(pid: int) -> int | None:
    """VmRSS in KiB from /proc/<pid>/status, or None if unreadable."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return None
    return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, we just can't signal it
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return False
    return True


def _cache_ttl() -> float:
    # Hard cap: never cache longer than 5 seconds, whatever config says.
    return max(0.5, min(float(get_settings().metrics_cache_ttl_s), 5.0))


def sample_pid(pid: int) -> dict[str, Any]:
    """Sample CPU% and RSS for a live PID.

    Returns a point-in-time sample dict, or
    ``{"status": "UNAVAILABLE", "reason": ...}`` when the PID cannot be
    honestly measured. Never raises.
    """
    if not isinstance(pid, int) or pid <= 0:
        return {"status": "UNAVAILABLE", "reason": f"invalid pid: {pid!r}"}
    if not _pid_alive(pid):
        return {"status": "UNAVAILABLE", "reason": f"pid {pid} is not running"}

    stat = _parse_stat(pid)
    if stat is None:
        return {"status": "UNAVAILABLE", "reason": f"/proc/{pid}/stat unreadable"}
    rss_kb = _read_rss_kb(pid)
    if rss_kb is None:
        return {"status": "UNAVAILABLE", "reason": f"/proc/{pid}/status unreadable"}

    now = time.time()
    total_ticks = stat["utime"] + stat["stime"]
    ttl = _cache_ttl()

    with _lock:
        prev = _last_cpu.get(pid)
        if prev and 0 < now - prev["ts"] <= ttl:
            dt = now - prev["ts"]
            dticks = max(0.0, total_ticks - prev["cpu_ticks"])
            cpu_percent = 100.0 * (dticks / _CLK_TCK) / dt
            method = "interval"
        else:
            cpu_percent = None
            method = "lifetime_average"
        _last_cpu[pid] = {"cpu_ticks": float(total_ticks), "ts": now}
        # Opportunistic prune of stale/foreign entries.
        for p in [p for p, v in _last_cpu.items() if now - v["ts"] > ttl * 2]:
            _last_cpu.pop(p, None)

    if cpu_percent is None:
        # First sample for this PID: honest lifetime average as the only
        # quantity computable from a single reading; labeled as such.
        boot = _btime()
        start_epoch = (boot + stat["starttime"] / _CLK_TCK) if boot else None
        uptime_s = max(0.0, now - start_epoch) if start_epoch else None
        cpu_percent = (
            100.0 * (total_ticks / _CLK_TCK) / uptime_s if uptime_s and uptime_s > 0 else None
        )
    else:
        boot = _btime()
        start_epoch = (boot + stat["starttime"] / _CLK_TCK) if boot else None
        uptime_s = max(0.0, now - start_epoch) if start_epoch else None

    result: dict[str, Any] = {
        "status": "OK",
        "pid": pid,
        "cpu_percent": round(cpu_percent, 2) if cpu_percent is not None else None,
        "cpu_method": method,
        "memory_rss_kb": rss_kb,
        "memory_rss_mb": round(rss_kb / 1024.0, 2),
        "uptime_s": round(uptime_s, 1) if uptime_s is not None else None,
        "measured_at": now,
        "sample": "point-in-time",
    }
    if result["cpu_percent"] is None:
        result["status"] = "UNAVAILABLE"
        result["reason"] = "cpu percent could not be computed from a single sample"
    return result


def _profile_pid(profile_id: str) -> tuple[str, int | None]:
    """Return (state, pid) for a profile from the on-disk runtime state.

    Imported lazily so this module stays import-safe in test contexts.
    """
    from .storage.xdg import cache_dir

    state = "unknown"
    pid: int | None = None
    try:
        raw = (cache_dir() / "runtime.json").read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        entry = data.get(profile_id) or {}
        state = str(entry.get("status") or "unknown")
        raw_pid = entry.get("pid")
        pid = int(raw_pid) if raw_pid else None
    except Exception as e:  # noqa: BLE001 - degrade, don't crash
        log.debug("runtime state unreadable: %s", e)
    return state, pid


def measure_profile(profile_id: str) -> dict[str, Any]:
    """Full per-profile metrics payload for the API route.

    Never fabricates: a profile with no live PID yields UNAVAILABLE with the
    reason, plus its last known state.
    """
    state, pid = _profile_pid(profile_id)
    measured_at = time.time()
    if pid is None:
        return {
            "profileId": profile_id,
            "state": state,
            "status": "UNAVAILABLE",
            "reason": "no live pid recorded for this profile",
            "cpu_percent": None,
            "memory_rss_mb": None,
            "uptime_s": None,
            "measured_at": measured_at,
            "sample": "point-in-time",
        }
    sample = sample_pid(pid)
    return {
        "profileId": profile_id,
        "state": state,
        **sample,
        "measured_at": measured_at,
    }


def running_profile_ids() -> list[str]:
    """Profile ids whose on-disk runtime state is 'running'."""
    from .storage.xdg import cache_dir

    try:
        raw = (cache_dir() / "runtime.json").read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return []
    return [pid for pid, e in data.items() if isinstance(e, dict) and e.get("status") == "running"]


def summary_totals() -> dict[str, Any]:
    """Aggregate metrics across running profiles.

    Per-profile entries are real samples or UNAVAILABLE — never 0-as-measured.
    """
    measured_at = time.time()
    ids = running_profile_ids()
    profiles: list[dict[str, Any]] = []
    total_rss_mb = 0.0
    cpu_values: list[float] = []
    unavailable = 0
    for profile_id in ids:
        m = measure_profile(profile_id)
        profiles.append(m)
        if m.get("status") == "UNAVAILABLE":
            unavailable += 1
            continue
        if isinstance(m.get("memory_rss_mb"), (int, float)):
            total_rss_mb += m["memory_rss_mb"]
        if isinstance(m.get("cpu_percent"), (int, float)):
            cpu_values.append(m["cpu_percent"])
    return {
        "running_profiles": len(ids),
        "measured": len(ids) - unavailable,
        "unavailable": unavailable,
        "total_memory_rss_mb": round(total_rss_mb, 2),
        "avg_cpu_percent": round(sum(cpu_values) / len(cpu_values), 2) if cpu_values else None,
        "profiles": profiles,
        "measured_at": measured_at,
        "sample": "point-in-time",
    }
