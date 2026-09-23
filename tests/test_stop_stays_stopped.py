"""v1.8.0: stop must stay stopped.

An explicit Stop must record a user-stop that the supervisor never
restarts. The supervisor may restart only an unexpected exit from RUNNING.
"""
from __future__ import annotations

import subprocess

import pytest

from multiprofile.api import events
from multiprofile.browser import launcher
from multiprofile.browser.supervisor import Supervisor, pid_alive
from multiprofile.storage import db
from multiprofile.storage.profiles import create_profile, set_runtime


@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    """Isolated control DB + XDG data dir (same pattern as the v1.7.0 suite)."""
    db_path = tmp_path / "control.db"
    monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(db_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    db.close()
    events.clear_subscribers()
    yield tmp_path
    db.close()
    db.configure(None)
    events.clear_subscribers()


def test_stop_then_supervisor_tick_does_not_relaunch(ctl):
    """Stop a running profile, run one supervisor tick, assert no relaunch."""
    proc = subprocess.Popen(["sleep", "60"])
    try:
        assert pid_alive(proc.pid)
        profile = create_profile({"name": "stop-test"})
        profile_id = profile["id"]
        set_runtime(profile_id, "running", proc.pid)
        db.upsert_profile_runtime(profile_id, state="starting")
        db.upsert_profile_runtime(profile_id, state="running", pid=proc.pid, cdp_port=19999)
        rt = db.get_profile_runtime(profile_id)
        assert rt["state"] == "running"

        launches: list[str] = []
        sup = Supervisor(
            interval_s=60,
            max_restart_attempts=3,
            backoff_base_s=0.01,
            launch_fn=lambda pid: launches.append(pid) or {"ok": True, "pid": 424242},
            tile_fn=lambda: {"ok": True, "results": []},
        )

        # The explicit user stop: STOPPING -> SIGTERM -> wait -> STOPPED.
        result = launcher.stop_profile(profile_id)
        assert result["ok"] is True
        assert not pid_alive(proc.pid), "the stopped pid must actually be dead"
        rt = db.get_profile_runtime(profile_id)
        assert rt["state"] == "stopped"
        assert rt["note"] == "user stop"

        # One supervisor tick must NOT treat the stop as a crash.
        summary = sup.supervise_once()
        assert launches == [], f"user-stopped profile was relaunched: {launches}"
        assert summary["restarted"] == []
        rt = db.get_profile_runtime(profile_id)
        assert rt["state"] == "stopped"
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def test_stop_records_user_stop_note(ctl):
    """stop_profile leaves an explicit user-stop audit trail in the state machine."""
    proc = subprocess.Popen(["sleep", "30"])
    try:
        profile = create_profile({"name": "stop-note-test"})
        profile_id = profile["id"]
        set_runtime(profile_id, "running", proc.pid)
        db.upsert_profile_runtime(profile_id, state="starting")
        db.upsert_profile_runtime(profile_id, state="running", pid=proc.pid)

        result = launcher.stop_profile(profile_id)
        assert result["ok"] is True
        rt = db.get_profile_runtime(profile_id)
        assert rt["state"] == "stopped"
        assert (rt["note"] or "").startswith("user stop")
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def test_supervisor_still_restarts_unexpected_crash(ctl):
    """Guard rail: a genuine crash (dead pid, still RUNNING) must still restart."""
    proc = subprocess.Popen(["sleep", "0.3"])
    profile = create_profile({"name": "crash-test"})
    profile_id = profile["id"]
    set_runtime(profile_id, "running", proc.pid)
    db.upsert_profile_runtime(profile_id, state="starting")
    db.upsert_profile_runtime(profile_id, state="running", pid=proc.pid, cdp_port=19998)
    proc.wait()  # crash: pid dead while state stays running
    assert not pid_alive(proc.pid)

    launches: list[str] = []

    def fake_launch(pid, **kwargs):
        launches.append(pid)
        db.set_profile_state(pid, "running", force=True)
        db.upsert_profile_runtime(pid, pid=424243, cdp_port=19998)
        return {"ok": True, "pid": 424243}

    sup = Supervisor(
        interval_s=60,
        max_restart_attempts=1,
        backoff_base_s=0.01,
        launch_fn=fake_launch,
        tile_fn=lambda: {"ok": True, "results": []},
    )
    summary = sup.supervise_once()
    assert launches == [profile_id], "unexpected crash must still be restarted"
    assert summary["restarted"] == [profile_id]
    assert db.get_profile_runtime(profile_id)["state"] == "running"
