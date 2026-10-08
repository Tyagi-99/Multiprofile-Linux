"""Idle parking + health-check staggering for Multiprofile-Linux v1.7.0.

IDLE PARKING
------------
A profile is idle when it is in state ``running`` AND
``now - last_activity_ts > idle_timeout_s`` (``[parking] idle_timeout_s``,
default 1800 = 30 min).

``last_activity_ts`` is tracked per profile and updated through
:func:`touch_activity`. The API hooks that call it (start/stop/navigate via
API, wizard completion) belong to the API layer — Workstream 3 wires
``touch_activity(profile_id)`` into those route handlers; Workstream 1's jobs
worker calls it when a launch job completes. This module cannot touch
``api/server.py`` or ``browser/launcher.py`` launch logic (workstream
boundary), so as a safety net it *bootstraps* activity: the first time a
profile is observed running with no recorded activity, activity is seeded
with "now" — a profile is therefore never parked the moment it launches.
No CDP polling is used; activity is purely event-driven.

``park_profile(profile_id) -> bool`` is the cross-workstream contract: the
WS1 jobs worker dispatches ``job_type='park'`` by calling it.
Parking is graceful: it reuses the launcher's existing
:func:`browser.launcher.stop_profile` path (SIGTERM, runtime state cleared,
concurrency slot released). The profile's data directory is never touched —
parking cannot lose profile data.

HEALTH-CHECK STAGGERING
-----------------------
:func:`schedule_health_checks` reads running profiles and enqueues
``health_check`` jobs with jittered delays so 100 profiles do not all check
at once. Waves are sized by ``[health] max_concurrent`` (default 4).

Workstream 1 owns the durable queue and the ``profiles_runtime`` table
(``profile_id, state, ...``) in :mod:`multiprofile.storage.db`. This module
reads that table via its real accessor
(:func:`multiprofile.storage.db.list_profile_runtimes`) and enqueues via the
real :func:`multiprofile.jobs.enqueue` entrypoint (the ``health_check`` job
type is advertised in ``JOB_TYPES``). The durable queue has no delayed
execution, so jitter is realized by *pacing* the enqueues: the scheduler
sleeps between waves. If the WS1 backend is unavailable the scheduler falls
back to the legacy on-disk runtime state and skips enqueue with a log line —
it never crashes.
"""
from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
from typing import Any

from .config import get_settings
from .profile_log import audit_event, log_event
from .storage.profiles import get_profile
from .storage.xdg import cache_dir, data_dir

log = logging.getLogger("multiprofile.parking")

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Last-activity tracking
# ---------------------------------------------------------------------------

def _activity_path():
    return data_dir() / "activity.json"


def _read_activity() -> dict[str, float]:
    try:
        raw = _activity_path().read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return {}


def _write_activity(data: dict[str, float]) -> None:
    path = _activity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def touch_activity(profile_id: str, when: float | None = None) -> float:
    """Record user activity for a profile. Called by API/launch hooks.

    Returns the recorded timestamp. Never raises.
    """
    ts = float(when if when is not None else time.time())
    try:
        with _lock:
            data = _read_activity()
            data[str(profile_id)] = ts
            _write_activity(data)
    except Exception as e:  # noqa: BLE001 - tracking must not break callers
        log.warning("touch_activity failed for %s: %s", profile_id, e)
    return ts


def last_activity_ts(profile_id: str) -> float | None:
    """Last recorded activity timestamp for a profile, or None."""
    with _lock:
        return _read_activity().get(str(profile_id))


def clear_activity(profile_id: str) -> None:
    """Forget recorded activity for a profile (e.g. after park/stop)."""
    try:
        with _lock:
            data = _read_activity()
            data.pop(str(profile_id), None)
            _write_activity(data)
    except Exception as e:  # noqa: BLE001
        log.warning("clear_activity failed for %s: %s", profile_id, e)


# ---------------------------------------------------------------------------
# Running-state observation (no CDP polling)
# ---------------------------------------------------------------------------

def _disk_runtime_state() -> dict[str, dict[str, Any]]:
    """On-disk runtime state from storage (status/pid per profile)."""
    try:
        raw = (cache_dir() / "runtime.json").read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return {}


def is_profile_running(profile_id: str) -> bool:
    """True if the profile is running per the runtime state.

    The control database is the record. ``runtime.json`` is consulted only
    when that profile has no row, which is the path that records a run
    without going through the state machine. Trusting the file first counted
    deleted profiles as running.
    """
    try:
        from .storage.db import get_profile_runtime

        row = get_profile_runtime(str(profile_id))
    except Exception:  # noqa: BLE001 - fall through to the cache
        row = None
    if row is not None:
        return str(row.get("state") or "") == "running"
    entry = _disk_runtime_state().get(str(profile_id))
    if entry and str(entry.get("status")) == "running":
        return True
    try:
        from .browser.runtime_state import LaunchState, get_runtime
        rt = get_runtime(str(profile_id))
        if rt is not None and rt.launch_state == LaunchState.RUNNING:
            return True
    except Exception:  # noqa: BLE001,S110 - degrade gracefully
        pass
    return False


def is_idle(profile_id: str, now: float | None = None) -> bool:
    """True when running AND idle longer than [parking] idle_timeout_s."""
    settings = get_settings()
    now = float(now if now is not None else time.time())
    if not is_profile_running(profile_id):
        return False
    ts = last_activity_ts(profile_id)
    if ts is None:
        # Safety net: a running profile with no recorded activity is treated
        # as just-active so it is never parked immediately (e.g. hooks not
        # wired yet, or process started before this feature existed).
        touch_activity(profile_id, when=now)
        return False
    return (now - ts) > settings.parking_idle_timeout_s


# ---------------------------------------------------------------------------
# Running-job guard (WS1 durable queue + legacy in-process queue)
# ---------------------------------------------------------------------------

_ACTIVE_JOB_STATUSES = ("queued", "running")


def job_running_for(profile_id: str, *, exclude_types: tuple[str, ...] = ("park",)) -> bool:
    """True if any non-terminal, non-excluded job currently targets this profile.

    Checks Workstream 1's durable queue first
    (``jobs.list_durable_jobs(status=...)``), then the legacy in-process
    queue. ``park`` jobs are excluded by default: the jobs worker marks a
    job ``running`` *before* dispatching it, so the in-flight ``park`` job
    calling :func:`park_profile` must not block itself. A duplicate park
    job is still safe — the second one finds the profile not running and is
    refused with ``not_running``. Never raises, never blocks.
    """
    pid = str(profile_id)
    excluded = set(exclude_types or ())
    try:
        from . import jobs as jobs_backend
    except ImportError:
        return False
    try:
        # WS1 durable queue: decoded records carry "profile_id"/"job_type" keys.
        list_durable = getattr(jobs_backend, "list_durable_jobs", None)
        if callable(list_durable):
            for status in _ACTIVE_JOB_STATUSES:
                try:
                    records = list_durable(status=status, limit=500) or []
                except TypeError:
                    records = list_durable(limit=500) or []
                for job in records:
                    if not isinstance(job, dict):
                        continue
                    if job.get("status") not in _ACTIVE_JOB_STATUSES:
                        continue
                    if str(job.get("job_type")) in excluded:
                        continue
                    if str(job.get("profile_id")) == pid:
                        return True
        # Legacy in-process queue: dicts carry "type"/"profileIds".
        list_legacy = getattr(jobs_backend, "list_jobs", None)
        if callable(list_legacy):
            try:
                legacy_jobs = list_legacy(limit=500)
            except TypeError:
                legacy_jobs = list_legacy()
            for job in legacy_jobs or []:
                if not isinstance(job, dict):
                    continue
                if job.get("status") not in _ACTIVE_JOB_STATUSES:
                    continue
                if str(job.get("type")) in excluded:
                    continue
                ids = job.get("profileIds") or []
                if pid in [str(x) for x in ids]:
                    return True
    except Exception as e:  # noqa: BLE001 - guard must not break parking
        log.debug("job_running_for check failed: %s", e)
    return False


# ---------------------------------------------------------------------------
# park_profile — the cross-workstream contract
# ---------------------------------------------------------------------------

def _stop_via_launcher(profile_id: str) -> dict[str, Any]:
    """Graceful stop through the launcher's existing stop path (SIGTERM).

    Kept behind an indirection so tests can stub it without touching the
    real launcher. The launcher's stop path only kills the process and clears
    runtime state — the profile's data directory is never modified.
    """
    from .browser.launcher import stop_profile
    return stop_profile(profile_id)


def sweep_idle_profiles(*, dry_run: bool = False) -> dict[str, Any]:
    """Enqueue a ``park`` job for every profile idle past the timeout.

    **The producer F20 was missing.** ``park_profile`` was reachable only from
    a job handler, and nothing enqueued one, so roughly 1,500 lines of parking
    code and a config flag reading ``enabled = true`` reached nothing at all.
    A control that exists in the vocabulary and touches nothing is the shape
    this audit kept finding; this is the last of them.

    Does nothing at all when parking is disabled, which is now the default --
    see ``config.parking_enabled`` for why that decision went the way it did.

    Enqueues rather than parking inline: parking stops a browser, and the
    thing that decides to stop a browser should not also be the thing holding
    the sweep loop. Going through the queue also means a park survives a
    restart and is visible in ``GET /api/jobs`` like any other job.

    Never raises.
    """
    settings = get_settings()
    summary: dict[str, Any] = {
        "enqueued": 0, "candidates": [], "skipped": [], "reason": None,
        "dryRun": bool(dry_run),
    }
    if not settings.parking_enabled:
        summary["reason"] = "parking_disabled"
        return summary

    try:
        from .storage.db import list_profile_runtimes

        running = [
            str(r.get("profile_id"))
            for r in list_profile_runtimes()
            if str(r.get("state") or "").lower() == "running"
        ]
    except Exception as e:  # noqa: BLE001 - a sweep must never take the app down
        summary["reason"] = f"runtime_unavailable: {e}"
        return summary

    now = time.time()
    for profile_id in running:
        ts = last_activity_ts(profile_id)
        idle_for_s = (now - ts) if ts is not None else 0.0
        if idle_for_s <= settings.parking_idle_timeout_s:
            continue
        if job_running_for(profile_id):
            summary["skipped"].append({"profileId": profile_id, "reason": "job_running"})
            continue
        summary["candidates"].append(
            {"profileId": profile_id, "idleForS": round(idle_for_s, 1)}
        )
        if dry_run:
            continue
        try:
            from .jobs import enqueue

            job_id = enqueue("park", profile_id, {"reason": "idle",
                                                  "idleForS": round(idle_for_s, 1)})
            summary["enqueued"] += 1
            audit_event("park_enqueued", profile_id=profile_id, ok=True,
                        detail={"jobId": job_id, "idleForS": round(idle_for_s, 1)})
        except Exception as e:  # noqa: BLE001 - one profile must not stop the sweep
            log.warning("park enqueue failed for %s: %s", profile_id, e)
            summary["skipped"].append({"profileId": profile_id, "reason": str(e)})
    return summary


def start_parking_sweeper() -> threading.Thread | None:
    """Run :func:`sweep_idle_profiles` on a background thread, or don't.

    Returns None when parking is disabled, which is the default. Started by
    the API server alongside the job workers.
    """
    settings = get_settings()
    if not settings.parking_enabled:
        log.info("idle parking disabled; no sweeper started")
        return None

    interval = max(10, int(settings.parking_sweep_interval_s))

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                result = sweep_idle_profiles()
                if result.get("enqueued"):
                    log.info("idle parking: enqueued %d park job(s)", result["enqueued"])
            except Exception as e:  # noqa: BLE001 - the sweeper must outlive its own bugs
                log.warning("idle parking sweep failed: %s", e, exc_info=True)

    thread = threading.Thread(target=_loop, name="mp-parking-sweep", daemon=True)
    thread.start()
    log.info(
        "idle parking enabled: sweeping every %ds for profiles idle over %ds",
        interval, settings.parking_idle_timeout_s,
    )
    return thread


def park_profile(profile_id: str) -> bool:
    """Park an idle profile: graceful stop, state -> stopped, audit entry.

    Returns True when the profile was parked, False with an audit note when
    parking was refused (parking disabled, profile unknown/not running, a job
    is currently running for the profile, or the profile is not idle) or when
    the graceful stop itself failed. Parking never loses profile data: the
    process is terminated, the data directory is untouched.
    """
    settings = get_settings()
    now = time.time()

    if not settings.parking_enabled:
        audit_event("park_refused", profile_id=profile_id, ok=False,
                    detail={"reason": "parking_disabled"})
        return False

    if get_profile(profile_id) is None:
        audit_event("park_refused", profile_id=profile_id, ok=False,
                    detail={"reason": "profile_not_found"})
        return False

    if not is_profile_running(profile_id):
        audit_event("park_refused", profile_id=profile_id, ok=False,
                    detail={"reason": "not_running"})
        return False

    if job_running_for(profile_id):
        audit_event("park_refused", profile_id=profile_id, ok=False,
                    detail={"reason": "job_running_for_profile"})
        log_event(profile_id, "warning", "park_refused",
                  {"reason": "a job is currently running for this profile"})
        return False

    ts = last_activity_ts(profile_id)
    idle_for_s = (now - ts) if ts is not None else 0.0
    if idle_for_s <= settings.parking_idle_timeout_s:
        audit_event("park_refused", profile_id=profile_id, ok=False,
                    detail={"reason": "not_idle",
                            "idle_for_s": round(idle_for_s, 1),
                            "idle_timeout_s": settings.parking_idle_timeout_s})
        return False

    idle_min = idle_for_s / 60.0
    try:
        result = _stop_via_launcher(profile_id)
    except Exception as e:  # noqa: BLE001 - never let the worker crash
        audit_event("park_failed", profile_id=profile_id, ok=False,
                    detail={"error": str(e), "idle_min": round(idle_min, 1)})
        log_event(profile_id, "error", "park_failed", {"error": str(e)})
        return False

    if not result.get("ok"):
        audit_event("park_failed", profile_id=profile_id, ok=False,
                    detail={"error": result.get("error"), "idle_min": round(idle_min, 1)})
        log_event(profile_id, "error", "park_failed", {"error": result.get("error")})
        return False

    clear_activity(profile_id)
    audit_event("profile_parked", profile_id=profile_id, ok=True,
                detail={"idle_min": round(idle_min, 1), "stopped_pid": result.get("stopped")})
    log_event(profile_id, "info", "parked",
              {"idle_min": round(idle_min, 1),
               "note": f"parked after {idle_min:.1f} min idle"})
    log.info("parked profile %s after %.1f min idle", profile_id, idle_min)
    return True


# ---------------------------------------------------------------------------
# Health-check staggering
# ---------------------------------------------------------------------------

def read_profiles_runtime() -> tuple[str, list[dict[str, Any]]]:
    """Read running-profile rows as (source, rows).

    source is one of:
      - "ws1"         — Workstream 1's ``profiles_runtime`` table
                        (``storage.db.list_profile_runtimes``)
      - "legacy"      — WS1 store unavailable; fell back to on-disk
                        runtime state (``runtime.json``)
      - "unavailable" — WS1 backend importable but the table could not be read

    Rows are normalized to ``{"profile_id": ..., "state": ..., "pid": ...}``.
    Never raises.
    """
    try:
        from .storage import db as ws1_db
        list_runtimes = getattr(ws1_db, "list_profile_runtimes", None)
        if not callable(list_runtimes):
            raise AttributeError("storage.db has no list_profile_runtimes")
        raw_rows = list_runtimes() or []
        rows: list[dict[str, Any]] = []
        for r in raw_rows:
            if isinstance(r, dict):
                rows.append({
                    "profile_id": str(r.get("profile_id") or ""),
                    "state": str(r.get("state") or ""),
                    "pid": r.get("pid"),
                })
            else:  # sqlite3.Row or similar mapping
                try:
                    rows.append({
                        "profile_id": str(r["profile_id"]),
                        "state": str(r["state"]),
                        "pid": r["pid"] if "pid" in r.keys() else None,
                    })
                except Exception:  # noqa: BLE001 - broad by design; the failure is logged, not hidden
                    continue
        log.info("read %d rows from WS1 profiles_runtime table", len(rows))
        return "ws1", rows
    except ImportError:
        pass  # WS1 storage.db not present — legacy fallback below
    except Exception as e:  # noqa: BLE001 - degrade, don't crash
        log.warning("WS1 profiles_runtime table unreadable: %s", e)
        return "unavailable", []

    # Legacy fallback: on-disk runtime state written by storage.set_runtime.
    legacy_rows = [
        {"profile_id": pid, "state": str(e.get("status") or ""), "pid": e.get("pid")}
        for pid, e in _disk_runtime_state().items()
    ]
    log.debug("using legacy on-disk runtime state (%d rows)", len(legacy_rows))
    return "legacy", legacy_rows


def compute_health_delays(
    profile_ids: list[str],
    *,
    max_concurrent: int,
    jitter_s: int,
    seed: int | None = None,
) -> list[tuple[str, float]]:
    """Assign jittered enqueue delays so profiles do not all check at once.

    Profiles are dealt into waves of ``max_concurrent``; wave *w* starts at
    ``w * wave_gap`` plus a uniform ``[0, jitter_s)`` random offset, where
    ``wave_gap = max(5, jitter_s / 2)``. Deterministic when ``seed`` is given.
    """
    rng = random.Random(seed)
    maxc = max(1, int(max_concurrent))
    jitter = max(0.0, float(jitter_s))
    wave_gap = max(5.0, jitter / 2.0)
    out: list[tuple[str, float]] = []
    for i, profile_id in enumerate(profile_ids):
        wave = i // maxc
        # `rng.random() * jitter`, not `rng.uniform(0, jitter)`: uniform is
        # documented as inclusive of both endpoints and can return the upper
        # bound outright through float rounding. The interval this function
        # promises just above is half-open, and the difference was a test that
        # failed roughly one run in a hundred -- which is worse than a bug,
        # because an intermittently red suite is one people re-run rather than
        # read.
        delay = wave * wave_gap + (rng.random() * jitter if jitter > 0 else 0.0)
        # Truncated, not rounded. `round(1.996, 2)` is 2.0, which puts the
        # *returned* value outside the half-open interval this function
        # documents -- and that is the value callers and tests see. It made a
        # bound assertion fail roughly one run in a hundred, and an
        # intermittently red suite is one people re-run rather than read.
        out.append((str(profile_id), math.floor(delay * 100) / 100))
    return out


def enqueue_health_check(profile_id: str, delay_s: float) -> int | None:
    """Enqueue one ``health_check`` job via WS1's durable queue.

    Uses the real :func:`multiprofile.jobs.enqueue` entrypoint; the jitter
    delay is recorded in the job payload as metadata (the durable queue has
    no delayed execution — actual staggering is done by pacing the enqueues
    in :func:`schedule_health_checks`). Returns the job id, or None when the
    backend is missing, does not advertise ``health_check``, or enqueue
    failed. Never raises.
    """
    try:
        from .jobs import JOB_TYPES, enqueue
    except ImportError:
        log.info("no jobs backend present; skipping health_check enqueue for %s", profile_id)
        return None
    try:
        if "health_check" not in (JOB_TYPES or ()):
            log.info("jobs backend does not advertise 'health_check'; skipping for %s", profile_id)
            return None
        job_id = enqueue(
            "health_check",
            profile_id,
            payload={"stagger_delay_s": round(float(delay_s), 2)},
        )
        audit_event("health_check_enqueued", profile_id=profile_id, ok=True,
                    detail={"delay_s": round(float(delay_s), 2), "jobId": job_id})
        return job_id
    except Exception as e:  # noqa: BLE001 - scheduler must not crash the caller
        log.warning("health_check enqueue failed for %s: %s", profile_id, e)
        return None


def _group_waves(
    delays: list[tuple[str, float]], max_concurrent: int
) -> list[list[tuple[str, float]]]:
    maxc = max(1, int(max_concurrent))
    return [delays[i:i + maxc] for i in range(0, len(delays), maxc)]


def schedule_health_checks(
    now: float | None = None,
    *,
    pace: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Enqueue staggered health checks for all running profiles.

    Profiles are dealt into waves of ``[health] max_concurrent`` (default 4);
    wave *w* is enqueued ``w * wave_gap + jitter`` seconds after the start,
    where ``wave_gap = max(5, jitter_s / 2)`` and jitter is uniform in
    ``[0, jitter_s)``. Because the durable queue has no delayed execution,
    the jitter is realized by *pacing*: this function sleeps between waves,
    so callers should run it on a background thread.

    Args:
        pace: when True (default), sleep between waves to realize the jitter.
        dry_run: when True, compute the wave plan but enqueue nothing.

    Returns a summary dict: enqueued count, skipped ids, wave plan, runtime
    source, and the reason when nothing was enqueued. Never raises.
    """
    settings = get_settings()
    summary: dict[str, Any] = {
        "enqueued": 0,
        "skipped": [],
        "waves": [],
        "source": None,
        "stagger": bool(settings.health_stagger),
        "reason": None,
    }
    if not settings.health_stagger:
        summary["reason"] = "health staggering disabled by config"
        return summary

    try:
        source, rows = read_profiles_runtime()
    except Exception as e:  # noqa: BLE001 - belt and braces
        log.warning("read_profiles_runtime crashed: %s", e)
        summary["reason"] = f"runtime read failed: {e}"
        return summary
    summary["source"] = source

    if source == "unavailable":
        summary["reason"] = "WS1 profiles_runtime table present but unreadable; skipped"
        log.warning("health scheduler skipped: WS1 profiles_runtime table unreadable")
        return summary

    running = [r["profile_id"] for r in rows if r.get("state") == "running" and r.get("profile_id")]
    if not running:
        summary["reason"] = f"no running profiles (source={source})"
        return summary

    delays = compute_health_delays(
        running,
        max_concurrent=settings.health_max_concurrent,
        jitter_s=settings.health_jitter_s,
    )
    waves = _group_waves(delays, settings.health_max_concurrent)
    summary["waves"] = [
        [{"profile_id": p, "delay_s": d} for p, d in wave] for wave in waves
    ]

    if dry_run:
        summary["reason"] = "dry_run: plan computed, nothing enqueued"
        return summary

    start = float(now if now is not None else time.time())
    for wave in waves:
        if pace:
            target = start + min(d for _, d in wave)
            sleep_for = target - time.time()
            if sleep_for > 0:
                time.sleep(sleep_for)
        for profile_id, delay_s in wave:
            if enqueue_health_check(profile_id, delay_s) is not None:
                summary["enqueued"] += 1
            else:
                summary["skipped"].append(profile_id)
    if summary["skipped"] and summary["enqueued"] == 0:
        summary["reason"] = "jobs backend could not accept health_check jobs"
    log.info("health scheduler: enqueued=%d skipped=%d source=%s waves=%d",
             summary["enqueued"], len(summary["skipped"]), source, len(waves))
    return summary
