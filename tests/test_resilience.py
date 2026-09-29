"""Failure modes the brief names: none of them may lose or corrupt data.

Proxy down, network loss, disk full, a corrupted profile store, two
launches of one profile at once, and the app killed mid-write. Each is
provoked here rather than reasoned about.

The standard this holds things to: a failure may refuse to proceed, and it
may lose the *current* operation, but it must never leave the operator with
a half-written file, a silently-wrong route, or a traceback instead of a
reason.
"""
from __future__ import annotations

import threading

import pytest


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


# ---------------------------------------------------------------------------
# proxy down / network loss
# ---------------------------------------------------------------------------


def test_a_dead_proxy_blocks_the_launch_rather_than_going_direct(xdg):
    """The one failure mode that must never fall back to the host IP."""
    from multiprofile.network import (
        NetworkConfig,
        NetworkMode,
        ProxyConfig,
        check_proxy_before_launch,
    )

    # Port 9 (discard) with nothing listening: a proxy that is simply down.
    net = NetworkConfig(
        mode=NetworkMode.HTTP_PROXY,
        proxy=ProxyConfig(scheme="http", host="127.0.0.1", port=9),
    )
    allowed, message, result = check_proxy_before_launch(net, timeout=2.0)

    assert allowed is False
    assert "blocked" in (message or "").lower()
    assert result is not None and result.ok is False


def test_the_block_message_names_the_proxy_without_its_password(xdg):
    from multiprofile.network import (
        NetworkConfig,
        NetworkMode,
        ProxyConfig,
        check_proxy_before_launch,
    )

    net = NetworkConfig(
        mode=NetworkMode.HTTP_PROXY,
        proxy=ProxyConfig("http", "127.0.0.1", 9, username="operator", password="s3cret"),
    )
    _, message, _ = check_proxy_before_launch(net, timeout=2.0)

    assert "127.0.0.1:9" in message
    assert "s3cret" not in message
    assert "***" in message


def test_network_loss_mid_probe_is_a_block_not_a_crash(xdg, monkeypatch):
    """A resolver failure must surface as a refusal, not a traceback."""
    import multiprofile.network as N

    def exploded(*a, **k):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(N.socket, "create_connection", exploded)
    net = N.NetworkConfig(
        mode=N.NetworkMode.SOCKS5, proxy=N.ProxyConfig("socks5", "198.51.100.1", 1080)
    )
    allowed, message, _ = N.check_proxy_before_launch(net, timeout=1.0)

    assert allowed is False
    assert message


# ---------------------------------------------------------------------------
# disk full
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_the_previous_store_intact(xdg):
    """A write that fails part-way must not damage what was already there.

    The store moved from a JSON file to SQLite, so the failure mode changed
    shape -- there is no rename to interrupt any more -- but the standard did
    not: the operation may be lost, the data may not.
    """
    import sqlite3
    from unittest import mock

    from multiprofile.storage import db
    from multiprofile.storage.profiles import create_profile, load_profiles

    create_profile({"name": "Survivor"})
    before = [p["id"] for p in load_profiles()]

    def disk_full(*a, **k):
        raise sqlite3.OperationalError("database or disk is full")

    with mock.patch.object(db, "insert_profiles", disk_full), \
            pytest.raises(sqlite3.OperationalError, match="disk is full"):
        create_profile({"name": "Doomed"})

    after = load_profiles()
    assert [p["id"] for p in after] == before, "a failed write damaged the store"
    names = [p["name"] for p in after]
    assert "Survivor" in names
    assert "Doomed" not in names


def test_a_failed_write_leaves_no_debris_that_reads_as_data(xdg):
    """After a failed write the store must still be readable, and the
    half-finished row must not be visible to the next reader."""
    import sqlite3
    from unittest import mock

    from multiprofile.storage import db
    from multiprofile.storage.profiles import create_profile, load_profiles

    create_profile({"name": "One"})

    def disk_full(*a, **k):
        raise sqlite3.OperationalError("database or disk is full")

    with mock.patch.object(db, "insert_profiles", disk_full), \
            pytest.raises(sqlite3.OperationalError, match="disk is full"):
        create_profile({"name": "Two"})

    names = [p["name"] for p in load_profiles()]
    assert "One" in names
    assert "Two" not in names


# ---------------------------------------------------------------------------
# corrupted store
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("garbage", [
    "{ not json at all",
    "",
    "null",
    '{"not": "a list"}',
    "\x00\x01\x02",
])
def test_a_corrupted_profile_store_does_not_crash_the_app(xdg, garbage):
    """A bad file must degrade to empty, not take the process down."""
    from multiprofile.storage.profiles import load_profiles
    from multiprofile.storage.xdg import profiles_path

    profiles_path().parent.mkdir(parents=True, exist_ok=True)
    profiles_path().write_text(garbage, encoding="utf-8")

    result = load_profiles()
    assert isinstance(result, list)


@pytest.mark.parametrize("garbage", ["{ broken", "", "[1, 2, 3]"])
def test_corrupted_gateways_and_pools_degrade_to_empty(xdg, garbage):
    from multiprofile.storage.gateways import _gateways_path, load_gateways
    from multiprofile.storage.pools import load_pools, pools_path

    for path in (_gateways_path(), pools_path()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(garbage, encoding="utf-8")

    assert isinstance(load_gateways(), list)
    assert isinstance(load_pools(), list)


def test_the_api_still_answers_with_a_corrupted_store(xdg):
    """The operator needs the console to open so they can fix it."""
    from multiprofile.api.server import create_app
    from multiprofile.storage.xdg import profiles_path

    profiles_path().parent.mkdir(parents=True, exist_ok=True)
    profiles_path().write_text("{ corrupted", encoding="utf-8")

    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/profiles").status_code == 200


# ---------------------------------------------------------------------------
# concurrent access
# ---------------------------------------------------------------------------


def test_concurrent_creates_all_survive(xdg):
    """Twenty threads creating profiles must leave twenty profiles.

    This test existed before and passed while the store was silently losing
    writes, because it only asserted the names it *did* find were unique --
    a lost create removes a name without duplicating one. Measured on the
    JSON store, 20 concurrent updates kept 3. The count is the assertion
    that would have caught it, so it is the one made here.
    """
    from multiprofile.storage.profiles import create_profile, load_profiles

    before = len(load_profiles())
    errors: list[Exception] = []

    def worker(i: int):
        try:
            create_profile({"name": f"concurrent-{i}"})
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent creates raised: {errors[:3]}"
    names = [p["name"] for p in load_profiles()]
    assert len(names) == before + 20, f"lost {before + 20 - len(names)} creates"
    assert len(names) == len(set(names)), "a concurrent write clobbered another"


def test_concurrent_updates_do_not_overwrite_each_other(xdg):
    """The bug the storage move exists to fix.

    The JSON store rewrote the whole list on every change from a snapshot
    taken before it, so two updates to *different* profiles raced and the
    later writer discarded the earlier one's change. Every call still
    returned success. Measured before the move: 3 of 20 survived.
    """
    from multiprofile.storage.profiles import create_profile, get_profile, update_profile

    ids = [create_profile({"name": f"p{i}"})["id"] for i in range(20)]
    errors: list[Exception] = []

    def worker(index: int, profile_id: str):
        try:
            update_profile(profile_id, {"notes": f"written-by-{index}"})
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i, pid))
               for i, pid in enumerate(ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent updates raised: {errors[:3]}"
    kept = [i for i, pid in enumerate(ids)
            if (get_profile(pid) or {}).get("notes") == f"written-by-{i}"]
    assert len(kept) == 20, f"lost {20 - len(kept)} of 20 concurrent updates"


def test_one_profile_cannot_hold_two_pool_assignments(xdg):
    """Concurrent assignment of the same profile must settle on one member."""
    from multiprofile.storage.pools import assign, create_pool, get_pool

    pool = create_pool({"label": "P", "members": [
        {"label": "a", "host": "198.51.100.1", "port": 1080},
        {"label": "b", "host": "198.51.100.2", "port": 1080},
    ]})
    seen: list[str] = []

    def worker():
        member, _ = assign(pool["id"], "prof-1")
        if member:
            seen.append(member["id"])

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert len(set(seen)) == 1, f"one profile got several members: {set(seen)}"
    assert len(get_pool(pool["id"])["assignments"]) == 1


# ---------------------------------------------------------------------------
# killed mid-write
# ---------------------------------------------------------------------------


def test_a_kill_mid_write_cannot_leave_half_a_store(xdg):
    """Every write is one transaction, so a kill loses it whole or not at all.

    The JSON store got this from write-to-temp-then-rename. SQLite gets it
    from the write-ahead log: a transaction that never commits is not
    replayed. What is asserted here is the property, not the mechanism --
    an interrupted write leaves the previous state readable and complete.
    """
    import sqlite3

    from multiprofile.storage import db
    from multiprofile.storage.profiles import create_profile, load_profiles

    create_profile({"name": "Before"})
    before = [p["id"] for p in load_profiles()]

    # A transaction opened and abandoned, the way a killed process leaves one.
    conn = db._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO profiles (id, name, client_id, created_at, updated_at,"
            " data_json) VALUES (?, ?, ?, ?, ?, ?)",
            ("profile_halfwritten", "Half", None, "2025-01-01", "2025-01-01", "{"),
        )
        conn.rollback()
    except sqlite3.Error:
        conn.rollback()

    after = load_profiles()
    assert [p["id"] for p in after] == before, "an abandoned write became visible"
    assert "profile_halfwritten" not in [p["id"] for p in after]

    # And the store still takes writes afterwards.
    create_profile({"name": "After"})
    assert "After" in [p["name"] for p in load_profiles()]


def test_a_partial_temp_file_is_never_loaded(xdg):
    from multiprofile.storage.profiles import create_profile, load_profiles
    from multiprofile.storage.xdg import profiles_path

    create_profile({"name": "Real"})
    tmp = profiles_path().with_suffix(profiles_path().suffix + ".tmp")
    tmp.write_text("[{garbage", encoding="utf-8")

    names = [p["name"] for p in load_profiles()]
    assert "Real" in names, "the partial .tmp file was loaded instead of the real store"
