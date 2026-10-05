"""Job queue package.

**One queue, since F20.** Two generations used to coexist: an in-process
queue that ``POST /api/jobs`` wrote to, and a durable SQLite queue with
workers, crash recovery and a schema that nothing ordinary ever wrote to. The
durable one could not take over because it was per-profile and the operator
works in batches — "launch these eight" is one thing they started, with one
progress bar and one cancel button.

:mod:`multiprofile.jobs.batches` supplies that concept and is now the only
producer. The names the API server imports (``create_job``/``get_job``/
``list_jobs``/``cancel_job``) are unchanged and return the same record shape
they always did; what changed is that the jobs behind them survive a restart.

:mod:`multiprofile.jobs.legacy` is retired. It remains only as a module that
explains itself and raises, so an import that was missed fails loudly instead
of quietly running a second queue nobody is watching.

Startup contract (wired by the application entry point)::

    from multiprofile.storage import db
    from multiprofile.jobs import recover, start_workers
    db.recover()          # re-queue jobs left 'running' by a crash; FIRST
    start_workers()       # then claim and execute
"""
from __future__ import annotations

from . import batches
from .batches import JOB_TYPES as BATCH_JOB_TYPES
from .handlers import DISPATCH, HandlerContext, JobCancelled, JobHandlerError
from .queue import DEFAULT_MAX_ATTEMPTS, JOB_TYPES, enqueue, pending_count
from .queue import get_job as get_durable_job
from .queue import list_jobs as list_durable_jobs
from .workers import JobWorker, get_worker, start_workers, stop_workers

# The API surface the server imports. Same names, same record shape, durable
# storage underneath.
create_job = batches.create_job
get_job = batches.get_job
list_jobs = batches.list_jobs
cancel_job = batches.cancel_job

#: Kept under its old name because `validate_job_payload` checks against it.
LEGACY_JOB_TYPES = batches.JOB_TYPES

# Durable recovery entry point (re-exported for the startup contract).
from ..storage.db import recover  # noqa: E402

__all__ = [
    # durable queue primitives
    "JOB_TYPES",
    "DEFAULT_MAX_ATTEMPTS",
    "DISPATCH",
    "HandlerContext",
    "JobCancelled",
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
    # batch API (what the HTTP layer and console use)
    "batches",
    "BATCH_JOB_TYPES",
    "create_job",
    "get_job",
    "list_jobs",
    "cancel_job",
    "LEGACY_JOB_TYPES",
]
