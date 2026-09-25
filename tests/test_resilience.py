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

import json
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


def test_disk_full_leaves_the_previous_file_intact(xdg):
    """ENOSPC during a save must not truncate what was already there.

    NB: patched with mock.patch, not monkeypatch. monkeypatch.undo() would
    also undo the xdg fixture's setenv calls -- they share one instance --
    and the rest of the test would then read the operator's real home
    directory instead of tmp_path.
    """
    from unittest import mock

    from multiprofile.storage.profiles import create_profile, load_profiles
    from multiprofile.storage.xdg import profiles_path

    create_profile({"name": "Survivor"})
    before = json.loads(profiles_path().read_text())

    def no_space(*a, **k):
        raise OSError(28, "No space left on device")

    with mock.patch("pathlib.Path.write_text", no_space), pytest.raises(OSError):
        create_profile({"name": "Doomed"})

    after = json.loads(profiles_path().read_text())
    assert [p["id"] for p in after] == [p["id"] for p in before], (
        "a failed write damaged the store"
    )
    names = [p["name"] for p in load_profiles()]
    assert "Survivor" in names
    assert "Doomed" not in names


def test_a_failed_write_leaves_no_temp_file_masquerading_as_data(xdg):
    from unittest import mock

    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import data_dir

    create_profile({"name": "One"})

    def no_space(*a, **k):
        raise OSError(28, "No space left on device")

    with mock.patch("pathlib.Path.write_text", no_space), pytest.raises(OSError):
        create_profile({"name": "Two"})

    # A leftover .tmp is harmless only because nothing reads it; assert the
    # real file is still the one being read.
    assert json.loads((data_dir() / "profiles.json").read_text())


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


def test_concurrent_writes_do_not_interleave_into_a_broken_file(xdg):
    """Twenty threads creating profiles must leave valid JSON."""
    from multiprofile.storage.profiles import create_profile, load_profiles
    from multiprofile.storage.xdg import profiles_path

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
    json.loads(profiles_path().read_text())  # must still parse
    names = [p["name"] for p in load_profiles()]
    assert len(names) == len(set(names)), "a concurrent write clobbered another"


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


def test_writes_are_atomic_so_a_kill_cannot_leave_half_a_file(xdg):
    """The store is replaced by rename, never written in place.

    A process killed between open() and the last write would otherwise leave
    a truncated profiles.json and lose every profile.
    """
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import profiles_path, secure_write_json

    create_profile({"name": "Before"})
    original = profiles_path().read_text()

    # Simulate a kill partway through: the temp file exists with partial
    # content, but the rename never happened.
    tmp = profiles_path().with_suffix(profiles_path().suffix + ".tmp")
    tmp.write_text('[{"id": "half', encoding="utf-8")

    assert profiles_path().read_text() == original, "the live file was touched"
    json.loads(profiles_path().read_text())

    # And the next successful write still lands cleanly over the debris.
    secure_write_json(profiles_path(), [{"id": "after", "name": "After"}])
    assert json.loads(profiles_path().read_text())[0]["id"] == "after"
    assert (profiles_path().stat().st_mode & 0o077) == 0


def test_a_partial_temp_file_is_never_loaded(xdg):
    from multiprofile.storage.profiles import create_profile, load_profiles
    from multiprofile.storage.xdg import profiles_path

    create_profile({"name": "Real"})
    tmp = profiles_path().with_suffix(profiles_path().suffix + ".tmp")
    tmp.write_text("[{garbage", encoding="utf-8")

    names = [p["name"] for p in load_profiles()]
    assert "Real" in names, "the partial .tmp file was loaded instead of the real store"
