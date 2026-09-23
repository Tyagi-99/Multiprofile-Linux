"""Integration tests for v1.7.0: create_app() startup sequence.

Proves, without launching any browser, that the Flask application entry
point performs the full control-plane startup:

    recover() -> start_workers() -> supervisor.start() (guarded by config)

plus the fail-safe paths (disabled supervisor, dead control DB,
MULTIPROFILE_NO_BACKGROUND), and an end-to-end SSE stream through the
Flask test client (not just the bus).

Background threads are enabled here on purpose (MULTIPROFILE_NO_BACKGROUND
is *removed* by the fixture) — the rest of the suite sets it to keep daemon
threads out of pytest. Each test stops the module singletons afterwards so
no worker/supervisor leaks into other test files.
"""

from __future__ import annotations

import threading
import time

import pytest

from multiprofile.storage import db


def _wait_for(pred, timeout: float = 15.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    raise AssertionError("timed out waiting for condition")


@pytest.fixture()
def bg_app_env(tmp_path, monkeypatch):
    """Isolated XDG + control DB, background services explicitly ENABLED."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.delenv("MULTIPROFILE_NO_BACKGROUND", raising=False)
    monkeypatch.delenv("MULTIPROFILE_SUPERVISOR_ENABLED", raising=False)
    monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(tmp_path / "control.db"))
    db.close()
    from multiprofile.browser.supervisor import stop_supervisor
    from multiprofile.jobs.workers import stop_workers

    stop_workers()
    stop_supervisor()
    yield tmp_path
    stop_workers()
    stop_supervisor()
    db.close()
    db.configure(None)


def _seed_crash_leftovers():
    """Simulate an unclean shutdown: a job stuck 'running' + a profile
    stuck in a transitional state. ``health_check`` is used because its
    handler is side-effect-free (PID aliveness check only, no launch)."""
    from multiprofile.jobs import enqueue

    job_id = enqueue("health_check", "ghost-profile", payload={})
    claimed = db.claim_next_job()
    assert claimed is not None and claimed["id"] == job_id
    db.upsert_profile_runtime("ghost-profile", state="starting")
    return job_id


def test_create_app_runs_startup_sequence(bg_app_env):
    """create_app(): recover() -> workers -> supervisor, no browsers."""
    from multiprofile.api.server import create_app
    from multiprofile.browser.supervisor import get_supervisor
    from multiprofile.jobs import get_durable_job
    from multiprofile.jobs.workers import get_worker

    job_id = _seed_crash_leftovers()

    app = create_app()

    # 1. recover() ran synchronously during create_app():
    #    attempts 1 -> 2 is recover's signature (a background worker may
    #    already have claimed/finished the job by the time we look).
    job = get_durable_job(job_id)
    assert job["attempts"] >= 2, job  # claim(1) + recover(1); worker may claim again
    assert job["status"] in ("queued", "running", "done"), job
    rt = db.get_profile_runtime("ghost-profile")
    assert rt is not None and rt["state"] == "error", rt

    # 2. durable worker pool started and drains the re-queued job:
    worker = get_worker()
    assert worker is not None and worker.running
    assert _wait_for(
        lambda: get_durable_job(job_id)["status"] == "done"
    ), "worker never finished the re-queued job"

    # 3. browser supervisor started (enabled by default):
    sup = get_supervisor()
    assert sup is not None and sup.running

    # 4. the app serves traffic:
    assert app.test_client().get("/api/health").status_code == 200


def test_create_app_supervisor_disabled_by_config(bg_app_env, monkeypatch):
    """Supervisor must not start when disabled; workers still start."""
    monkeypatch.setenv("MULTIPROFILE_SUPERVISOR_ENABLED", "0")
    from multiprofile.api.server import create_app
    from multiprofile.browser.supervisor import get_supervisor, stop_supervisor
    from multiprofile.jobs.workers import get_worker

    stop_supervisor()
    app = create_app()

    assert get_supervisor() is None
    worker = get_worker()
    assert worker is not None and worker.running
    assert app.test_client().get("/api/health").status_code == 200


def test_create_app_survives_dead_control_db(bg_app_env, tmp_path, monkeypatch):
    """A control DB that cannot open must degrade, never crash the app."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(blocker / "control.db"))
    db.close()

    from multiprofile.api.server import create_app
    from multiprofile.jobs.workers import get_worker

    app = create_app()  # must not raise

    assert app.test_client().get("/api/health").status_code == 200
    assert get_worker() is None  # degraded: no durable jobs without a DB


def test_no_background_flag_skips_services(bg_app_env, monkeypatch):
    """MULTIPROFILE_NO_BACKGROUND=1 keeps daemon threads out of the process."""
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")

    from multiprofile.api.server import create_app
    from multiprofile.browser.supervisor import get_supervisor
    from multiprofile.jobs.workers import get_worker

    app = create_app()

    assert get_worker() is None
    assert get_supervisor() is None
    assert app.test_client().get("/api/health").status_code == 200


def test_sse_stream_end_to_end(bg_app_env):
    """GET /api/events/stream through the Flask app delivers a published
    event in the SSE envelope (not just the in-process bus)."""
    from multiprofile.api import event_bus
    from multiprofile.api.server import create_app

    app = create_app()
    client = app.test_client()
    bus = event_bus.get_bus()

    def _publish_late():
        time.sleep(0.3)
        bus.publish("job.progress", {"job_id": 7, "status": "done"})

    t = threading.Thread(target=_publish_late, daemon=True)
    t.start()
    try:
        resp = client.get("/api/events/stream?max_events=1")
    finally:
        t.join(timeout=5)

    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    body = resp.get_data(as_text=True)
    assert ": connected" in body
    assert '"job.progress"' in body
    assert '"job_id": 7' in body
