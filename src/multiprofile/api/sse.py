"""Live event stream endpoint.

WS-vs-SSE decision: neither ``flask-sock`` nor ``gevent-websocket`` is
installed in this environment, and adding a WebSocket extension would pull
in a heavier async serving story (gevent workers) for a dev-server app.
Server-Sent Events work with plain Flask, need no new dependencies, are
trivially testable in-process, and are a natural fit for a one-way
server -> client event feed. The endpoint consumes WS1's
``multiprofile.api.events`` bus (``(event_type, payload)`` tuples) and
re-emits each event in the uniform envelope
``{"type", "payload", "at"}``; if a WebSocket server is adopted later, the
same bus and envelope can feed it unchanged.

Endpoint: ``GET /api/events/stream``

- Content-Type ``text/event-stream``; a ``: ping`` comment every 20s keeps
  proxies from closing idle connections.
- Query param ``max_events=N`` (optional, default 0 = infinite): close the
  stream after N events. Intended for tests and finite replays.
- Event frames follow the bus envelope:
  ``{"type": ..., "payload": {...}, "at": <iso>}``.

Events emitted:
- ``profile.status`` {profile_id, old_state, new_state, at}
- ``job.progress``   {job_id, job_type, profile_id, status, detail, at}
"""
from __future__ import annotations

import json
import logging
import queue

from flask import Response, request, stream_with_context

from .event_bus import (
    emit_job_progress,
    emit_profile_status,
    get_bus,
    normalize_message,
    subscribe_queue,
)

log = logging.getLogger("multiprofile.api.events")

__all__ = ["register_events_routes", "emit_profile_status", "emit_job_progress"]


def register_events_routes(app) -> None:
    """Register ``GET /api/events/stream`` (SSE) on a Flask app."""

    @app.get("/api/events/stream")
    def events_stream():
        bus = get_bus()
        try:
            sub = subscribe_queue(bus)
        except Exception as e:
            log.warning("event subscribe failed: %s", e)
            return {"error": "event bus unavailable"}, 503
        try:
            max_events = int(request.args.get("max_events") or 0)
        except (TypeError, ValueError):
            max_events = 0

        def _gen():
            sent = 0
            try:
                # Replay comment so clients/tests see the stream open.
                yield ": connected\n\n"
                while True:
                    try:
                        message = normalize_message(sub.get(timeout=20))
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
                    sent += 1
                    if max_events and sent >= max_events:
                        break
            finally:
                unsub = getattr(bus, "unsubscribe", None)
                if callable(unsub):
                    try:
                        unsub(sub)
                    except Exception:
                        pass

        return Response(
            stream_with_context(_gen()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
