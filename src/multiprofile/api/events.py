"""Tiny in-process events bus for control-plane notifications.

Producers (supervisor, job workers, launcher) call :func:`publish`; consumers
(Workstream 3's WebSocket/SSE endpoint) call :func:`subscribe` and read
``(event_type, payload)`` tuples off the returned queue, or register a
callback via :func:`subscribe_fn`.

Event types emitted in v1.7.0:

- ``profile.status`` — payload ``{"profile_id", "old_state", "new_state", "note"}``
- ``job.progress``  — payload ``{"job_id", "status", "detail"}``

FUTURE: multi-process broadcast. This bus is per-process only. If the API
server ever runs as multiple processes, events published in one process will
not reach subscribers in another — that needs a broker (Redis pub/sub,
SQLite polling, or similar) and is explicitly out of scope for v1.7.0.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

log = logging.getLogger("multiprofile.api.events")

_lock = threading.RLock()
_subscribers: list[queue.Queue[tuple[str, dict[str, Any]]] | Callable[[str, dict[str, Any]], None]] = []


def publish(event_type: str, payload: dict[str, Any] | None = None) -> int:
    """Publish an event to all subscribers. Returns the subscriber count.

    Never raises: a failing subscriber must not break the producer.
    """
    payload = dict(payload or {})
    with _lock:
        subs = list(_subscribers)
    delivered = 0
    for sub in subs:
        try:
            if isinstance(sub, queue.Queue):
                sub.put_nowait((event_type, payload))
            else:
                sub(event_type, payload)
            delivered += 1
        except Exception:  # noqa: BLE001 - subscribers must not break producers
            log.exception("event subscriber failed for %s", event_type)
    return delivered


def subscribe() -> queue.Queue[tuple[str, dict[str, Any]]]:
    """Subscribe and receive a queue of ``(event_type, payload)`` tuples."""
    q: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
    with _lock:
        _subscribers.append(q)
    return q


def subscribe_fn(fn: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
    """Subscribe with a callback. Returns an ``unsubscribe()`` callable."""
    with _lock:
        _subscribers.append(fn)

    def _unsubscribe() -> None:
        with _lock:
            try:
                _subscribers.remove(fn)
            except ValueError:
                pass

    return _unsubscribe


def unsubscribe(target: Any) -> None:
    """Remove a queue or callback previously added via subscribe()/subscribe_fn()."""
    with _lock:
        try:
            _subscribers.remove(target)
        except ValueError:
            pass


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def clear_subscribers() -> None:
    """Remove all subscribers (used by tests)."""
    with _lock:
        _subscribers.clear()


__all__ = [
    "publish",
    "subscribe",
    "subscribe_fn",
    "unsubscribe",
    "subscriber_count",
    "clear_subscribers",
]
