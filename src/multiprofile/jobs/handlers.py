"""Handler dispatch table for durable job types.

Job types: ``launch``, ``stop``, ``restart``, ``park``, ``health_check``,
``consistency_check``.

Every handler has the signature ``handler(record, ctx) -> dict`` where
``record`` is the decoded job row and ``ctx`` is a :class:`HandlerContext`
carrying injectable dependencies (used by tests to mock the launcher).

Cross-workstream interface (``park`` job type):

    ``park_profile(profile_id) -> bool`` imported lazily from
    :mod:`multiprofile.parking` (built by Workstream 2). The import is lazy so
    this module loads before/after that module exists; if it is missing (or
    exposes a different signature) the ``park`` handler fails the job with a
    clear error instead of crashing the worker.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

from ..api import events
from ..audit import audit_log
from ..storage import db as _db
from ..storage.state import ProfileState

log = logging.getLogger("multiprofile.jobs.handlers")


class JobHandlerError(Exception):
    """A handler failed in a way that should fail (or retry) the job."""


@dataclass
class HandlerContext:
    """Injectable dependencies for handlers (mocks in tests)."""

    launch_semaphore: threading.Semaphore | None = None
    launch_fn: Callable[..., dict[str, Any]] | None = None
    stop_fn: Callable[[str], dict[str, Any] | None] | None = None


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _publish_profile_status(profile_id: str, old: str, new: str, note: str | None = None) -> None:
    events.publish(
        "profile.status",
        {"profile_id": profile_id, "old_state": old, "new_state": new, "note": note},
    )


def _transition(record: dict[str, Any], to: ProfileState | str, note: str | None = None) -> None:
    profile_id = record.get("profile_id")
    if not profile_id:
        return
    try:
        change = _db.set_profile_state(profile_id, to, note=note)
    except Exception as e:  # noqa: BLE001 - state write must not kill the job
        log.warning("job %s: state write failed: %s", record.get("id"), e)
        return
    _publish_profile_status(profile_id, change["old"], change["new"], note)


def _launch_fn(ctx: HandlerContext) -> Callable[..., dict[str, Any]]:
    if ctx.launch_fn is not None:
        return ctx.launch_fn
    from ..browser.launcher import launch_profile

    return launch_profile


def _stop_fn(ctx: HandlerContext) -> Callable[[str], Any]:
    if ctx.stop_fn is not None:
        return ctx.stop_fn
    from ..browser.launcher import stop_profile

    return stop_profile


class JobCancelled(Exception):
    """The operator cancelled this job before it started doing anything."""


def _check_cancelled(record: dict[str, Any]) -> None:
    """Raise if this job has been cancelled, at a point where stopping is free.

    F20: a batch's queued members are cancelled outright, but a member already
    claimed by a worker is not -- interrupting a half-launched browser leaves
    a process nobody is tracking, which is worse than one extra window. So the
    check happens here, before any work, and nowhere later.
    """
    from . import batches

    if batches.cancel_requested(record.get("id")):
        raise JobCancelled(str(record.get("id")))


def handle_launch(record: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    """Launch a profile through the existing launcher code path."""
    profile_id = record.get("profile_id")
    if not profile_id:
        raise JobHandlerError("launch job requires a profile_id")
    _check_cancelled(record)
    payload = _payload(record)
    sem = ctx.launch_semaphore
    launch = _launch_fn(ctx)
    _transition(record, ProfileState.STARTING)
    kwargs = {k: v for k, v in payload.items() if k in ("url", "direct", "cdp")}
    # The grid slot this profile was assigned at create time, passed to Chrome
    # as a --window-position hint so the window opens roughly where the tiling
    # pass will put it, instead of jumping after the fact.
    rect = payload.get("window_rect")
    if isinstance(rect, dict):
        from ..browser.grid import Rect

        try:
            kwargs["window_rect"] = Rect(
                x=int(rect["x"]), y=int(rect["y"]),
                w=int(rect["w"]), h=int(rect["h"]),
            )
        except (KeyError, TypeError, ValueError):
            # A hint, not a requirement: the tiling pass at the end of the
            # batch places the window properly either way.
            log.warning("job %s: unusable window_rect %r", record.get("id"), rect)
    if sem is not None:
        with sem:
            result = launch(profile_id, **kwargs)
    else:
        result = launch(profile_id, **kwargs)
    if not isinstance(result, dict) or not result.get("ok"):
        err = (result or {}).get("error", "launch failed") if isinstance(result, dict) else "launch failed"
        _transition(record, ProfileState.ERROR, note=str(err))
        raise JobHandlerError(f"launch failed for {profile_id}: {err}")
    _transition(record, ProfileState.RUNNING)
    audit_log("job_launch", profile_id=profile_id, ok=True, detail={"jobId": record.get("id")})
    return {"ok": True, "pid": result.get("pid"), "cdpPort": result.get("cdpPort")}


def handle_stop(record: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    profile_id = record.get("profile_id")
    if not profile_id:
        raise JobHandlerError("stop job requires a profile_id")
    _check_cancelled(record)
    _transition(record, ProfileState.STOPPING)
    result = _stop_fn(ctx)(profile_id)
    if isinstance(result, dict) and result.get("error"):
        _transition(record, ProfileState.ERROR, note=str(result.get("error")))
        raise JobHandlerError(f"stop failed for {profile_id}: {result.get('error')}")
    _transition(record, ProfileState.STOPPED)
    audit_log("job_stop", profile_id=profile_id, ok=True, detail={"jobId": record.get("id")})
    return {"ok": True, "stopped": (result or {}).get("stopped") if isinstance(result, dict) else None}


def handle_restart(record: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    """Stop then launch — a stop failure does not block the launch attempt."""
    profile_id = record.get("profile_id")
    if not profile_id:
        raise JobHandlerError("restart job requires a profile_id")
    try:
        handle_stop(record, ctx)
    except JobHandlerError as e:
        log.warning("restart job %s: stop phase failed, continuing to launch: %s", record.get("id"), e)
    return handle_launch(record, ctx)


def handle_park(record: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    """Park a profile via the Workstream 2 parking interface.

    Interface contract: ``multiprofile.parking.park_profile(profile_id) -> bool``.
    ``True`` = parked, ``False`` = park refused/failed (job fails, may retry).
    """
    profile_id = record.get("profile_id")
    if not profile_id:
        raise JobHandlerError("park job requires a profile_id")
    try:
        from ..parking import park_profile
    except ImportError as e:
        raise JobHandlerError(
            f"park job unavailable: multiprofile.parking is not present ({e}). "
            "Workstream 2 must provide park_profile(profile_id) -> bool."
        ) from e
    try:
        parked = park_profile(profile_id)
    except TypeError as e:
        raise JobHandlerError(
            f"park_profile signature mismatch: expected park_profile(profile_id) -> bool ({e})"
        ) from e
    if not isinstance(parked, bool):
        raise JobHandlerError(
            f"park_profile returned {type(parked).__name__}, expected bool"
        )
    if not parked:
        raise JobHandlerError(f"park refused for {profile_id}")
    audit_log("job_park", profile_id=profile_id, ok=True, detail={"jobId": record.get("id")})
    return {"ok": True, "parked": True}


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie answers kill(pid, 0) but is already dead.
    stat = f"/proc/{pid}/stat"
    try:
        with open(stat, "r", encoding="utf-8") as f:
            state = f.read().rsplit(")", 1)[-1].split()[0]
        return state != "Z"
    except OSError:
        return True


def handle_health_check(record: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    """Point-in-time health sample: is the recorded PID alive?

    Honest by construction: reports exactly what the OS says about the PID in
    the runtime record. ``alive=false`` for a missing/stale record is reported
    as PARTIAL availability, never fabricated.
    """
    profile_id = record.get("profile_id")
    rt = _db.get_profile_runtime(profile_id) if profile_id else None
    pid = (rt or {}).get("pid")
    alive = _pid_alive(pid)
    detail = {
        "profile_id": profile_id,
        "state": (rt or {}).get("state"),
        "pid": pid,
        "alive": alive,
        "availability": "ok" if alive else "PARTIAL",
    }
    _db.record_heartbeat(profile_id) if profile_id and alive else None
    return {"ok": True, "health": detail}


def handle_consistency_check(record: dict[str, Any], ctx: HandlerContext) -> dict[str, Any]:
    """Run the profile consistency engine against the stored profile."""
    profile_id = record.get("profile_id")
    if not profile_id:
        raise JobHandlerError("consistency_check job requires a profile_id")
    from ..consistency import ProfileConsistencyEngine
    from ..storage.profiles import get_profile

    profile = get_profile(profile_id)
    if not profile:
        raise JobHandlerError(f"profile not found: {profile_id}")
    engine = ProfileConsistencyEngine()
    result = engine.check(profile)
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    return {"ok": True, "consistency": result}


DISPATCH: dict[str, Callable[[dict[str, Any], HandlerContext], dict[str, Any]]] = {
    "launch": handle_launch,
    "stop": handle_stop,
    "restart": handle_restart,
    "park": handle_park,
    "health_check": handle_health_check,
    "consistency_check": handle_consistency_check,
}

__all__ = [
    "DISPATCH",
    "HandlerContext",
    "JobCancelled",
    "JobHandlerError",
    "handle_launch",
    "handle_stop",
    "handle_restart",
    "handle_park",
    "handle_health_check",
    "handle_consistency_check",
]
