"""Deleting a profile, and getting it back.

``DELETE /api/profiles/<id>`` removed the row and then ``rmtree``'d
``chrome-profiles/<id>``. That directory holds the cookies, the logins and
whatever warmed-up session state the profile had accumulated -- in an
anti-detect browser it *is* the asset, and it cannot be recreated by making a
new profile with the same settings, because the point of the old one was that
some site had come to trust it. A confirmation dialog was the only thing
between a misclick and losing it, and the console offers the same button for a
multi-select, so one misclick could take a whole fleet.

Every comparable product (AdsPower, GoLogin, Multilogin) has a trash with
restore. This is that, and the test names below are the behaviours that make
it worth having rather than a second confirmation step:

* the files survive the delete -- if the directory is removed, "restore" gives
  back a profile with no session, which is the part nobody wanted back;
* restore is subject to the fleet cap, because a restore is a create;
* the purge is the only thing that removes files, and it says so;
* an old trash does not grow forever, but nothing is purged early.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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


def _warm_profile(client, name="Warm Account", **fields):
    """A profile with a chrome-profiles directory, as a used one would have."""
    from multiprofile.storage.xdg import data_dir

    body = {"name": name, **fields}
    pid = client.post("/api/profiles", json=body).get_json()["profile"]["id"]
    root = data_dir() / "chrome-profiles" / pid
    root.mkdir(parents=True, exist_ok=True)
    (root / "Cookies").write_bytes(b"session" * 512)
    return pid, root


# --------------------------------------------------------------------------
# 1. The thing that was being destroyed
# --------------------------------------------------------------------------

def test_deleting_a_profile_does_not_remove_its_session_directory(client):
    """The original bug, stated as the behaviour it broke."""
    pid, root = _warm_profile(client)
    assert root.exists()
    r = client.delete(f"/api/profiles/{pid}")
    assert r.status_code == 200
    assert r.get_json()["trashed"] is True
    assert root.exists(), "the session directory is the asset; trashing must keep it"
    assert (root / "Cookies").read_bytes().startswith(b"session")


def test_a_deleted_profile_leaves_the_fleet_listing(client):
    pid, _ = _warm_profile(client)
    client.delete(f"/api/profiles/{pid}")
    items = client.get("/api/profiles").get_json()["items"]
    assert not any(p["id"] == pid for p in items)
    assert client.get(f"/api/profiles/{pid}").status_code == 404


def test_a_deleted_profile_is_in_the_trash_with_what_it_is_holding(client):
    pid, _ = _warm_profile(client)
    client.delete(f"/api/profiles/{pid}")
    body = client.get("/api/trash").get_json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["id"] == pid
    assert item["status"] == "trashed"
    assert item["deletedAt"]
    # Reported because the trash deliberately does not free the disk: an
    # operator deciding whether to empty it needs to know what they get back.
    assert item["diskBytes"] > 0
    assert body["diskBytes"] == item["diskBytes"]


# --------------------------------------------------------------------------
# 2. Restore
# --------------------------------------------------------------------------

def test_a_restored_profile_comes_back_whole(client):
    pid, root = _warm_profile(client, notes="months of cookies", startUrl="https://example.org")
    original = client.get(f"/api/profiles/{pid}").get_json()
    client.delete(f"/api/profiles/{pid}")

    r = client.post(f"/api/profiles/{pid}/restore")
    assert r.status_code == 200
    back = r.get_json()["profile"]
    assert back["id"] == pid
    assert back["notes"] == "months of cookies"
    assert back["startUrl"] == "https://example.org"
    # The identity has to survive, or the restored profile is a new one
    # wearing the old name -- which is exactly what a fresh create already is.
    assert back["seed"] == original["seed"]
    assert back["fingerprint"] == original["fingerprint"]
    assert root.exists()
    assert client.get("/api/trash").get_json()["count"] == 0


def test_a_restored_profile_is_in_the_fleet_again(client):
    pid, _ = _warm_profile(client)
    client.delete(f"/api/profiles/{pid}")
    client.post(f"/api/profiles/{pid}/restore")
    items = client.get("/api/profiles").get_json()["items"]
    assert any(p["id"] == pid for p in items)


def test_restoring_something_that_is_not_in_the_trash_is_a_404(client):
    assert client.post("/api/profiles/profile_nope/restore").status_code == 404


def test_a_restore_cannot_push_the_fleet_over_its_cap(client, monkeypatch):
    """A restore is a create; the cap has to hold or it is not a cap."""
    monkeypatch.setenv("MAX_PROFILES", "3")
    pid, _ = _warm_profile(client, "doomed")
    client.delete(f"/api/profiles/{pid}")
    # Fill the fleet in its place.
    while client.post("/api/profiles", json={"name": "filler"}).status_code == 200:
        pass
    r = client.post(f"/api/profiles/{pid}/restore")
    assert r.status_code == 409
    assert r.get_json()["error"]["code"] == "fleet_full"
    # And it is still in the trash, not lost between the two tables.
    assert any(p["id"] == pid for p in client.get("/api/trash").get_json()["items"])


def test_trashed_profiles_do_not_count_against_the_cap(client, monkeypatch):
    """Otherwise a full fleet could never be trimmed: deleting would not free
    a slot, and creating would stay refused for ever."""
    monkeypatch.setenv("MAX_PROFILES", "3")
    while client.post("/api/profiles", json={"name": "filler"}).status_code == 200:
        pass
    assert client.post("/api/profiles", json={"name": "one more"}).status_code == 409
    victim = client.get("/api/profiles").get_json()["items"][0]["id"]
    client.delete(f"/api/profiles/{victim}")
    assert client.post("/api/profiles", json={"name": "one more"}).status_code == 200


# --------------------------------------------------------------------------
# 3. Purging is the only thing that removes files
# --------------------------------------------------------------------------

def test_purging_one_trashed_profile_removes_its_files(client):
    pid, root = _warm_profile(client)
    client.delete(f"/api/profiles/{pid}")
    assert root.exists()
    r = client.delete(f"/api/trash/{pid}")
    assert r.status_code == 200
    assert not root.exists()
    assert client.get("/api/trash").get_json()["count"] == 0


def test_emptying_the_trash_removes_every_one_of_them(client):
    roots = []
    for i in range(4):
        pid, root = _warm_profile(client, f"p{i}")
        client.delete(f"/api/profiles/{pid}")
        roots.append(root)
    assert client.post("/api/trash/empty").get_json()["purged"] == 4
    assert not any(r.exists() for r in roots)
    assert client.get("/api/trash").get_json()["count"] == 0


def test_purging_something_that_is_not_in_the_trash_is_a_404(client):
    assert client.delete("/api/trash/profile_nope").status_code == 404


def test_a_live_profile_cannot_be_purged_through_the_trash_route(client):
    """The trash routes must not be a second, unconfirmed delete."""
    pid, root = _warm_profile(client)
    assert client.delete(f"/api/trash/{pid}").status_code == 404
    assert root.exists()
    assert client.get(f"/api/profiles/{pid}").status_code == 200


def test_permanent_delete_is_still_available_when_asked_for_explicitly(client):
    pid, root = _warm_profile(client)
    r = client.delete(f"/api/profiles/{pid}?permanent=1")
    assert r.status_code == 200
    assert r.get_json()["permanent"] is True
    assert not root.exists()
    assert client.get("/api/trash").get_json()["count"] == 0


# --------------------------------------------------------------------------
# 4. Retention
# --------------------------------------------------------------------------

def _backdate(profile_id: str, days: int) -> None:
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = db._connect()
    with conn:
        conn.execute("UPDATE profiles_trash SET deleted_at = ? WHERE id = ?", (when, profile_id))


def test_the_trash_purges_itself_after_the_retention_window(client, monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_TRASH_RETENTION_DAYS", "30")
    old, old_root = _warm_profile(client, "old")
    recent, recent_root = _warm_profile(client, "recent")
    client.delete(f"/api/profiles/{old}")
    client.delete(f"/api/profiles/{recent}")
    _backdate(old, 45)
    _backdate(recent, 29)

    body = client.get("/api/trash").get_json()
    ids = {p["id"] for p in body["items"]}
    assert old not in ids, "past the window, should have been purged"
    assert recent in ids, "inside the window, must not be purged early"
    assert not old_root.exists()
    assert recent_root.exists()


def test_retention_can_be_switched_off(client, monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_TRASH_RETENTION_DAYS", "0")
    pid, root = _warm_profile(client)
    client.delete(f"/api/profiles/{pid}")
    _backdate(pid, 9999)
    body = client.get("/api/trash").get_json()
    assert body["retentionDays"] == 0
    assert any(p["id"] == pid for p in body["items"])
    assert root.exists()


# --------------------------------------------------------------------------
# 5. The move is one transaction
# --------------------------------------------------------------------------

def test_a_profile_is_never_in_both_tables_or_neither(client):
    pid, _ = _warm_profile(client)
    client.delete(f"/api/profiles/{pid}")
    assert db.get_profile_row(pid) is None
    assert db.get_trash_row(pid) is not None
    client.post(f"/api/profiles/{pid}/restore")
    assert db.get_profile_row(pid) is not None
    assert db.get_trash_row(pid) is None


def test_trashing_twice_does_not_duplicate_or_lose_the_row(client):
    pid, _ = _warm_profile(client)
    assert client.delete(f"/api/profiles/{pid}").status_code == 200
    assert client.delete(f"/api/profiles/{pid}").status_code == 404
    assert db.count_trash() == 1


def test_a_trashed_profiles_proxy_password_is_still_encrypted(client, tmp_path):
    """The trash table is storage like any other; moving a row must not
    unwrap what the profiles table kept wrapped."""
    pid, _ = _warm_profile(client, "with-proxy", proxyHost="1.2.3.4",
                           proxyPort=8080, proxyUser="bob", proxyPass="hunter2-secret")
    client.delete(f"/api/profiles/{pid}")
    blob = b""
    for name in ("control.db", "control.db-wal", "control.db-shm"):
        path = tmp_path / "data" / "multiprofile" / name
        if path.exists():
            blob += path.read_bytes()
    assert b"hunter2-secret" not in blob


# --------------------------------------------------------------------------
# 6. Listing the trash must not walk the disk
# --------------------------------------------------------------------------

def test_the_trash_list_does_not_stat_every_retained_file(client, monkeypatch):
    """The size is measured once, when the profile is trashed.

    The first version measured it while rendering the list, which is a full
    tree walk per row on every page load. This host's own Chromium profile
    holds 3239 files, so 150 trashed profiles cost about 2 s of stat() calls
    to display a column. Nothing writes to a trashed profile, so there is
    nothing to re-measure.
    """
    from multiprofile.storage import profiles as store

    pid, root = _warm_profile(client)
    (root / "Cache").mkdir(exist_ok=True)
    for i in range(50):
        (root / "Cache" / f"f{i}").write_bytes(b"x" * 32)
    client.delete(f"/api/profiles/{pid}")

    walks = []
    real = store._dir_bytes
    monkeypatch.setattr(store, "_dir_bytes", lambda p: (walks.append(p), real(p))[1])

    body = client.get("/api/trash").get_json()
    assert body["items"][0]["diskBytes"] > 0, "the number must still be right"
    assert walks == [], f"listing walked the disk: {walks}"


def test_a_restored_profile_does_not_keep_the_trash_only_fields(client):
    pid, _ = _warm_profile(client)
    client.delete(f"/api/profiles/{pid}")
    back = client.post(f"/api/profiles/{pid}/restore").get_json()["profile"]
    assert "deletedAt" not in back
    assert "diskBytes" not in back
    assert client.get(f"/api/profiles/{pid}").get_json().get("deletedAt") is None


# --------------------------------------------------------------------------
# 7. The schema migration
# --------------------------------------------------------------------------

def test_a_database_that_predates_disk_bytes_gains_the_column(tmp_path, monkeypatch):
    """v3 shipped the trash table without disk_bytes; v4 adds it in place."""
    import sqlite3

    from multiprofile.storage import db as dbmod

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (3, '2026-01-01')")
        conn.execute(
            "CREATE TABLE profiles_trash (id TEXT PRIMARY KEY, name TEXT NOT NULL"
            " DEFAULT '', client_id TEXT, created_at TEXT, updated_at TEXT NOT NULL,"
            " deleted_at TEXT NOT NULL, data_json TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO profiles_trash VALUES ('profile_old', 'Old', NULL, NULL,"
            " '2026-01-01', '2026-01-01', '{\"id\": \"profile_old\", \"name\": \"Old\"}')"
        )
    conn.close()

    dbmod.close()
    monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(path))
    dbmod.configure(str(path))
    rows = dbmod.list_trash_rows()
    assert [r["id"] for r in rows] == ["profile_old"], "the existing row survived"
    assert rows[0]["diskBytes"] == 0, "unmeasured old rows read as zero, not a crash"
    dbmod.close()


# --------------------------------------------------------------------------
# 8. The running-profile guard
# --------------------------------------------------------------------------

def test_a_running_profile_cannot_be_trashed(client):
    """Chrome is writing into the directory the trash is meant to preserve."""
    import os

    from multiprofile.storage.profiles import set_runtime

    pid, root = _warm_profile(client)
    set_runtime(pid, "running", os.getpid())  # a pid that is certainly alive
    r = client.delete(f"/api/profiles/{pid}")
    assert r.status_code == 409
    assert r.get_json()["error"]["code"] == "profile_running"
    assert root.exists()
    assert client.get(f"/api/profiles/{pid}").status_code == 200


def test_a_profile_left_marked_running_by_a_crash_can_still_be_deleted(client):
    """The guard must check the process, not the record.

    `isRunning` is derived from the recorded status and pid, and neither is
    updated when Chrome dies unexpectedly. A guard that trusted the record
    would make a crashed profile undeletable until something reaped it --
    worse than the problem the guard exists to prevent.
    """
    from multiprofile.storage.profiles import set_runtime

    pid, _ = _warm_profile(client)
    set_runtime(pid, "running", 2_147_483_600)  # recorded, long dead
    r = client.delete(f"/api/profiles/{pid}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["trashed"] is True
