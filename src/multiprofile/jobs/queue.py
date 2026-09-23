"""Durable job queue backed by the SQLite control-plane store.

``enqueue()`` writes a row to the ``jobs`` table; worker threads claim rows
atomically (``UPDATE ... WHERE status='queued'``) and hand them to the
handler dispatch table in :mod:`multiprofile.jobs.handlers`.

Durability contract: a job that is ``queued`` is never lost by a process
restart. A job that was ``running`` when the process died is re-queued on
startup by :func:`multiprofile.storage.db.recover` (with ``attempts + 1``);
once ``attempts`` reaches ``max_attempts`` it is marked ``failed`` instead of
being retried forever.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..storage import db as _db
from . import handlers

log = logging.getLogger("multiprofile.jobs.queue")

JOB_TYPES = tuple(sorted(handlers.DISPATCH.keys()))

DEFAULT_MAX_ATTEMPTS = 3


def enqueue(
    job_type: str,
    profile_id: str | None = None,
    payload: dict[str, Any] | None = None,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Persist a new job and return its integer id.

    Raises:
        ValueError: for an unknown ``job_type``.
    """
    if job_type not in handlers.DISPATCH:
        raise ValueError(
            f"unsupported job type: {job_type!r}; known: {JOB_TYPES}"
        )
    payload_json = json.dumps(payload or {}, separators=(",", ":"))
    job_id = _db.insert_job(job_type, profile_id, payload_json, max_attempts=max_attempts)
    log.info("enqueued job %d type=%s profile=%s", job_id, job_type, profile_id)
    return job_id


def get_job(job_id: int) -> dict[str, Any] | None:
    """Fetch one durable job record (payload/result JSON decoded)."""
    row = _db.get_job_record(job_id)
    return _decode(row) if row else None


def list_jobs(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return [_decode(r) for r in _db.list_job_records(status=status, limit=limit)]


def pending_count() -> int:
    return _db.count_jobs("queued")


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    for key in ("payload_json", "result_json"):
        raw = row.get(key)
        if isinstance(raw, str):
            try:
                row[key.replace("_json", "")] = json.loads(raw)
            except json.JSONDecodeError:
                row[key.replace("_json", "")] = None
        else:
            row[key.replace("_json", "")] = None
    return row


__all__ = ["JOB_TYPES", "DEFAULT_MAX_ATTEMPTS", "enqueue", "get_job", "list_jobs", "pending_count"]
