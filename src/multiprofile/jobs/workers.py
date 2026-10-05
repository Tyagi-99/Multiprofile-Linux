"""Durable job worker pool.

A :class:`JobWorker` runs a dispatcher thread that atomically claims queued
jobs from the SQLite store and executes them on a
``concurrent.futures.ThreadPoolExecutor``. Launches are additionally bounded
by a semaphore (``max_concurrent_launches``) so slow browsers cannot pile up.

Lifecycle::

    worker = JobWorker()          # reads [jobs] config
    worker.start()                # dispatcher + executor threads
    job_id = enqueue("launch", profile_id, {...})
    ...
    worker.stop()                 # graceful; in-flight jobs finish

On process startup call ``multiprofile.storage.db.recover()`` BEFORE
``worker.start()`` so jobs left ``running`` by a crash are re-queued first.

Every job publishes ``job.progress`` events (``queued -> running ->
done | failed``) on the in-process events bus.
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..api import events
from ..audit import audit_log
from ..config import get_settings
from ..storage import db as _db
from . import batches
from .handlers import DISPATCH, HandlerContext, JobCancelled, JobHandlerError

log = logging.getLogger("multiprofile.jobs.workers")

POLL_INTERVAL_S = 0.25


class JobWorker:
    """Claims and executes durable jobs."""

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        max_concurrent_launches: int | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
        launch_fn: Callable[..., dict[str, Any]] | None = None,
        stop_fn: Callable[[str], Any] | None = None,
        on_job_done: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        settings = get_settings()
        # 0 means "follow the profile concurrency cap" -- see config.py. The
        # cap is the authoritative gate (`browser_manager.acquire_slot`), so a
        # narrower worker pool would only serialise launches behind it and
        # undo F33 without changing how many browsers can exist.
        cap = max(1, int(settings.max_concurrent_profiles))
        self.max_workers = max_workers or settings.jobs_max_workers or cap
        self.max_concurrent_launches = (
            max_concurrent_launches or settings.jobs_max_concurrent_launches or cap
        )
        self.poll_interval_s = poll_interval_s
        self._launch_semaphore = threading.Semaphore(self.max_concurrent_launches)
        self._ctx = HandlerContext(
            launch_semaphore=self._launch_semaphore,
            launch_fn=launch_fn,
            stop_fn=stop_fn,
        )
        self._on_job_done = on_job_done
        self._executor: ThreadPoolExecutor | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API

    def start(self) -> "JobWorker":
        with self._lock:
            if self._started:
                return self
            self._stop_event.clear()
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="mp-job",
            )
            self._thread = threading.Thread(
                target=self._dispatch_loop, name="mp-job-dispatch", daemon=True
            )
            self._thread.start()
            self._started = True
        log.info(
            "job worker started (max_workers=%d, max_concurrent_launches=%d)",
            self.max_workers,
            self.max_concurrent_launches,
        )
        return self

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
            self._stop_event.set()
            thread, executor = self._thread, self._executor
            self._thread, self._executor = None, None
        if thread is not None:
            thread.join(timeout=timeout)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        log.info("job worker stopped")

    @property
    def running(self) -> bool:
        return self._started

    # ------------------------------------------------------------- dispatch

    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                record = _db.claim_next_job()
            except Exception:  # noqa: BLE001 - dispatcher must survive DB hiccups
                log.exception("job claim failed")
                self._stop_event.wait(self.poll_interval_s)
                continue
            if record is None:
                self._stop_event.wait(self.poll_interval_s)
                continue
            executor = self._executor
            if executor is None:
                # Shutting down: put the claim back so it is not lost.
                _db.update_job_record(record["id"], status="queued")
                return
            executor.submit(self._run_job, record["id"])

    def _run_job(self, job_id: int) -> None:
        record = _db.get_job_record(job_id)
        if record is None:
            return
        # Decode JSON columns for the handler.
        record = dict(record)
        for key in ("payload_json", "result_json"):
            raw = record.get(key)
            if isinstance(raw, str):
                try:
                    record[key.replace("_json", "")] = json.loads(raw)
                except json.JSONDecodeError:
                    record[key.replace("_json", "")] = None
        job_type = record.get("job_type")
        batch_id = (record.get("payload") or {}).get("batchId")
        # A batch goes 'running' when its first member does, not when it is
        # created: the operator's progress bar should start moving when work
        # starts, and a batch sitting behind the concurrency cap has not.
        batches.on_member_started(batch_id)
        events.publish(
            "job.progress",
            {"job_id": job_id, "status": "running",
             "detail": {"type": job_type, "batchId": batch_id}},
        )
        handler = DISPATCH.get(job_type)
        if handler is None:
            self._finish_job(record, ok=False, error=f"unknown job type: {job_type}")
            return
        try:
            result = handler(record, self._ctx)
        except JobCancelled:
            # Not a failure and not retryable: the operator asked for this.
            _db.update_job_record(job_id, status="cancelled", error=None)
            events.publish(
                "job.progress",
                {"job_id": job_id, "status": "cancelled",
                 "detail": {"type": job_type, "batchId": batch_id}},
            )
        except JobHandlerError as e:
            self._finish_job(record, ok=False, error=str(e))
        except Exception:  # noqa: BLE001 - unexpected handler crash
            self._finish_job(
                record, ok=False, error=f"handler crashed: {traceback.format_exc(limit=5)}"
            )
        else:
            self._finish_job(record, ok=True, result=result)
        # After the member has settled, and outside the try: the batch may now
        # be complete, which is where tiling and the final status happen. A
        # failure here must not be reported as a handler failure.
        batches.on_member_finished(batch_id)

    def _finish_job(
        self,
        record: dict[str, Any],
        *,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        job_id = record["id"]
        attempts = int(record.get("attempts") or 0)
        max_attempts = int(record.get("max_attempts") or 3)
        if ok:
            _db.update_job_record(
                job_id,
                status="done",
                result_json=json.dumps(result or {}, separators=(",", ":")),
                error=None,
            )
            final = "done"
            detail: dict[str, Any] = {"type": record.get("job_type"), "ok": True}
        elif attempts < max_attempts:
            _db.update_job_record(
                job_id, status="queued", error=f"attempt {attempts} failed: {error}"
            )
            final = "queued"
            detail = {
                "type": record.get("job_type"),
                "ok": False,
                "retry": True,
                "attempt": attempts,
                "max_attempts": max_attempts,
                "error": error,
            }
        else:
            _db.update_job_record(job_id, status="failed", error=error)
            final = "failed"
            detail = {
                "type": record.get("job_type"),
                "ok": False,
                "attempt": attempts,
                "max_attempts": max_attempts,
                "error": error,
            }
        events.publish("job.progress", {"job_id": job_id, "status": final, "detail": detail})
        audit_log(
            "job_finished",
            profile_id=record.get("profile_id"),
            ok=ok,
            detail={"jobId": job_id, "type": record.get("job_type"), "status": final},
        )
        log.info("job %d (%s) -> %s", job_id, record.get("job_type"), final)
        if self._on_job_done is not None:
            try:
                self._on_job_done({"job_id": job_id, "status": final, "ok": ok})
            except Exception:  # noqa: BLE001 - callback must not break the worker
                log.exception("on_job_done callback failed")


# Module-level singleton for the running process (started by app wiring).
_worker: JobWorker | None = None
_worker_lock = threading.Lock()


def start_workers(**kwargs: Any) -> JobWorker:
    """Start (or return) the process-wide worker singleton."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.running:
            _worker = JobWorker(**kwargs).start()
        return _worker


def stop_workers(timeout: float = 10.0) -> None:
    global _worker
    with _worker_lock:
        worker, _worker = _worker, None
    if worker is not None:
        worker.stop(timeout=timeout)


def get_worker() -> JobWorker | None:
    with _worker_lock:
        return _worker


__all__ = ["JobWorker", "start_workers", "stop_workers", "get_worker", "POLL_INTERVAL_S"]
