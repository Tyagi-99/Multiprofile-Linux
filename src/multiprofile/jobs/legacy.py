"""In-process job queue for profile launch/stop with concurrency gate."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Any

from ..audit import audit_log
from ..browser import manager as browser_manager
from ..browser.grid import slot_rect_map
from ..browser.layout_manager import get_layout_manager
from ..browser.work_area import WorkAreaError
from ..config import get_settings
from ..storage.profiles import set_runtime

log = logging.getLogger("multiprofile.jobs")

JOB_TYPES = ("launch_profile", "stop_profile", "bulk_launch", "bulk_stop")

_lock = threading.RLock()
_jobs: dict[str, dict[str, Any]] = {}
_queue: deque[str] = deque()
_recent: deque[str] = deque(maxlen=200)
_worker_started = False
_cancel: set[str] = set()


def _now() -> float:
    return time.time()


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        ids = list(_recent)[-limit:]
        return [dict(_jobs[i]) for i in reversed(ids) if i in _jobs]


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def cancel_job(job_id: str) -> dict[str, Any]:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return {"ok": False, "error": "not found"}
        if j["status"] in ("completed", "failed", "cancelled"):
            return {"ok": True, "job": dict(j), "note": "already finished"}
        _cancel.add(job_id)
        if j["status"] == "queued":
            j["status"] = "cancelled"
            j["finishedAt"] = _now()
        return {"ok": True, "job": dict(j)}


def create_job(
    job_type: str,
    profile_ids: list[str],
    *,
    url: str | None = None,
    direct: bool = False,
    cdp: bool = False,
    tile_required: bool = False,
) -> dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise ValueError(f"unsupported job type: {job_type}")
    ids = [str(x) for x in (profile_ids or []) if x]
    if not ids:
        raise ValueError("profileIds required")

    job_id = "job_" + uuid.uuid4().hex[:10]
    job: dict[str, Any] = {
        "id": job_id,
        "type": job_type,
        "profileIds": ids,
        "url": url,
        "direct": bool(direct),
        "cdp": bool(cdp),
        "tileRequired": bool(tile_required),
        "status": "queued",
        "progress": 0,
        "total": len(ids),
        "results": [],
        "tile": None,
        "error": None,
        "createdAt": _now(),
        "startedAt": None,
        "finishedAt": None,
    }
    with _lock:
        _jobs[job_id] = job
        _queue.append(job_id)
        _recent.append(job_id)
        _ensure_worker()
    audit_log(
        "job_create",
        profile_id=ids[0],
        ok=True,
        detail={"jobId": job_id, "type": job_type, "count": len(ids)},
    )
    return dict(job)


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker_loop, name="mp-jobs", daemon=True).start()


def _is_cancelled(job_id: str) -> bool:
    with _lock:
        return job_id in _cancel or _jobs.get(job_id, {}).get("status") == "cancelled"


def _worker_loop() -> None:
    while True:
        job_id = None
        with _lock:
            if _queue:
                job_id = _queue.popleft()
        if not job_id:
            time.sleep(0.15)
            continue
        try:
            _run_job(job_id)
        except Exception as e:
            log.exception("job %s failed: %s", job_id, e)
            with _lock:
                j = _jobs.get(job_id)
                if j:
                    j["status"] = "failed"
                    j["error"] = str(e)
                    j["finishedAt"] = _now()


def _run_job(job_id: str) -> None:
    from ..browser.launcher import launch_profile, stop_profile

    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if job["status"] == "cancelled" or job_id in _cancel:
            job["status"] = "cancelled"
            job["finishedAt"] = _now()
            return
        job["status"] = "running"
        job["startedAt"] = _now()

    jtype = job["type"]
    ids = list(job["profileIds"])
    url = job.get("url")
    direct = bool(job.get("direct"))
    cdp = bool(job.get("cdp"))
    results: list[dict[str, Any]] = []

    if jtype in ("launch_profile", "bulk_launch"):
        settings = get_settings()
        tile_required = bool(job.get("tileRequired"))
        layout_mgr = get_layout_manager()
        slot_rects: dict[str, Any] = {}
        # Fail-closed: validate work area + plan BEFORE any launch when required.
        # Best-effort: still pass slot geometry as Chrome --window-position hints.
        if settings.window_tiling_enabled:
            if tile_required:
                try:
                    pre_plan = layout_mgr.build_plan(ids)
                except WorkAreaError as e:
                    with _lock:
                        job["status"] = "failed"
                        job["error"] = "work_area_unavailable"
                        job["tile"] = {
                            "ok": False,
                            "error": "work_area_unavailable",
                            "message": str(e),
                            "reasons": e.reasons,
                            "results": [],
                        }
                        job["finishedAt"] = _now()
                    return
                except ValueError as e:
                    with _lock:
                        job["status"] = "failed"
                        job["error"] = "grid_unavailable"
                        job["tile"] = {"ok": False, "error": "grid_unavailable", "message": str(e), "results": []}
                        job["finishedAt"] = _now()
                    return
                slot_rects = slot_rect_map(pre_plan)
            else:
                pre_plan, _meta = layout_mgr.best_effort_plan(ids)
                slot_rects = slot_rect_map(pre_plan)
        launched_ids: list[str] = []
        for i, pid in enumerate(ids):
            if _is_cancelled(job_id):
                with _lock:
                    job["status"] = "cancelled"
                    job["results"] = results
                    job["finishedAt"] = _now()
                return
            # Visible queue state while waiting for a concurrency slot
            try:
                set_runtime(pid, "queued", None)
            except Exception:
                pass
            got = browser_manager.acquire_slot(pid, timeout=180.0)
            if not got:
                try:
                    set_runtime(pid, "failed", None)
                except Exception:
                    pass
                results.append({"profileId": pid, "ok": False, "error": "concurrency_timeout", "status": "failed"})
            else:
                try:
                    result = launch_profile(
                        pid,
                        url=url,
                        direct=direct,
                        cdp=cdp,
                        _slot_held=True,
                        window_rect=slot_rects.get(pid),
                    )
                    result = {**result, "profileId": pid}
                    results.append(result)
                    if not result.get("ok"):
                        browser_manager.release_slot(pid)
                        try:
                            set_runtime(pid, "failed", None)
                        except Exception:
                            pass
                    else:
                        browser_manager.mark_running(pid)
                        launched_ids.append(pid)
                except Exception as e:
                    browser_manager.release_slot(pid)
                    try:
                        set_runtime(pid, "failed", None)
                    except Exception:
                        pass
                    results.append({"profileId": pid, "ok": False, "error": str(e), "status": "failed"})
            with _lock:
                job["progress"] = i + 1
                job["results"] = list(results)


        # CDP tiling via WindowLayoutManager (sole geometry owner). Never kill browsers on tile failure.
        tile_info: dict[str, Any] | None = None
        if settings.window_tiling_enabled and launched_ids:
            if tile_required:
                try:
                    plan = layout_mgr.build_plan(launched_ids)
                    tile_info = layout_mgr.apply_plan(plan)
                except WorkAreaError as e:
                    tile_info = {
                        "ok": False,
                        "error": "work_area_unavailable",
                        "message": str(e),
                        "reasons": e.reasons,
                        "results": [],
                    }
                except Exception as e:
                    tile_info = {"ok": False, "error": "tile_failed", "message": str(e), "results": []}
            else:
                plan, meta = layout_mgr.best_effort_plan(launched_ids)
                if plan is not None:
                    try:
                        tile_info = layout_mgr.apply_plan(plan)
                    except Exception as e:
                        tile_info = {"ok": False, "error": "tile_failed", "message": str(e), "results": []}
                else:
                    tile_info = meta
        with _lock:
            job["tile"] = tile_info

    elif jtype in ("stop_profile", "bulk_stop"):
        for i, pid in enumerate(ids):
            if _is_cancelled(job_id):
                with _lock:
                    job["status"] = "cancelled"
                    job["results"] = results
                    job["finishedAt"] = _now()
                return
            try:
                result = stop_profile(pid)
                result = {**result, "profileId": pid}
            except Exception as e:
                result = {"profileId": pid, "ok": False, "error": str(e)}
            results.append(result)
            browser_manager.release_slot(pid)
            with _lock:
                job["progress"] = i + 1
                job["results"] = list(results)

    ok = all(r.get("ok") for r in results) if results else True
    tile_info = None
    with _lock:
        tile_info = job.get("tile")
    if bool(job.get("tileRequired")) and tile_info is not None and not tile_info.get("ok"):
        ok = False
    with _lock:
        job["status"] = "completed" if ok else "failed"
        if not ok and not job.get("error"):
            if bool(job.get("tileRequired")) and tile_info is not None and not tile_info.get("ok"):
                job["error"] = tile_info.get("error") or "tile_failed"
            else:
                job["error"] = "one or more profiles failed"
        job["results"] = results
        job["finishedAt"] = _now()
    audit_log(
        "job_done",
        profile_id=ids[0] if ids else None,
        ok=ok,
        detail={"jobId": job_id, "type": jtype, "status": job["status"]},
    )
