"""The two runtime stores have to describe the same profiles.

Found by running the desktop console. ``runtime.json`` held 151 entries,
42 marked running, and ``profiles_runtime`` held rows for profiles that had
been deleted. Metrics had already stopped trusting the file. The profile
list, the parking check and the reaper had not, so the file could still
decide that a profile was running after the database said it was not.
"""
from __future__ import annotations

import json

from multiprofile.storage import db
from multiprofile.storage.profiles import (
    create_profile,
    delete_profile,
    get_profile,
    mirror_runtime_cache,
    prune_stale_runtime,
    reap_orphans,
    set_runtime,
    trash_profile,
)
from multiprofile.storage.xdg import runtime_state_path


def _stopped(profile_id: str) -> None:
    """A real stopped row: the state machine has no direct stopped insert."""
    db.set_profile_state(profile_id, "starting")
    db.set_profile_state(profile_id, "stopped")


def test_the_database_wins_over_a_stale_running_flag():
    """The regression: a JSON entry must not outvote the state machine."""
    pid = create_profile({"name": "stale"})["id"]
    _stopped(pid)
    set_runtime(pid, "running", 424242)

    profile = get_profile(pid)
    assert profile is not None
    assert profile["status"] == "idle"
    assert profile["isRunning"] is False

    from multiprofile.parking import is_profile_running

    assert is_profile_running(pid) is False


def test_a_stopped_profile_keeps_a_parked_catalogue_flag():
    """`parked` is not a process state. A stopped row must not wipe it."""
    from multiprofile.storage.profiles import update_profile

    pid = create_profile({"name": "parked"})["id"]
    _stopped(pid)
    update_profile(pid, {"status": "parked"})
    set_runtime(pid, "running", 7)
    profile = get_profile(pid)
    assert profile is not None
    assert profile["status"] == "parked"
    assert profile["isRunning"] is False


def test_a_json_only_run_is_still_visible():
    """Callers that never touch the state machine still record a run.

    The trash guard and the extension manager do this. A database row, when
    one exists, is the record; the absence of one is not a reason to ignore
    the cache.
    """
    pid = create_profile({"name": "json-only"})["id"]
    set_runtime(pid, "running", 424242)
    profile = get_profile(pid)
    assert profile is not None
    assert profile["isRunning"] is True

    from multiprofile.parking import is_profile_running

    assert is_profile_running(pid) is True


def test_a_database_run_is_visible_when_the_cache_says_idle():
    pid = create_profile({"name": "db-run"})["id"]
    db.upsert_profile_runtime(pid, pid=5151)
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    set_runtime(pid, "idle", None)

    profile = get_profile(pid)
    assert profile is not None
    assert profile["status"] == "running"
    assert profile["pid"] == 5151
    assert profile["isRunning"] is True


def test_delete_and_trash_drop_the_runtime_row():
    gone = create_profile({"name": "gone"})["id"]
    _stopped(gone)
    assert db.get_profile_runtime(gone) is not None
    assert delete_profile(gone, purge_storage=False) is not None
    assert db.get_profile_runtime(gone) is None

    trashed = create_profile({"name": "trashed"})["id"]
    db.upsert_profile_runtime(trashed, pid=99)
    db.set_profile_state(trashed, "starting")
    db.set_profile_state(trashed, "running")
    assert trash_profile(trashed) is not None
    assert db.get_profile_runtime(trashed) is None


def test_prune_drops_ghosts_and_rewrites_the_cache_from_the_database():
    live = create_profile({"name": "live"})["id"]
    _stopped(live)
    set_runtime(live, "running", 111)
    set_runtime("ghost_deleted", "running", 222)
    db.set_profile_state("ghost_row", "starting")
    db.set_profile_state("ghost_row", "running")

    result = prune_stale_runtime()
    assert result["runtimeRows"] == 1
    assert result["jsonDropped"] == 1
    assert db.get_profile_runtime("ghost_row") is None
    assert db.get_profile_runtime(live)["state"] == "stopped"

    cache = json.loads(runtime_state_path().read_text(encoding="utf-8"))
    assert "ghost_deleted" not in cache
    assert cache[live]["status"] == "idle"


def test_a_state_change_rewrites_the_cache_entry():
    pid = create_profile({"name": "mirror"})["id"]
    set_runtime(pid, "running", 333)
    _stopped(pid)
    mirror_runtime_cache(pid)
    cache = json.loads(runtime_state_path().read_text(encoding="utf-8"))
    assert cache[pid]["status"] == "idle"


def test_the_reaper_does_not_hide_a_crash_from_the_supervisor(monkeypatch):
    """Supervisor on: a dead pid left in RUNNING stays RUNNING.

    Marking the cache idle here used to make the console say the profile
    had stopped while the supervisor was still going to restart it.
    """
    monkeypatch.setenv("MULTIPROFILE_SUPERVISOR_ENABLED", "1")
    pid = create_profile({"name": "crash"})["id"]
    db.upsert_profile_runtime(pid, pid=2_147_483_600)
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    set_runtime(pid, "running", 2_147_483_600)

    assert reap_orphans() == []
    assert db.get_profile_runtime(pid)["state"] == "running"
    profile = get_profile(pid)
    assert profile is not None
    assert profile["isRunning"] is True


def test_the_reaper_records_the_death_when_nothing_else_will(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_SUPERVISOR_ENABLED", "0")
    pid = create_profile({"name": "unwatched"})["id"]
    db.upsert_profile_runtime(pid, pid=2_147_483_600)
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")

    reaped = reap_orphans()
    assert [item["profileId"] for item in reaped] == [pid]
    assert db.get_profile_runtime(pid)["state"] == "error"
    profile = get_profile(pid)
    assert profile is not None
    assert profile["isRunning"] is False
    assert profile["status"] == "failed"
