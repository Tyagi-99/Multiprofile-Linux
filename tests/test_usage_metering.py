"""Measuring what the fleet did.

Nothing recorded usage before this. `storage/db.py` made `client_id` a column
with the note that "billing has to aggregate by it", and there was nothing to
aggregate: no runtime, no session history, no way to answer what a workspace
costs or whether a cap is generous.

The tests below are the properties that make a meter worth drawing a bill
from. Two matter more than the rest:

**It cannot be bypassed.** Sessions open and close inside
`db.set_profile_state`, the transition every caller already routes through.
Metering from the launcher, the supervisor and the job handlers separately
would mean three places that must each remember, and the one that forgets
bills short -- the same shape as F1, where a control existed but sat off the
path that mattered.

**It rounds against its author.** A session abandoned by a crash closes at the
profile's last heartbeat, never at the time the process next starts: the
machine may have been off for days. Charging for them is the more profitable
reading and the dishonest one.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from multiprofile import usage
from multiprofile.storage import db


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


@pytest.fixture
def client():
    from multiprofile.api.server import create_app

    return create_app().test_client()


def _profile(name="P", **fields):
    from multiprofile.storage.profiles import create_profile

    return create_profile({"name": name, **fields})["id"]


def _run(profile_id: str, seconds: float = 0.0) -> None:
    """A full lifecycle through the real state machine."""
    db.set_profile_state(profile_id, "starting")
    db.set_profile_state(profile_id, "running")
    if seconds:
        time.sleep(seconds)
    db.set_profile_state(profile_id, "stopping")
    db.set_profile_state(profile_id, "stopped")


# --------------------------------------------------------------------------
# 1. It measures, at all
# --------------------------------------------------------------------------

def test_running_a_profile_records_a_session():
    pid = _profile()
    assert db.count_usage_sessions() == 0
    _run(pid)
    rows = db.usage_rows()
    assert len(rows) == 1
    assert rows[0]["profile_id"] == pid
    assert rows[0]["end_reason"] == "stopped"
    assert rows[0]["open"] is False


def test_a_running_profile_has_an_open_session_with_time_so_far():
    """"Currently running" must be visible, not missing until it stops."""
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    row = db.usage_rows()[0]
    assert row["open"] is True
    assert row["ended_at"] is None
    assert row["seconds"] >= 0


def test_the_duration_is_the_time_spent_running():
    pid = _profile()
    _run(pid, seconds=1.1)
    assert db.usage_rows()[0]["seconds"] >= 1


def test_a_profile_that_errors_records_why():
    """"stopped" and "crashed" are the same duration and different facts."""
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    db.set_profile_state(pid, "error")
    assert db.usage_rows()[0]["end_reason"] == "error"


# --------------------------------------------------------------------------
# 2. It cannot be bypassed or double-counted
# --------------------------------------------------------------------------

def test_metering_sits_on_the_transition_not_on_the_callers():
    """Every caller reaches the meter because they reach the state machine.

    Asserted through db.set_profile_state directly rather than through the
    launcher, because that is the claim: a caller that never heard of usage
    still gets metered.
    """
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    assert db.count_usage_sessions() == 1


def test_entering_running_twice_does_not_bill_the_minute_twice():
    """A forced or repeated transition must not open a second session."""
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    db.set_profile_state(pid, "running", force=True)
    db.set_profile_state(pid, "running", force=True)
    assert db.count_usage_sessions() == 1


def test_a_transition_that_does_not_move_is_not_a_session():
    pid = _profile()
    db.set_profile_state(pid, "stopped", force=True)
    db.set_profile_state(pid, "stopped", force=True)
    assert db.count_usage_sessions() == 0


def test_closing_a_profile_that_was_never_running_records_nothing():
    pid = _profile()
    assert db.close_usage_session(pid) == 0
    assert db.count_usage_sessions() == 0


def test_a_metering_failure_does_not_break_the_state_change(monkeypatch):
    """The record of what the browser is doing outranks the record of cost."""
    pid = _profile()

    def boom(*a, **k):
        raise RuntimeError("meter is on fire")

    monkeypatch.setattr(db, "_meter_transition", boom)
    db.set_profile_state(pid, "starting")
    change = db.set_profile_state(pid, "running")
    assert change["new"] == "running"
    assert db.get_profile_runtime(pid)["state"] == "running"


# --------------------------------------------------------------------------
# 3. It rounds against its author
# --------------------------------------------------------------------------

def test_a_session_abandoned_by_a_crash_closes_at_the_last_heartbeat():
    """Not at "now": the machine may have been off for three days."""
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    db.record_heartbeat(pid)
    heartbeat = db.get_profile_runtime(pid)["last_heartbeat"]

    closed = db.close_abandoned_sessions()
    assert closed == 1
    row = db.usage_rows()[0]
    assert row["end_reason"] == "abandoned"
    assert row["ended_at"] == heartbeat
    assert row["open"] is False


def test_an_abandoned_session_with_no_heartbeat_bills_nothing():
    """The start is the honest floor when nothing says it lived longer."""
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    db.close_abandoned_sessions()
    assert db.usage_rows()[0]["seconds"] == 0


def test_a_stale_heartbeat_from_an_earlier_run_does_not_shorten_or_extend():
    """A heartbeat older than this session's start belongs to a previous one."""
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    conn = db._connect()
    with conn:
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        conn.execute(
            "UPDATE profiles_runtime SET last_heartbeat = ? WHERE profile_id = ?",
            (old, pid),
        )
    db.close_abandoned_sessions()
    row = db.usage_rows()[0]
    assert row["seconds"] == 0, "a stale heartbeat must not produce a negative span"
    assert row["ended_at"] == row["started_at"]


def test_recovery_closes_abandoned_sessions():
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    result = db.recover()
    assert result["closed_usage_sessions"] == 1
    assert not any(r["open"] for r in db.usage_rows())


def test_a_clock_that_goes_backwards_cannot_produce_negative_time():
    pid = _profile()
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert db.close_usage_session(pid, at=past) == 0


# --------------------------------------------------------------------------
# 4. Attribution
# --------------------------------------------------------------------------

def test_usage_is_attributed_to_the_workspace_that_owned_it_at_the_time():
    """A profile can change hands; a bill must say who owned it while it ran."""
    from multiprofile.storage.profiles import update_profile

    pid = _profile(clientId="client_one")
    _run(pid)
    update_profile(pid, {"clientId": "client_two"})
    _run(pid)

    by_client = {b["clientId"]: b for b in usage.summary("all")["byClient"]}
    assert by_client["client_one"]["sessions"] == 1
    assert by_client["client_two"]["sessions"] == 1


def test_usage_for_a_deleted_profile_still_counts_towards_the_total():
    """Dropping it would make the totals stop adding up, which a usage report
    may not do -- it would silently discount whatever was deleted."""
    from multiprofile.storage.profiles import purge_profile, trash_profile

    pid = _profile("Gone")
    _run(pid, seconds=1.1)
    trash_profile(pid)
    purge_profile(pid)

    report = usage.summary("all")
    assert report["total"]["sessions"] == 1
    assert report["total"]["seconds"] >= 1
    row = next(b for b in report["byProfile"] if b["profileId"] == pid)
    assert row["name"] is None, "the name is gone; the usage is not"


# --------------------------------------------------------------------------
# 5. Windows
# --------------------------------------------------------------------------

@pytest.mark.parametrize("period", ["day", "week", "month", "all"])
def test_every_period_is_understood(period):
    assert usage.summary(period)["period"] == period


def test_an_unknown_period_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        usage.period_start("fortnight")


def test_all_has_no_lower_bound():
    assert usage.period_start("all") is None


def test_a_session_outside_the_window_is_excluded():
    pid = _profile()
    _run(pid)
    conn = db._connect()
    with conn:
        long_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn.execute("UPDATE usage_sessions SET started_at = ?", (long_ago,))
    assert usage.summary("day")["total"]["sessions"] == 0
    assert usage.summary("all")["total"]["sessions"] == 1


# --------------------------------------------------------------------------
# 6. The API
# --------------------------------------------------------------------------

def test_the_summary_endpoint_reports_the_measured_totals(client):
    pid = _profile("Metered")
    _run(pid, seconds=1.1)
    body = client.get("/api/usage?period=day").get_json()
    assert body["ok"] is True
    assert body["total"]["sessions"] == 1
    assert body["total"]["seconds"] >= 1
    assert body["byProfile"][0]["name"] == "Metered"


def test_the_sessions_endpoint_shows_the_rows_behind_the_total(client):
    """A total nobody can drill into is a number to be taken on trust."""
    pid = _profile("Metered")
    _run(pid)
    body = client.get("/api/usage/sessions?period=day").get_json()
    assert body["count"] == 1
    assert body["items"][0]["profileName"] == "Metered"
    assert body["items"][0]["profile_id"] == pid


def test_a_bad_period_is_a_400_not_a_silent_default(client):
    r = client.get("/api/usage?period=fortnight")
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "period"


def test_an_empty_meter_reports_zero_rather_than_failing(client):
    body = client.get("/api/usage").get_json()
    assert body["total"] == {"sessions": 0, "seconds": 0, "open": 0}
    assert body["byProfile"] == []


# --------------------------------------------------------------------------
# 7. Presentation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("seconds", "text"), [
    (0, "0s"),
    (45, "45s"),
    (90, "1m 30s"),
    (3600, "1h 00m"),
    (11_580, "3h 13m"),
    (-5, "0s"),
])
def test_durations_read_as_durations(seconds, text):
    assert usage.format_seconds(seconds) == text
