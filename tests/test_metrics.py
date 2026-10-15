"""Where the metrics module believes runtime state lives.

Written after running the desktop console and finding its landing screen
disagreeing with itself.
"""
from __future__ import annotations

from multiprofile import metrics


def _mark_running(db, profile_id: str, *, pid: int) -> None:
    """Drive a profile to RUNNING through the legal transitions.

    `stopped -> running` is refused by the state machine; the path is
    `stopped -> starting -> running`. Going through it rather than forcing it
    keeps the fixture honest about what the product actually does.
    """
    db.upsert_profile_runtime(profile_id, pid=pid)
    db.set_profile_state(profile_id, "starting")
    db.set_profile_state(profile_id, "running")
    db.upsert_profile_runtime(profile_id, pid=pid)




class TestRuntimeStateComesFromTheDatabase:
    """Metrics had a second, stale source of truth for "is this running".

    `running_profile_ids` and `_profile_pid` read
    `$XDG_CACHE_HOME/multiprofile/runtime.json`. The launcher writes that file
    as well as the database, so both stores are live -- but the JSON one is
    never pruned, while the database is pruned with the profile and every
    transition goes through the state machine. Found by running the desktop
    console, whose landing screen showed both of these at once:

        Overview   Running 0
        Resources  "42 profiles are marked running, but no live process
                    could be sampled."

    On that machine the JSON held 148 entries, 43 marked running, many for
    profiles deleted long ago, while `profiles_runtime` correctly held one.
    """

    def test_running_ids_come_from_the_runtime_table(self, tmp_path, monkeypatch):
        from multiprofile.storage import db

        monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(tmp_path / "c.db"))
        db.close()
        try:
            _mark_running(db, "p_running", pid=4242)
            db.upsert_profile_runtime("p_stopped", pid=None)
            assert metrics.running_profile_ids() == ["p_running"]
        finally:
            db.close()

    def test_a_stale_runtime_json_is_ignored(self, tmp_path, monkeypatch):
        """The regression itself: the unpruned file must not decide the count."""
        import json as _json

        from multiprofile.storage import db
        from multiprofile.storage.xdg import cache_dir

        monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(tmp_path / "c.db"))
        db.close()
        try:
            stale = {f"ghost_{i}": {"status": "running", "pid": 999000 + i}
                     for i in range(43)}
            (cache_dir() / "runtime.json").write_text(_json.dumps(stale))
            _mark_running(db, "p_real", pid=1234)

            ids = metrics.running_profile_ids()
            assert ids == ["p_real"], ids
            assert not any(i.startswith("ghost_") for i in ids)

            summary = metrics.summary_totals()
            assert summary["running_profiles"] == 1, summary["running_profiles"]
        finally:
            db.close()

    def test_the_pid_comes_from_the_runtime_table(self, tmp_path, monkeypatch):
        from multiprofile.storage import db

        monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(tmp_path / "c.db"))
        db.close()
        try:
            _mark_running(db, "p", pid=777001)
            state, pid = metrics._profile_pid("p")
            assert state == "running"
            assert pid == 777001
        finally:
            db.close()

    def test_an_unknown_profile_is_unknown_not_invented(self, tmp_path, monkeypatch):
        from multiprofile.storage import db

        monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(tmp_path / "c.db"))
        db.close()
        try:
            state, pid = metrics._profile_pid("never-existed")
            assert state == "unknown"
            assert pid is None
        finally:
            db.close()
