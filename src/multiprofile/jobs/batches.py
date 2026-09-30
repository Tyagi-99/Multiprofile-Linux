"""Batch jobs on the durable queue — F20's migration.

The durable SQLite queue has always been per-profile: one row, one profile,
one handler. The operator does not work that way. "Launch these eight" is one
thing they started, with one progress bar and one cancel button, and the
legacy in-process queue was the only thing that modelled it. That mismatch is
why ``POST /api/jobs`` kept writing to the legacy queue long after the durable
one had workers, recovery and a schema: moving the writer was never a
redirect, it needed the missing concept.

A **batch** is that concept. It owns N rows in ``jobs`` joined by ``batch_id``,
and it is the record the API and console read. Three properties are worth
stating because they are the reason this is a migration rather than a rewrite:

**Progress is derived, never stored.** It is a count of members in a terminal
state. A stored counter and the rows it counts can disagree after a crash, and
the rows are the truth; a progress bar that survives a restart saying 5/8 when
three rows are still queued is worse than no progress bar.

**Results keep the operator's order.** Members carry ``batch_seq``, so the
results list reads in the order the profiles were given rather than the order
they happened to finish in. The legacy queue took care to do this and a grid
that reshuffles between identical runs is a real annoyance.

**Tiling is planned before anything launches and applied after everything
has.** When ``tileRequired`` is set, the work area is resolved at create time
and the batch is refused outright if it cannot be — failing before any browser
starts is strictly better than failing after eight of them have. The grid is
then applied once, when the last member finishes, using only the profiles that
actually launched, in their original order.

What is *not* preserved: the legacy queue's jobs lived in a process. These
survive it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..audit import audit_log
from ..browser.work_area import WorkAreaError
from ..config import get_settings
from ..storage import db as _db

log = logging.getLogger("multiprofile.jobs.batches")

#: The API's vocabulary. Unchanged from the legacy queue on purpose -- this is
#: a migration of the machinery underneath, and a client that worked yesterday
#: must not need editing today.
JOB_TYPES = ("launch_profile", "stop_profile", "bulk_launch", "bulk_stop")

_LAUNCH_TYPES = ("launch_profile", "bulk_launch")
_STOP_TYPES = ("stop_profile", "bulk_stop")

#: API job type -> durable handler name in `handlers.DISPATCH`.
_MEMBER_HANDLER = {
    "launch_profile": "launch",
    "bulk_launch": "launch",
    "stop_profile": "stop",
    "bulk_stop": "stop",
}

#: Only one thread may finalise a batch. The database guard below is the real
#: protection (an UPDATE that claims the transition); this just avoids N
#: threads racing to do the same tiling work.
_finalize_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Time
#
# The legacy record used epoch floats and the durable rows use ISO strings.
# The API keeps epoch floats, because the console sorts and formats on them
# and changing the wire format is not part of moving a queue.
# ---------------------------------------------------------------------------

def _epoch(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        text = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _now() -> float:
    return time.time()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Reading — the legacy record shape, rebuilt from durable rows
# ---------------------------------------------------------------------------

def _member_result(row: dict[str, Any]) -> dict[str, Any]:
    """One entry of the batch's `results` list, in the legacy shape."""
    raw = row.get("result_json")
    result: dict[str, Any] = {}
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                result = parsed
        except json.JSONDecodeError:
            result = {}
    status = str(row.get("status") or "")
    out = {
        **result,
        "profileId": row.get("profile_id"),
        "ok": status == "done",
        "status": status,
    }
    if row.get("error"):
        out["error"] = row["error"]
    return out


def _record(batch: dict[str, Any], members: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The batch as the API and console have always seen a job."""
    batch_id = str(batch["id"])
    if members is None:
        members = _db.list_batch_members(batch_id)
    payload: dict[str, Any] = {}
    raw = batch.get("payload_json")
    if isinstance(raw, str) and raw:
        try:
            payload = json.loads(raw) or {}
        except json.JSONDecodeError:
            payload = {}

    settled = [m for m in members if m.get("status") in _db.TERMINAL_JOB_STATES]
    tile = None
    if isinstance(batch.get("tile_json"), str) and batch["tile_json"]:
        try:
            tile = json.loads(batch["tile_json"])
        except json.JSONDecodeError:
            tile = None

    return {
        "id": batch_id,
        "type": batch.get("job_type"),
        "profileIds": list(payload.get("profileIds") or []),
        "url": payload.get("url"),
        "direct": bool(payload.get("direct")),
        "cdp": bool(payload.get("cdp")),
        "tileRequired": bool(payload.get("tileRequired")),
        "status": batch.get("status"),
        # Derived, not stored: see the module docstring.
        "progress": len(settled),
        "total": int(batch.get("total") or len(members)),
        "results": [_member_result(m) for m in members if m.get("status") in _db.TERMINAL_JOB_STATES],
        "tile": tile,
        "error": batch.get("error"),
        "createdAt": _epoch(batch.get("created_at")) or 0.0,
        "startedAt": _epoch(batch.get("started_at")),
        "finishedAt": _epoch(batch.get("finished_at")),
        "source": "durable",
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    batch = _db.get_job_batch(str(job_id))
    return _record(batch) if batch else None


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    return [_record(b) for b in _db.list_job_batches(limit=limit)]


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

def _plan_tiling(ids: list[str], tile_required: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve the grid before anything launches.

    Returns ``(slot_rects, refusal)``. A refusal is a tile-info dict and means
    the batch must not start: with ``tileRequired`` the operator asked for a
    tiled fleet, and discovering the work area is unavailable after eight
    browsers are open helps nobody.
    """
    from ..browser.grid import slot_rect_map
    from ..browser.layout_manager import get_layout_manager

    settings = get_settings()
    if not settings.window_tiling_enabled:
        return {}, None
    manager = get_layout_manager()
    if tile_required:
        try:
            plan = manager.build_plan(ids)
        except WorkAreaError as e:
            return {}, {
                "ok": False, "error": "work_area_unavailable",
                "message": str(e), "reasons": e.reasons, "results": [],
            }
        except ValueError as e:
            return {}, {
                "ok": False, "error": "grid_unavailable",
                "message": str(e), "results": [],
            }
        return slot_rect_map(plan), None
    plan, _meta = manager.best_effort_plan(ids)
    return (slot_rect_map(plan) if plan is not None else {}), None


def create_job(
    job_type: str,
    profile_ids: list[str],
    *,
    url: str | None = None,
    direct: bool = False,
    cdp: bool = False,
    tile_required: bool = False,
) -> dict[str, Any]:
    """Create a batch and enqueue one durable job per profile."""
    if job_type not in JOB_TYPES:
        raise ValueError(f"unsupported job type: {job_type}")
    ids = [str(x) for x in (profile_ids or []) if x]
    if not ids:
        raise ValueError("profileIds required")

    batch_id = "job_" + uuid.uuid4().hex[:10]
    payload = {
        "profileIds": ids,
        "url": url,
        "direct": bool(direct),
        "cdp": bool(cdp),
        "tileRequired": bool(tile_required),
    }
    payload_json = json.dumps(payload, separators=(",", ":"))

    slot_rects: dict[str, Any] = {}
    refusal: dict[str, Any] | None = None
    if job_type in _LAUNCH_TYPES:
        slot_rects, refusal = _plan_tiling(ids, bool(tile_required))

    batch = _db.insert_job_batch(batch_id, job_type, payload_json, len(ids))

    if refusal is not None:
        # Persisted as a failed batch rather than raised: the operator pressed
        # a button and is owed a job record saying why nothing happened, which
        # a 4xx with no trace in the jobs list would not give them.
        _db.update_job_batch(
            batch_id,
            status="failed",
            error=refusal.get("error") or "tile_unavailable",
            tile_json=json.dumps(refusal, separators=(",", ":")),
            finished_at=_iso(),
        )
        audit_log("job_create", profile_id=ids[0], ok=False,
                  detail={"jobId": batch_id, "type": job_type,
                          "count": len(ids), "refused": refusal.get("error")})
        return _record(_db.get_job_batch(batch_id) or batch, [])

    handler = _MEMBER_HANDLER[job_type]
    entries = []
    for pid in ids:
        member_payload: dict[str, Any] = {"batchId": batch_id}
        if job_type in _LAUNCH_TYPES:
            member_payload.update({"url": url, "direct": bool(direct), "cdp": bool(cdp)})
            rect = slot_rects.get(pid)
            if rect is not None:
                # As plain numbers: the payload is JSON in a column, and a
                # Rect that only exists in this process would not survive the
                # restart the whole migration is for.
                member_payload["window_rect"] = {
                    "x": rect.x, "y": rect.y, "w": rect.w, "h": rect.h,
                }
        entries.append((pid, json.dumps(member_payload, separators=(",", ":"))))

    # One transaction: a half-created batch whose total says 8 and whose rows
    # number 3 is a progress bar that can never finish.
    _db.insert_batch_members(batch_id, handler, entries)

    audit_log("job_create", profile_id=ids[0], ok=True,
              detail={"jobId": batch_id, "type": job_type, "count": len(ids)})
    return _record(_db.get_job_batch(batch_id) or batch)


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------

def cancel_job(job_id: str) -> dict[str, Any]:
    batch = _db.get_job_batch(str(job_id))
    if not batch:
        return {"ok": False, "error": "not found"}
    if batch.get("status") in ("completed", "failed", "cancelled"):
        return {"ok": True, "job": _record(batch), "note": "already finished"}

    counts = _db.request_batch_cancel(str(job_id))
    # A member already running cannot be interrupted safely -- a half-launched
    # browser is worse than a finished one -- so the batch only settles as
    # cancelled once those finish. When none were running it settles now.
    if counts["stillRunning"] == 0:
        _finalize(str(job_id))
    batch = _db.get_job_batch(str(job_id)) or batch
    out = {"ok": True, "job": _record(batch)}
    if counts["stillRunning"]:
        out["note"] = (
            f"{counts['stillRunning']} profile(s) already launching; they will "
            "finish and the batch will then be marked cancelled"
        )
    return out


def cancel_requested(job_id: int) -> bool:
    """Whether the member job has been asked to stop. Checked by handlers."""
    try:
        return _db.job_cancel_requested(int(job_id))
    except Exception:  # noqa: BLE001 - a cancel check must never fail a job
        return False


# ---------------------------------------------------------------------------
# Lifecycle hooks, called by the worker
# ---------------------------------------------------------------------------

def on_member_started(batch_id: str | None) -> None:
    if not batch_id:
        return
    try:
        batch = _db.get_job_batch(str(batch_id))
        if batch and batch.get("status") == "queued":
            _db.update_job_batch(str(batch_id), status="running", started_at=_iso())
    except Exception as e:  # noqa: BLE001 - bookkeeping must not fail the job
        log.warning("batch %s: could not mark running: %s", batch_id, e)


def on_member_finished(batch_id: str | None) -> None:
    if not batch_id:
        return
    try:
        _finalize(str(batch_id))
    except Exception as e:  # noqa: BLE001 - a batch must not take a worker down
        log.warning("batch %s: finalisation failed: %s", batch_id, e, exc_info=True)


def _finalize(batch_id: str) -> None:
    """Settle the batch if every member has finished. Idempotent."""
    with _finalize_lock:
        batch = _db.get_job_batch(batch_id)
        if not batch or batch.get("status") in ("completed", "failed", "cancelled"):
            return
        members = _db.list_batch_members(batch_id)
        if not members:
            return
        if any(m.get("status") not in _db.TERMINAL_JOB_STATES for m in members):
            return

        payload: dict[str, Any] = {}
        raw = batch.get("payload_json")
        if isinstance(raw, str) and raw:
            try:
                payload = json.loads(raw) or {}
            except json.JSONDecodeError:
                payload = {}

        tile_info = _apply_tiling(batch, payload, members)

        counts: dict[str, int] = {}
        for m in members:
            counts[str(m.get("status"))] = counts.get(str(m.get("status")), 0) + 1
        status = _db._settled_batch_status(counts)

        error = None
        if status != "completed":
            error = ("one or more profiles failed" if counts.get("failed")
                     else "cancelled by the operator")
        # Tiling was demanded and did not happen: the batch did not do what was
        # asked even if every browser started, and saying "completed" would be
        # the same lie F1 was.
        if bool(payload.get("tileRequired")) and tile_info is not None and not tile_info.get("ok"):
            status = "failed"
            error = tile_info.get("error") or "tile_failed"

        _db.update_job_batch(
            batch_id,
            status=status,
            error=error,
            finished_at=_iso(),
            tile_json=(json.dumps(tile_info, separators=(",", ":"))
                       if tile_info is not None else None),
        )
        ids = list(payload.get("profileIds") or [])
        audit_log("job_done", profile_id=ids[0] if ids else None,
                  ok=(status == "completed"),
                  detail={"jobId": batch_id, "type": batch.get("job_type"),
                          "status": status})


def _apply_tiling(
    batch: dict[str, Any], payload: dict[str, Any], members: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Tile the profiles that actually launched, in the operator's order."""
    if batch.get("job_type") not in _LAUNCH_TYPES:
        return None
    settings = get_settings()
    if not settings.window_tiling_enabled:
        return None
    launched = [
        str(m.get("profile_id"))
        for m in sorted(members, key=lambda r: int(r.get("batch_seq") or 0))
        if m.get("status") == "done"
    ]
    if not launched:
        return None

    from ..browser.layout_manager import get_layout_manager

    manager = get_layout_manager()
    if bool(payload.get("tileRequired")):
        try:
            return manager.apply_plan(manager.build_plan(launched))
        except WorkAreaError as e:
            return {"ok": False, "error": "work_area_unavailable",
                    "message": str(e), "reasons": e.reasons, "results": []}
        except Exception as e:  # noqa: BLE001 - the failure is reported in the response, not swallowed
            return {"ok": False, "error": "tile_failed", "message": str(e), "results": []}
    plan, meta = manager.best_effort_plan(launched)
    if plan is None:
        return meta
    try:
        return manager.apply_plan(plan)
    except Exception as e:  # noqa: BLE001 - the failure is reported in the response, not swallowed
        return {"ok": False, "error": "tile_failed", "message": str(e), "results": []}


__all__ = [
    "JOB_TYPES",
    "cancel_job",
    "cancel_requested",
    "create_job",
    "get_job",
    "list_jobs",
    "on_member_finished",
    "on_member_started",
]
