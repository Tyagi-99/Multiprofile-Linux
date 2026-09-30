"""Retired: the in-process job queue that F20 replaced.

This module held the original queue — a dict of jobs, a deque, and a worker
thread. It modelled batches correctly and lost every one of them when the
process ended, which is what **F20** was: ``POST /api/jobs`` wrote here while
the durable SQLite queue next door had workers, crash recovery and a schema
and no ordinary producer at all. Two queues, one watched, one durable, neither
both.

The durable queue now carries batches (:mod:`multiprofile.jobs.batches`) and
is the only producer. Nothing imports this.

It is left as a module rather than deleted so that an import missed during the
migration fails here, loudly, with this explanation — instead of silently
starting a second queue whose jobs the console cannot see and a restart
discards. That failure mode is the finding itself, and it is not worth risking
again to save a file.

The implementation is in git history at ``jobs/legacy.py`` before the F20
commit if it is ever needed for reference.
"""
from __future__ import annotations

_MESSAGE = (
    "multiprofile.jobs.legacy was retired by F20. The durable queue in "
    "multiprofile.jobs.batches is the only producer; import create_job, "
    "get_job, list_jobs or cancel_job from multiprofile.jobs instead."
)


def __getattr__(name: str):
    raise AttributeError(f"{_MESSAGE} (attribute requested: {name!r})")


__all__: list[str] = []
