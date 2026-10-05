"""The profile store, after the move from profiles.json to SQLite.

The JSON store rewrote the entire list on every change, and released its lock
between reading the list and writing it back. Two requests editing *different*
profiles therefore raced, and the later writer wrote back a snapshot taken
before the earlier one's change. Measured on the JSON store, under the
threaded Flask server the product actually runs::

    20 threads, each updating a different profile
    writes that survived: 3/20

and all twenty returned success to their callers. Nothing logged, nothing
failed, seventeen edits gone.

A per-row UPDATE cannot lose a write to a different row, so the fix is
structural rather than a bigger lock. These tests hold the properties that
makes true, plus the ones the move must not break: the migration from an
existing install, and the fact that the store is still inspectable.
"""
from __future__ import annotations

import json
import threading

import pytest

from multiprofile.storage import db
from multiprofile.storage.profiles import (
    create_profile,
    delete_profile,
    get_profile,
    import_legacy_json,
    load_profiles,
    save_profiles,
    update_profile,
)
from multiprofile.storage.xdg import profiles_path


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


# --------------------------------------------------------------------------
# 1. The bug the move exists to fix
# --------------------------------------------------------------------------

def test_concurrent_updates_all_survive():
    """The measurement in the module docstring, as an assertion."""
    ids = [create_profile({"name": f"p{i}"})["id"] for i in range(20)]
    errors: list[Exception] = []

    def worker(index: int, profile_id: str):
        try:
            update_profile(profile_id, {"notes": f"by-{index}"})
        except Exception as e:  # noqa: BLE001 - asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i, pid))
               for i, pid in enumerate(ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors[:3]
    kept = [i for i, pid in enumerate(ids)
            if (get_profile(pid) or {}).get("notes") == f"by-{i}"]
    assert len(kept) == 20, f"lost {20 - len(kept)} of 20"


def test_repeated_updates_to_one_profile_do_not_lose_the_last_write():
    """Same profile this time: the transaction must serialise them."""
    pid = create_profile({"name": "hot"})["id"]
    errors: list[Exception] = []

    def worker(i: int):
        try:
            update_profile(pid, {"notes": f"n{i}"})
        except Exception as e:  # noqa: BLE001 - asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors[:3]
    notes = (get_profile(pid) or {}).get("notes")
    assert notes in {f"n{i}" for i in range(20)}, notes


def test_an_update_does_not_touch_its_neighbours():
    a = create_profile({"name": "A", "notes": "keep-a"})["id"]
    b = create_profile({"name": "B", "notes": "keep-b"})["id"]
    update_profile(a, {"notes": "changed"})
    assert get_profile(b)["notes"] == "keep-b"


# --------------------------------------------------------------------------
# 2. The API did not change
# --------------------------------------------------------------------------

def test_create_read_update_delete_round_trip():
    created = create_profile({"name": "Round", "tags": ["a"], "proxyType": "none"})
    pid = created["id"]
    assert get_profile(pid)["name"] == "Round"

    updated = update_profile(pid, {"name": "Trip"})
    assert updated["name"] == "Trip"
    assert get_profile(pid)["name"] == "Trip"

    report = delete_profile(pid, purge_storage=False)
    assert report is not None and report["leftBehind"] == []
    assert get_profile(pid) is None
    assert delete_profile(pid, purge_storage=False) is None


def test_updating_a_missing_profile_returns_none():
    assert update_profile("profile_nope", {"name": "x"}) is None


def test_profiles_keep_their_insertion_order():
    """The console lists them in creation order; a set would not."""
    names = [f"n{i}" for i in range(6)]
    for n in names:
        create_profile({"name": n})
    listed = [p["name"] for p in load_profiles() if p["name"] in names]
    assert listed == names


def test_the_runtime_view_is_still_attached():
    """isRunning/isQueued/launchState are synthesised on read, not stored."""
    pid = create_profile({"name": "rt"})["id"]
    p = get_profile(pid)
    for key in ("status", "pid", "isRunning", "isQueued", "isFailed",
                "launchState", "cdpPort"):
        assert key in p, key
    assert p["isRunning"] is False


def test_save_profiles_replaces_the_whole_set():
    """Still supported, because restore and import genuinely mean that."""
    create_profile({"name": "old"})
    save_profiles([{"id": "profile_x", "name": "new", "proxyType": "none"}])
    names = [p["name"] for p in load_profiles()]
    assert names == ["new"]


# --------------------------------------------------------------------------
# 3. Migration from an existing install
# --------------------------------------------------------------------------

def _write_legacy(items):
    path = profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def test_an_existing_profiles_json_is_imported():
    path = _write_legacy([
        {"id": "profile_a", "name": "Alpha", "seed": 111, "tags": ["x"],
         "clientId": "acme", "notes": "keep me", "timezone": "Europe/Berlin"},
        {"id": "profile_b", "name": "Beta", "seed": 222},
    ])
    items = load_profiles()
    assert [p["id"] for p in items] == ["profile_a", "profile_b"]
    a = get_profile("profile_a")
    assert a["seed"] == 111 and a["tags"] == ["x"] and a["notes"] == "keep me"
    assert a["clientId"] == "acme" and a["timezone"] == "Europe/Berlin"
    assert not path.exists()


def test_the_original_file_is_kept_not_deleted():
    """It is the only copy of the operator's profiles at that moment. A
    migration that removes its own input has no way back if the import turns
    out to be wrong."""
    path = _write_legacy([{"id": "profile_a", "name": "Alpha"}])
    load_profiles()
    backup = path.with_suffix(path.suffix + ".migrated")
    assert backup.exists()
    assert json.loads(backup.read_text())[0]["id"] == "profile_a"


def test_the_import_runs_once():
    _write_legacy([{"id": "profile_a", "name": "Alpha"}])
    assert len(load_profiles()) == 1
    assert import_legacy_json() == 0
    assert len(load_profiles()) == 1


def test_the_import_never_clobbers_rows_written_since():
    """It imports into an empty table only.

    A non-empty table means this install has already moved, so a
    profiles.json still sitting there is stale -- importing it would
    resurrect deleted profiles and revert edits.
    """
    created = create_profile({"name": "already here"})
    _write_legacy([{"id": "profile_a", "name": "Alpha"}])
    assert import_legacy_json() == 0
    ids = [p["id"] for p in load_profiles()]
    assert created["id"] in ids
    assert "profile_a" not in ids


@pytest.mark.parametrize("garbage", [
    "{ not json", "", "null", '{"not": "a list"}', "\x00\x01",
])
def test_a_corrupt_legacy_file_does_not_take_the_process_down(garbage):
    path = profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(garbage, encoding="utf-8")
    assert isinstance(load_profiles(), list)


def test_malformed_entries_are_dropped_not_imported():
    _write_legacy([
        {"id": "profile_good", "name": "Good"},
        "a string",
        {"name": "no id"},
        None,
    ])
    assert [p["id"] for p in load_profiles()] == ["profile_good"]


def test_a_migrated_proxy_password_is_wrapped():
    _write_legacy([{"id": "profile_a", "name": "A", "proxyPass": "s3cret"}])
    load_profiles()
    row = db.get_profile_row("profile_a")
    assert row["proxyPass"] != "s3cret"
    assert row["proxyPass"].startswith("mpenc:")


# --------------------------------------------------------------------------
# 4. The store itself
# --------------------------------------------------------------------------

def test_the_queryable_columns_are_populated():
    """Billing has to aggregate by client without parsing every blob."""
    created = create_profile({"name": "Named", "clientId": "acme"})
    row = db._connect().execute(
        "SELECT name, client_id, created_at, updated_at FROM profiles"
        " WHERE id = ?", (created["id"],)
    ).fetchone()
    assert row["name"] == "Named"
    assert row["client_id"] == "acme"
    assert row["created_at"] and row["updated_at"]


def test_a_row_that_will_not_parse_is_skipped_not_fatal():
    create_profile({"name": "fine"})
    conn = db._connect()
    with conn:
        conn.execute(
            "INSERT INTO profiles (id, name, client_id, created_at, updated_at,"
            " data_json) VALUES ('profile_bad', 'bad', NULL, '', '', '{oops')"
        )
    names = [p["name"] for p in load_profiles()]
    assert "fine" in names
    assert "bad" not in names


def test_mutate_profile_returns_none_for_a_missing_row():
    assert db.mutate_profile("profile_nope", lambda p: p) is None


def test_a_mutation_returning_none_leaves_the_row_alone():
    pid = create_profile({"name": "untouched"})["id"]
    db.mutate_profile(pid, lambda p: None)
    assert get_profile(pid)["name"] == "untouched"
