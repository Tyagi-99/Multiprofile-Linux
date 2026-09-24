"""Job queue package.

Two generations coexist here (same public surface for the API server):

- :mod:`multiprofile.jobs.legacy` — the original in-process queue
  (``create_job``/``get_job``/``list_jobs``/``cancel_job`` with bulk
  launch/stop). Preserved unchanged for backward compatibility.
- :mod:`multiprofile.jobs.queue` + :mod:`multiprofile.jobs.workers` —
  the durable SQLite-backed queue (``enqueue`` + :class:`JobWorker`),
  introduced in v1.7.0. Survives process restarts.

Startup contract (wired by the application entry point)::

    from multiprofile.storage import db
    from multiprofile.jobs import recover, start_workers
    db.recover()          # re-queue jobs left 'running' by a crash; FIRST
    start_workers()       # then claim and execute
"""
from __future__ import annotations

from . import legacy
from .handlers import DISPATCH, HandlerContext, JobHandlerError
from .queue import DEFAULT_MAX_ATTEMPTS, JOB_TYPES, enqueue, pending_count
from .queue import get_job as get_durable_job
from .queue import list_jobs as list_durable_jobs
from .workers import JobWorker, get_worker, start_workers, stop_workers

# Legacy API preserved for the API server (server.py imports these names).
create_job = legacy.create_job
get_job = legacy.get_job
list_jobs = legacy.list_jobs
cancel_job = legacy.cancel_job
LEGACY_JOB_TYPES = legacy.JOB_TYPES

# Durable recovery entry point (re-exported for the startup contract).
from ..storage.db import recover  # noqa: E402

__all__ = [
    # durable queue
    "JOB_TYPES",
    "DEFAULT_MAX_ATTEMPTS",
    "DISPATCH",
    "HandlerContext",
    "JobHandlerError",
    "JobWorker",
    "enqueue",
    "get_durable_job",
    "list_durable_jobs",
    "pending_count",
    "get_worker",
    "start_workers",
    "stop_workers",
    "recover",
    # legacy in-process queue (unchanged behavior)
    "create_job",
    "get_job",
    "list_jobs",
    "cancel_job",
    "LEGACY_JOB_TYPES",
    "legacy",
]
