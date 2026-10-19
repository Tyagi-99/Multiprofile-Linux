"""The operator table gets runtime truth in the row, not just a state word.

_with_launch_state projects the launch error, the proxy verdict and the last
launch onto each profile row so the console can show them without opening
each profile. The launch error in particular must survive the trip: a blocked
launch that renders as a bare "error" costs the operator the one sentence that
tells them what to fix.
"""
from __future__ import annotations

import pytest

from multiprofile.api.server import _operator_state, _with_launch_state, create_app
from multiprofile.browser.runtime_state import (
    LaunchState,
    clear_all_runtimes,
    set_launch_state,
    upsert_runtime,
)


@pytest.fixture()
def xdg_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")


@pytest.fixture()
def clean_runtimes():
    clear_all_runtimes()
    yield
    clear_all_runtimes()


@pytest.fixture()
def client(xdg_isolated):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------


def test_row_carries_the_launch_error_verbatim(xdg_isolated, clean_runtimes):
    """The GitHub-gateway refusal is the operator's fix. Never swallow it."""
    message = "GitHub Account is not a network gateway"
    set_launch_state(
        "p1",
        LaunchState.LAUNCH_FAILED,
        error=message,
        error_code="github_not_a_gateway",
    )

    [row] = _with_launch_state([{"id": "p1", "name": "One"}])

    assert row["launchError"] == message
    assert row["launchErrorCode"] == "github_not_a_gateway"
    assert row["launchState"] == "LAUNCH_FAILED"


def test_row_carries_the_proxy_verdict_and_egress(xdg_isolated, clean_runtimes):
    upsert_runtime("p1", proxy_ok=True, egress_ip="203.0.113.7", last_proxy_check=1700000000.0)

    [row] = _with_launch_state([{"id": "p1", "name": "One"}])

    assert row["proxyOk"] is True
    assert row["egressIp"] == "203.0.113.7"
    assert row["lastProxyCheck"] == 1700000000.0


def test_unchecked_proxy_is_none_not_false(xdg_isolated, clean_runtimes):
    """null means never probed. The console must not render that as healthy."""
    upsert_runtime("p1", pid=123)

    [row] = _with_launch_state([{"id": "p1", "name": "One"}])

    assert row["proxyOk"] is None
    assert row["egressIp"] is None


def test_failed_proxy_is_false(xdg_isolated, clean_runtimes):
    upsert_runtime("p1", proxy_ok=False)
    [row] = _with_launch_state([{"id": "p1", "name": "One"}])
    assert row["proxyOk"] is False


def test_profile_without_runtime_gets_explicit_nulls(xdg_isolated, clean_runtimes):
    """Every key is always present, so the table never reads undefined."""
    [row] = _with_launch_state([{"id": "never-launched", "name": "Fresh"}])

    for key in (
        "launchState",
        "cdpPort",
        "launchError",
        "launchErrorCode",
        "proxyOk",
        "egressIp",
        "lastProxyCheck",
        "lastLaunch",
    ):
        assert key in row, key
        assert row[key] is None, key


def test_last_launch_comes_from_control_db(xdg_isolated, clean_runtimes):
    from multiprofile.storage import db as control_db

    control_db.upsert_profile_runtime("p1", started_at="2026-09-24T10:00:00Z")

    [row] = _with_launch_state([{"id": "p1", "name": "One"}])

    assert row["lastLaunch"] == "2026-09-24T10:00:00Z"


def test_projection_survives_a_control_db_failure(xdg_isolated, clean_runtimes, monkeypatch):
    """Bookkeeping must never take the profile list down."""
    from multiprofile.storage import db as control_db

    def boom(*a, **k):
        raise RuntimeError("db is gone")

    monkeypatch.setattr(control_db, "list_profile_runtimes", boom)
    set_launch_state("p1", LaunchState.RUNNING)

    [row] = _with_launch_state([{"id": "p1", "name": "One"}])

    assert row["launchState"] == "RUNNING"
    assert row["lastLaunch"] is None


def test_original_profile_dict_is_not_mutated(xdg_isolated, clean_runtimes):
    upsert_runtime("p1", proxy_ok=True)
    original = {"id": "p1", "name": "One"}

    _with_launch_state([original])

    assert original == {"id": "p1", "name": "One"}


def test_no_proxy_password_is_ever_projected(xdg_isolated, clean_runtimes):
    """The row exposes the verdict and the egress IP, never a credential."""
    upsert_runtime("p1", proxy_ok=True, egress_ip="203.0.113.7")

    [row] = _with_launch_state([{"id": "p1", "name": "One"}])

    added = set(row) - {"id", "name"}
    assert not any("pass" in k.lower() or "secret" in k.lower() for k in added), added


# --------------------------------------------------------------------------
# one state vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "launch_state,expected",
    [
        ("RUNNING", "running"),
        ("VERIFIED", "running"),
        ("LAUNCHING", "starting"),
        ("CDP_READY", "starting"),
        ("WINDOW_FOUND", "starting"),
        ("POSITIONING", "starting"),
        ("CREATED", "stopped"),
        ("LAUNCH_FAILED", "error"),
        ("CDP_FAILED", "error"),
        ("WINDOW_NOT_FOUND", "error"),
        ("POSITION_FAILED", "error"),
        ("VERIFY_FAILED", "error"),
    ],
)
def test_every_launch_state_collapses_to_an_operator_word(launch_state, expected):
    assert _operator_state(launch_state) == expected


def test_every_launch_state_member_is_mapped():
    """A new LaunchState must not silently fall through to the raw enum name."""
    for member in LaunchState:
        assert _operator_state(member.value) is not None, member.value


def test_unknown_and_empty_states_are_not_guessed():
    assert _operator_state(None) is None
    assert _operator_state("") is None
    assert _operator_state("NONSENSE") is None


def test_row_state_is_the_operator_word_not_the_enum(xdg_isolated, clean_runtimes):
    set_launch_state("p1", LaunchState.LAUNCH_FAILED, error="nope")
    [row] = _with_launch_state([{"id": "p1", "name": "One"}])
    assert row["state"] == "error"
    # The finer detail is still available for diagnostics.
    assert row["launchState"] == "LAUNCH_FAILED"


def test_parked_wins_over_the_launch_registry(xdg_isolated, clean_runtimes):
    """A parked profile reads parked even with a stale RUNNING in the registry."""
    set_launch_state("p1", LaunchState.RUNNING)
    [row] = _with_launch_state([{"id": "p1", "name": "One", "status": "parked"}])
    assert row["state"] == "parked"


def test_never_launched_profile_is_stopped(xdg_isolated, clean_runtimes):
    [row] = _with_launch_state([{"id": "fresh", "name": "Fresh"}])
    assert row["state"] == "stopped"


def test_a_saved_launch_state_cannot_override_a_stopped_row(xdg_isolated, clean_runtimes):
    """A launchState left inside the profile document is not a running browser.

    The live registry is empty and control.db says stopped. The list used to
    keep the document's RUNNING or VERIFY_FAILED via setdefault, so Stop
    could record stopped and the table still painted running or error.
    """
    from multiprofile.storage import db as control_db

    control_db.upsert_profile_runtime("p1", state="stopped", note="user stop")
    [row] = _with_launch_state([{
        "id": "p1",
        "name": "One",
        "status": "idle",
        "launchState": "RUNNING",
        "cdpPort": 46315,
        "launchError": "leftover",
        "launchErrorCode": "verify",
    }])
    assert row["state"] == "stopped"
    assert row["launchState"] is None
    assert row["cdpPort"] is None
    assert row["launchError"] is None
    assert row["launchErrorCode"] is None


def test_a_saved_verify_failure_does_not_paint_error(xdg_isolated, clean_runtimes):
    from multiprofile.storage import db as control_db

    control_db.upsert_profile_runtime("p1", state="stopped")
    [row] = _with_launch_state([{
        "id": "p1",
        "name": "One",
        "launchState": "VERIFY_FAILED",
    }])
    assert row["state"] == "stopped"
    assert row["launchState"] is None


# --------------------------------------------------------------------------
# through the route
# --------------------------------------------------------------------------


def test_list_route_returns_the_new_fields(client, clean_runtimes):
    created = client.post("/api/profiles", json={"name": "Row test"})
    assert created.status_code == 200, created.data
    pid = created.get_json()["profile"]["id"]

    set_launch_state(pid, LaunchState.LAUNCH_FAILED, error="proxy dead", error_code="proxy_blocked")

    listed = client.get("/api/profiles?limit=25")
    assert listed.status_code == 200
    row = next(r for r in listed.get_json()["items"] if r["id"] == pid)

    assert row["launchError"] == "proxy dead"
    assert row["launchErrorCode"] == "proxy_blocked"
    assert "proxyOk" in row
    assert "lastLaunch" in row
