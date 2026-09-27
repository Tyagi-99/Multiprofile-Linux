"""Tests for v1.7.0 workstream 1: storage truth + durable jobs + supervisor.

Isolation: every test gets its own SQLite control DB (MULTIPROFILE_CONTROL_DB
override) and its own XDG_DATA_HOME (audit log), so nothing touches the real
user data dir.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
import types

import pytest

from multiprofile.api import events
from multiprofile.audit import read_audit
from multiprofile.browser.supervisor import Supervisor, pid_alive
from multiprofile.jobs import enqueue, get_durable_job
from multiprofile.jobs.handlers import handle_park
from multiprofile.jobs.workers import JobWorker
from multiprofile.storage import db
from multiprofile.storage.state import (
    InvalidStateTransition,
    ProfileState,
    validate_transition,
)

# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    """Isolated control DB + XDG data dir."""
    db_path = tmp_path / "control.db"
    monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(db_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    db.close()
    events.clear_subscribers()
    yield tmp_path
    db.close()
    db.configure(None)
    events.clear_subscribers()


def mark_running(profile_id: str, pid: int, cdp_port: int = 19999) -> None:
    db.upsert_profile_runtime(profile_id, state="starting")
    db.upsert_profile_runtime(profile_id, state="running", pid=pid, cdp_port=cdp_port)


def wait_for(pred, timeout: float = 10.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = pred()
        if val:
            return val
        time.sleep(interval)
    raise AssertionError(f"timed out waiting after {timeout}s")


# ------------------------------------------------------------- state machine


def test_state_machine_valid_transitions(ctl):
    validate_transition("p", "stopped", "starting")
    validate_transition("p", "starting", "running")
    validate_transition("p", "running", "stopping")
    validate_transition("p", "stopping", "stopped")
    validate_transition("p", "running", "error")       # browser died
    validate_transition("p", "starting", "error")      # launch failed
    validate_transition("p", "stopping", "error")      # stop failed
    validate_transition("p", "error", "starting")      # explicit restart/recover
    validate_transition("p", "error", "stopped")       # explicit acknowledgement
    validate_transition("p", "running", "starting")    # explicit relaunch
    validate_transition("p", "running", "running")     # idempotent no-op


def test_state_machine_invalid_transitions_raise(ctl):
    for src, dst in [
        ("stopped", "running"),
        ("stopped", "stopping"),
        ("stopped", "error"),
        ("running", "stopped"),
        ("error", "running"),
        ("error", "stopping"),
        ("stopping", "starting"),
        ("starting", "stopping"),
    ]:
        with pytest.raises(InvalidStateTransition):
            validate_transition("p", src, dst)


def test_state_machine_unknown_state(ctl):
    with pytest.raises(ValueError):
        validate_transition("p", "stopped", "flying")


def test_db_set_profile_state_enforces_machine(ctl):
    db.upsert_profile_runtime("p1", state="starting")
    change = db.set_profile_state("p1", ProfileState.RUNNING)
    assert change == {"old": "starting", "new": "running"}
    with pytest.raises(InvalidStateTransition):
        db.set_profile_state("p1", "stopped")  # running -> stopped is invalid


# --------------------------------------------------------------------- WAL


def test_wal_mode_is_on(ctl):
    assert db.journal_mode() == "wal"


def test_schema_tables_exist(ctl):
    db.journal_mode()  # ensure the schema is created first
    conn = sqlite3.connect(db.db_path())
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"profiles_runtime", "jobs", "schema_version"} <= tables
    cols = {r[1] for r in sqlite3.connect(db.db_path()).execute(
        "PRAGMA table_info(profiles_runtime)")}
    for expected in ("profile_id", "state", "cdp_port", "pid", "device_preset_id",
                     "network_config_id", "started_at", "updated_at", "last_heartbeat"):
        assert expected in cols, f"missing column {expected}"
    job_cols = {r[1] for r in sqlite3.connect(db.db_path()).execute(
        "PRAGMA table_info(jobs)")}
    for expected in ("id", "job_type", "profile_id", "payload_json", "status",
                     "attempts", "max_attempts", "created_at", "updated_at",
                     "result_json", "error"):
        assert expected in job_cols, f"missing job column {expected}"


# ------------------------------------------------------------- durable jobs


def test_enqueue_rejects_unknown_type(ctl):
    with pytest.raises(ValueError):
        enqueue("teleport", "p1")


def test_enqueue_durable_restart(ctl):
    """enqueue -> worker1 claims (crash) -> recover -> worker2 finishes same job."""
    job_id = enqueue("health_check", "p_hc", {"probe": True}, max_attempts=5)

    # Simulate a crash: claim the job, then "die" without finishing it.
    claimed = db.claim_next_job()
    assert claimed is not None and claimed["id"] == job_id
    assert claimed["status"] == "running"
    worker1 = JobWorker(poll_interval_s=0.05).start()
    time.sleep(0.2)
    worker1.stop()
    assert get_durable_job(job_id)["status"] == "running"  # still claimed

    recovered = db.recover()
    assert recovered["recovered_jobs"] == 1
    row = get_durable_job(job_id)
    assert row["status"] == "queued"
    assert row["attempts"] == 2  # claim(1) + recover(1)

    worker2 = JobWorker(poll_interval_s=0.05).start()
    try:
        done = wait_for(lambda: get_durable_job(job_id)["status"] == "done")
    finally:
        worker2.stop()
    assert done
    final = get_durable_job(job_id)
    assert final["id"] == job_id  # same job picked up, never lost
    assert final["payload"]["probe"] is True


def test_recover_marks_stuck_profiles_error(ctl):
    db.upsert_profile_runtime("p_stuck", state="starting", pid=4242)
    recovered = db.recover()
    assert recovered["recovered_profiles"] == ["p_stuck"]
    rt = db.get_profile_runtime("p_stuck")
    assert rt["state"] == "error"
    assert "restart" in (rt["note"] or "")
    entries = [e for e in read_audit(limit=50) if e["action"] == "profile_state_recovered"]
    assert entries and entries[0]["profileId"] == "p_stuck"


def test_recover_exhausted_job_fails(ctl):
    job_id = enqueue("launch", "p_x", max_attempts=1)
    db.claim_next_job()  # attempts -> 1 == max_attempts
    recovered = db.recover()
    assert recovered["failed_jobs"] == 1
    assert get_durable_job(job_id)["status"] == "failed"


# --------------------------------------------------------------- supervisor


def test_supervisor_detects_dead_pid(ctl):
    proc = subprocess.Popen(["sleep", "0.3"])
    mark_running("p_dead", proc.pid)
    proc.wait()  # now definitely dead
    assert not pid_alive(proc.pid)

    q = events.subscribe()
    sup = Supervisor(
        interval_s=60,
        max_restart_attempts=1,
        backoff_base_s=0.01,
        launch_fn=lambda pid: {"ok": False, "error": "no chrome in test"},
    )
    summary = sup.supervise_once()

    assert summary["dead"] == ["p_dead"]
    rt = db.get_profile_runtime("p_dead")
    assert rt["state"] == "error"
    assert "restart retries exhausted" in (rt["note"] or "")
    entries = [e for e in read_audit(limit=50) if e["action"] == "supervisor_browser_died"]
    assert entries and entries[0]["profileId"] == "p_dead"

    statuses = []
    while not q.empty():
        statuses.append(q.get_nowait())
    assert any(
        t == "profile.status" and p["profile_id"] == "p_dead" and p["new_state"] == "error"
        for t, p in statuses
    ), "expected profile.status event for the dead browser"


def test_supervisor_restart_retiles_grid(ctl):
    proc = subprocess.Popen(["sleep", "0.3"])
    mark_running("p_rs", proc.pid)
    proc.wait()

    tiled = []

    def fake_launch(profile_id, **kwargs):
        # Faithful mock: the real launcher records running state itself.
        db.set_profile_state(profile_id, "running", force=True)
        db.upsert_profile_runtime(profile_id, pid=999999, cdp_port=19998)
        return {"ok": True, "pid": 999999, "cdpPort": 19998}

    sup = Supervisor(
        interval_s=60,
        max_restart_attempts=3,
        backoff_base_s=0.01,
        launch_fn=fake_launch,
        tile_fn=lambda: tiled.append("tiled") or {"ok": True, "results": []},
    )
    summary = sup.supervise_once()

    assert summary["restarted"] == ["p_rs"]
    assert tiled == ["tiled"], "re-tile must run after a successful restart"
    rt = db.get_profile_runtime("p_rs")
    assert rt["state"] == "running"
    assert rt["pid"] == 999999


def test_supervisor_retry_bounded(ctl):
    proc = subprocess.Popen(["sleep", "0.2"])
    mark_running("p_b", proc.pid)
    proc.wait()

    calls = []

    sup = Supervisor(
        interval_s=60,
        max_restart_attempts=3,
        backoff_base_s=0.01,
        launch_fn=lambda pid: calls.append(pid) or {"ok": False, "error": "boom"},
        tile_fn=lambda: (_ for _ in ()).throw(AssertionError("tile must not run on failure")),
    )
    sup.supervise_once()
    assert calls == ["p_b"] * 3, "exactly max_restart_attempts launches, never infinite"
    rt = db.get_profile_runtime("p_b")
    assert rt["state"] == "error"
    assert "boom" in (rt["note"] or "")


def test_supervisor_ignores_live_pid(ctl):
    proc = subprocess.Popen(["sleep", "30"])
    try:
        mark_running("p_live", proc.pid)
        sup = Supervisor(interval_s=60, launch_fn=lambda pid: {"ok": True})
        summary = sup.supervise_once()
        assert summary["alive"] == 1
        assert summary["dead"] == []
        rt = db.get_profile_runtime("p_live")
        assert rt["state"] == "running"
        assert rt["last_heartbeat"]
    finally:
        proc.terminate()
        proc.wait()


def test_supervisor_start_stop_loop(ctl):
    sup = Supervisor(interval_s=0.05, backoff_base_s=0.01)
    sup.start()
    assert sup.running
    time.sleep(0.2)
    sup.stop()
    assert not sup.running


# -------------------------------------------------- concurrent launch bound


def test_concurrent_launches_bounded(ctl):
    in_flight = 0
    max_seen = 0
    lock = threading.Lock()

    def slow_launch(profile_id, **kwargs):
        nonlocal in_flight, max_seen
        with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        try:
            time.sleep(0.25)
        finally:
            with lock:
                in_flight -= 1
        return {"ok": True, "pid": 1000, "cdpPort": 20000}

    for i in range(4):
        enqueue("launch", f"p_c{i}", max_attempts=1)

    worker = JobWorker(
        max_workers=4,
        max_concurrent_launches=2,
        poll_interval_s=0.02,
        launch_fn=slow_launch,
    ).start()
    try:
        wait_for(
            lambda: all(
                get_durable_job(j)["status"] == "done" for j in range(1, 5)
            ),
            timeout=15.0,
        )
    finally:
        worker.stop()

    assert max_seen <= 2, f"launch semaphore violated: {max_seen} concurrent"
    assert max_seen >= 1


# ------------------------------------------------------------------ events


def test_events_bus_pub_sub(ctl):
    q = events.subscribe()
    seen = []
    unsub = events.subscribe_fn(lambda t, p: seen.append((t, p)))
    events.publish("profile.status", {"profile_id": "p1", "new_state": "running"})
    etype, payload = q.get(timeout=2)
    assert etype == "profile.status"
    assert payload["profile_id"] == "p1"
    assert seen == [("profile.status", {"profile_id": "p1", "new_state": "running"})]
    unsub()
    assert events.subscriber_count() == 1


def test_job_progress_events_published(ctl):
    q = events.subscribe()
    job_id = enqueue("health_check", "p_ev", max_attempts=1)
    worker = JobWorker(poll_interval_s=0.02).start()
    try:
        wait_for(lambda: get_durable_job(job_id)["status"] == "done")
    finally:
        worker.stop()
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    kinds = {(t, p.get("status")) for t, p in got if t == "job.progress"}
    assert ("job.progress", "running") in kinds
    assert ("job.progress", "done") in kinds


# --------------------------------------------------------------------- park


def test_park_handler_contract(ctl):
    """park job calls park_profile(profile_id) -> bool (Workstream 2 interface)."""
    fake = types.ModuleType("multiprofile.parking")
    calls = []
    fake.park_profile = lambda profile_id: calls.append(profile_id) or True
    sys.modules["multiprofile.parking"] = fake
    try:
        record = {"id": 1, "job_type": "park", "profile_id": "p_park", "payload": {}}
        from multiprofile.jobs.handlers import HandlerContext

        result = handle_park(record, HandlerContext())
        assert result == {"ok": True, "parked": True}
        assert calls == ["p_park"]
    finally:
        sys.modules.pop("multiprofile.parking", None)


def test_park_handler_missing_module_fails_clearly(ctl):
    """Without multiprofile.parking the job fails with a clear error (no crash)."""
    fake = types.ModuleType("multiprofile.parking")  # no park_profile attribute
    sys.modules["multiprofile.parking"] = fake
    try:
        record = {"id": 2, "job_type": "park", "profile_id": "p_park2", "payload": {}}
        from multiprofile.jobs.handlers import HandlerContext, JobHandlerError

        with pytest.raises(JobHandlerError, match="park"):
            handle_park(record, HandlerContext())
    finally:
        sys.modules.pop("multiprofile.parking", None)


def test_park_handler_against_real_parking_module(ctl):
    """Contract check against the real multiprofile.parking (Workstream 2).

    park_profile(profile_id) -> bool; a refused park (unknown profile) fails
    the job cleanly so the worker can retry it.
    """
    import inspect

    from multiprofile import parking
    from multiprofile.jobs.handlers import HandlerContext, JobHandlerError

    assert str(inspect.signature(parking.park_profile)) == "(profile_id: 'str') -> 'bool'"
    record = {"id": 4, "job_type": "park", "profile_id": "no_such_profile_xyz", "payload": {}}
    with pytest.raises(JobHandlerError, match="park refused"):
        handle_park(record, HandlerContext())


def test_park_handler_rejects_non_bool(ctl):
    fake = types.ModuleType("multiprofile.parking")
    fake.park_profile = lambda profile_id: "yes"
    sys.modules["multiprofile.parking"] = fake
    try:
        record = {"id": 3, "job_type": "park", "profile_id": "p_park3", "payload": {}}
        from multiprofile.jobs.handlers import HandlerContext, JobHandlerError

        with pytest.raises(JobHandlerError, match="bool"):
            handle_park(record, HandlerContext())
    finally:
        sys.modules.pop("multiprofile.parking", None)


def test_supervisor_crash_loop_is_bounded(ctl):
    """A browser that dies seconds after every relaunch must not be restarted forever.

    Regression: the restart budget lived in a local loop variable, so it only
    bounded consecutive *launch failures*. A launch that reported ok and then
    died on the next tick started a fresh budget every time — the supervisor
    relaunched a crash-looping browser without limit (observed in the field as
    "relaunch ... succeeded (attempt 1)" repeating every ~11s).
    """
    now = [1000.0]
    launches = []

    def fake_launch(profile_id):
        # Reports success, exactly like the real launcher did in the field.
        db.set_profile_state(profile_id, "running", force=True)
        db.upsert_profile_runtime(profile_id, pid=4242, cdp_port=19997)
        launches.append(profile_id)
        return {"ok": True, "pid": 4242, "cdpPort": 19997}

    sup = Supervisor(
        interval_s=60,
        max_restart_attempts=3,
        backoff_base_s=0.01,
        restart_stability_s=60.0,
        launch_fn=fake_launch,
        tile_fn=lambda: {"ok": True, "results": []},
        pid_alive_fn=lambda pid: False,  # every relaunched browser is already dead
        clock=lambda: now[0],
    )

    mark_running("p_loop", 4242)
    # Ten watchdog ticks, each 11s apart — inside the 60s stability window.
    for _ in range(10):
        sup.supervise_once()
        now[0] += 11.0

    assert len(launches) == 3, (
        f"expected at most max_restart_attempts=3 relaunches across the episode, "
        f"got {len(launches)}"
    )
    rt = db.get_profile_runtime("p_loop")
    assert rt["state"] == "error"
    assert "did not keep the browser alive" in (rt["note"] or "")
    exhausted = [e for e in read_audit(limit=100) if e["action"] == "supervisor_restart_exhausted"]
    assert exhausted, "crash loop must be audited as restart-exhausted"


def test_supervisor_stability_window_resets_budget(ctl):
    """Crashes separated by a stable run each get the full restart budget."""
    now = [1000.0]
    launches = []
    alive = [False]

    def fake_launch(profile_id):
        db.set_profile_state(profile_id, "running", force=True)
        db.upsert_profile_runtime(profile_id, pid=4243, cdp_port=19996)
        launches.append(now[0])
        alive[0] = True
        return {"ok": True, "pid": 4243, "cdpPort": 19996}

    sup = Supervisor(
        interval_s=60,
        max_restart_attempts=3,
        backoff_base_s=0.01,
        restart_stability_s=60.0,
        launch_fn=fake_launch,
        tile_fn=lambda: {"ok": True, "results": []},
        pid_alive_fn=lambda pid: alive[0],
        clock=lambda: now[0],
    )

    mark_running("p_stable", 4243)
    # Four crashes, each after a long healthy run: one restart every time.
    for _ in range(4):
        alive[0] = False
        sup.supervise_once()          # sees the death, relaunches
        now[0] += 3600.0              # an hour of uptime proves stability
        sup.supervise_once()          # alive tick: budget resets

    assert len(launches) == 4, "a stable run must restore the full restart budget"
    rt = db.get_profile_runtime("p_stable")
    assert rt["state"] == "running"
