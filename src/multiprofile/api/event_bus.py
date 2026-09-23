"""Event bus accessor with a thread-safe in-process fallback.

Workstream 1's ``src/multiprofile/api/events.py`` is the canonical bus —
an in-process pub/sub exposing ``publish(event_type, payload)`` /
``subscribe()`` (plus ``unsubscribe`` / ``subscribe_fn``). Everything here
goes through :func:`get_bus()`:

- If ``multiprofile.api.events`` is importable and exposes callable
  ``publish``/``subscribe``, the real bus is used (this is now the case).
- Otherwise a minimal in-process fallback bus (same method names, queue
  based) is used so the SSE endpoint and the emit helpers work standalone.

Message shape note: WS1's ``subscribe()`` yields ``(event_type, payload)``
tuples and ``publish()`` does not timestamp. Consumers that need the
uniform envelope ``{"type", "payload", "at"}`` should use
:func:`normalize_message` (used by the SSE endpoint).

INTEGRATION POINTS FOR WS1:
- ``emit_profile_status()`` / ``emit_job_progress()`` are already called
  from the launch/stop and job routes in ``server.py``. When the supervisor
  and job workers exist, WS1 should publish from the *authoritative* state
  transitions there (browser/supervisor.py, jobs worker loop) using the
  same event types and payload shapes, and can remove the best-effort
  calls from the routes if they become duplicates.
- The SSE endpoint in ``sse.py`` resolves the bus lazily per request, so
  subscribers always attach to whichever bus is live.
"""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("multiprofile.api.events")

_fallback: "FallbackBus | None" = None
_fallback_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FallbackBus:
    """Minimal in-process pub/sub bus (queue per subscriber)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        message = {
            "type": event_type,
            "payload": dict(payload or {}),
            "at": _now_iso(),
        }
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass  # slow consumer: drop, never block publishers

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)


def _ws1_bus():
    """Return WS1's bus if it exists and exposes publish/subscribe, else None."""
    try:
        from . import events as ws1  # type: ignore
    except Exception:
        return None
    publish = getattr(ws1, "publish", None)
    subscribe = getattr(ws1, "subscribe", None)
    if callable(publish) and callable(subscribe):
        return ws1
    log.warning("multiprofile.api.events present but missing publish/subscribe; using fallback bus")
    return None


def get_bus():
    """Return the live event bus (WS1's if present, else the fallback)."""
    bus = _ws1_bus()
    if bus is not None:
        return bus
    global _fallback
    with _fallback_lock:
        if _fallback is None:
            _fallback = FallbackBus()
        return _fallback


def subscribe_queue(bus) -> queue.Queue:
    """Attach a queue to any bus, adapting WS1's unknown subscribe() shape.

    - queue-like return (has ``get``) is used directly;
    - an iterable/generator return is pumped into a queue on a daemon thread;
    - anything else raises and the caller falls back to the FallbackBus.
    """
    sub = bus.subscribe()
    if hasattr(sub, "get") and callable(sub.get):
        return sub
    q: queue.Queue = queue.Queue(maxsize=1000)

    def _pump() -> None:
        try:
            for item in sub:
                try:
                    q.put_nowait(item)
                except queue.Full:
                    pass
        except Exception as e:  # pragma: no cover - defensive
            log.warning("event pump ended: %s", e)

    threading.Thread(target=_pump, name="mp-events-pump", daemon=True).start()
    return q


def emit(bus, event_type: str, payload: dict[str, Any]) -> None:
    """Publish one event, never raising into the request path."""
    try:
        bus.publish(event_type, payload)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("event publish failed (%s): %s", event_type, e)


def emit_profile_status(
    bus, profile_id: str, old_state: str | None, new_state: str
) -> None:
    """Emit ``profile.status`` {profile_id, old_state, new_state, at}."""
    emit(
        bus,
        "profile.status",
        {"profile_id": profile_id, "old_state": old_state, "new_state": new_state},
    )


def emit_job_progress(
    bus,
    job_id: str,
    job_type: str,
    profile_id: str | None,
    status: str,
    detail: str | None = None,
) -> None:
    """Emit ``job.progress`` {job_id, job_type, profile_id, status, detail, at}."""
    payload: dict[str, Any] = {
        "job_id": job_id,
        "job_type": job_type,
        "profile_id": profile_id,
        "status": status,
    }
    if detail is not None:
        payload["detail"] = detail
    emit(bus, "job.progress", payload)


def normalize_message(item: Any) -> dict[str, Any]:
    """Normalize any bus message to the uniform envelope.

    Handles WS1's ``(event_type, payload)`` tuples as well as the fallback
    bus's ``{"type", "payload", "at"}`` dicts. ``"at"`` defaults to receive
    time when the producer did not stamp it.
    """
    if isinstance(item, (tuple, list)) and len(item) == 2:
        event_type, payload = item
        payload = dict(payload or {}) if isinstance(payload, dict) else {"value": payload}
        return {
            "type": str(event_type),
            "payload": payload,
            "at": payload.get("at") or _now_iso(),
        }
    if isinstance(item, dict):
        payload = item.get("payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        return {
            "type": item.get("type"),
            "payload": payload,
            "at": item.get("at") or payload.get("at") or _now_iso(),
        }
    return {"type": "unknown", "payload": {"raw": str(item)}, "at": _now_iso()}
