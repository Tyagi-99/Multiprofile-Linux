"""Operator data must be owner-only on disk.

profiles.json was written with a bare ``write_text``, so the process umask
decided its mode: 0664 on a real install, group- and world-readable, on a
file whose schema carries ``proxyPass``. gateways.json and pools.json were
0600 only because two separate authors happened to remember. The cookie
jars, clients.json, templates.json, the audit log and control.db were not
protected at all.

Every writer now goes through ``secure_write_json`` so the decision is made
once, and ``harden_data_dir`` tightens the files that were already written
loosely before this existed.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


def _mode(path) -> int:
    return path.stat().st_mode & 0o777


def _is_private(path) -> bool:
    return (path.stat().st_mode & 0o077) == 0


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------


def test_secure_write_json_is_owner_only(xdg):
    from multiprofile.storage.xdg import data_dir, secure_write_json

    p = data_dir() / "probe.json"
    secure_write_json(p, {"a": 1})

    assert _mode(p) == 0o600
    import json
    assert json.loads(p.read_text()) == {"a": 1}


def test_secure_write_json_leaves_no_loose_temp_behind(xdg):
    from multiprofile.storage.xdg import data_dir, secure_write_json

    p = data_dir() / "probe.json"
    secure_write_json(p, {"a": 1})
    assert list(data_dir().glob("*.tmp")) == []


def test_secure_write_json_replaces_atomically(xdg):
    from multiprofile.storage.xdg import data_dir, secure_write_json

    p = data_dir() / "probe.json"
    secure_write_json(p, {"v": 1})
    secure_write_json(p, {"v": 2})
    import json
    assert json.loads(p.read_text()) == {"v": 2}
    assert _mode(p) == 0o600


def test_harden_existing_tightens_a_loose_file(xdg):
    from multiprofile.storage.xdg import data_dir, harden_existing

    p = data_dir() / "loose.json"
    p.write_text("{}")
    os.chmod(p, 0o664)

    assert harden_existing(p) is True
    assert _mode(p) == 0o600
    # Idempotent: nothing left to do the second time.
    assert harden_existing(p) is False


def test_harden_existing_is_quiet_about_missing_files(xdg):
    from multiprofile.storage.xdg import data_dir, harden_existing

    assert harden_existing(data_dir() / "nope.json") is False


def _db_file():
    """The main database file, created if it does not exist yet."""
    import pathlib

    from multiprofile.storage.db import db_path
    from multiprofile.storage.profiles import load_profiles

    load_profiles()  # forces the store into existence
    return pathlib.Path(db_path())


# ---------------------------------------------------------------------------
# the real stores
# ---------------------------------------------------------------------------


def test_the_profile_store_is_not_world_readable(xdg):
    """The regression that mattered, now asserted where profiles live.

    When the store moved from profiles.json into SQLite it landed 0644,
    because SQLite creates the file itself and the process umask decided --
    so every profile's proxy host, username, client and notes became
    world-readable on a normal install. The sidecars count: a row lives in
    control.db-wal before it is checkpointed.
    """
    import pathlib

    from multiprofile.storage.db import db_path
    from multiprofile.storage.profiles import create_profile

    create_profile({"name": "perm test", "proxyPass": "secret"})

    root = pathlib.Path(db_path())
    files = [f for f in sorted(root.parent.glob(root.name + "*")) if f.is_file()]
    assert files, "the database was never created"
    for f in files:
        assert _is_private(f), f"{f.name} is {oct(_mode(f))}"


def test_cookie_jars_are_not_world_readable(xdg):
    """A cookie jar is a session-hijacking kit."""
    from multiprofile.cookies_store import cookies_path, save_cookies

    save_cookies("prof-a", [{"name": "sid", "value": "abc", "domain": ".x.test"}])

    assert _is_private(cookies_path("prof-a"))


def test_clients_and_templates_are_not_world_readable(xdg):
    from multiprofile.storage.clients import create_client
    from multiprofile.storage.templates import create_template
    from multiprofile.storage.xdg import clients_path, templates_path

    create_client({"name": "Acme"})
    create_template({"name": "T"})

    assert _is_private(clients_path())
    assert _is_private(templates_path())


def test_pools_and_gateways_stay_private(xdg):
    """These were already correct — keep them that way."""
    from multiprofile.storage.gateways import create_gateway
    from multiprofile.storage.pools import create_pool, pools_path
    from multiprofile.storage.xdg import data_dir

    create_pool({"label": "P", "members": []})
    create_gateway({"label": "G", "providerType": "custom", "host": "h", "port": 1})

    assert _is_private(pools_path())
    assert _is_private(data_dir() / "gateways.json")


def test_the_audit_log_is_not_world_readable(xdg):
    """Who launched, exported and deleted what is the operator's record."""
    from multiprofile.audit import audit_log
    from multiprofile.storage.xdg import audit_path

    audit_log("probe", ok=True)

    assert _is_private(audit_path())


# ---------------------------------------------------------------------------
# migration for files already on disk
# ---------------------------------------------------------------------------


def test_harden_data_dir_fixes_files_written_before_this_rule(xdg):
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import harden_data_dir

    create_profile({"name": "x"})
    target = _db_file()
    os.chmod(target, 0o664)  # simulate a pre-existing install

    changed = harden_data_dir()

    assert target.name in changed
    assert _is_private(target)


def test_harden_data_dir_is_idempotent(xdg):
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import harden_data_dir

    create_profile({"name": "x"})
    harden_data_dir()
    assert harden_data_dir() == []


def test_the_server_entrypoint_hardens_existing_data(xdg, monkeypatch):
    """The migration must actually run, not merely exist.

    It runs from the server entrypoint rather than create_app(): the factory
    is called by every test, and a migration there once reached past a leaky
    fixture and rewrote the operator's real files.
    """
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.app import prepare_data_dir
    from multiprofile.storage.profiles import create_profile
    create_profile({"name": "x"})
    target = _db_file()
    os.chmod(target, 0o664)

    prepare_data_dir()

    assert _is_private(target)


def test_create_app_does_not_change_permissions(xdg, monkeypatch):
    """The factory stays free of side effects on operator data."""
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.storage.profiles import create_profile
    create_profile({"name": "x"})
    target = _db_file()
    os.chmod(target, 0o664)

    from multiprofile.api.server import create_app
    create_app()

    assert _mode(target) == 0o664, "create_app touched operator data"


# ---------------------------------------------------------------------------
# the launcher's own writes, and the secrets it used to leave behind (F4)
# ---------------------------------------------------------------------------


def _stage_legacy_extension(profile_id: str) -> "os.PathLike":
    """Recreate exactly what the old staging step left on disk."""
    import json

    from multiprofile.storage.xdg import chrome_profile_dir

    staged = chrome_profile_dir(profile_id) / "extension"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "manifest.json").write_text("{}", encoding="utf-8")
    (staged / "evasion.js").write_text("// config\n", encoding="utf-8")
    (staged / "cookies.json").write_text(
        json.dumps([{"name": "sid", "value": "abc", "domain": ".x.test"}]),
        encoding="utf-8",
    )
    (staged / "proxy.json").write_text(
        json.dumps({"host": "h", "port": 1, "username": "op",
                    "password": "super-secret-value"}),
        encoding="utf-8",
    )
    for name in ("cookies.json", "proxy.json", "evasion.js"):
        os.chmod(staged / name, 0o644)  # what the umask actually produced
    return staged


def test_the_legacy_staged_secrets_are_deleted_not_merely_tightened(xdg):
    """proxy.json held the proxy password in cleartext, at 0644.

    Nothing reads it any more, so leaving a hardened copy on disk would keep
    a plaintext secret for no reason. It goes.
    """
    from multiprofile.storage.xdg import purge_legacy_extension_staging

    staged = _stage_legacy_extension("prof-legacy")
    assert (staged / "proxy.json").exists()

    cleaned = purge_legacy_extension_staging("prof-legacy")

    assert cleaned == ["prof-legacy"]
    assert not staged.exists()


def test_the_purge_sweeps_every_profile(xdg):
    from multiprofile.storage.xdg import purge_legacy_extension_staging

    for pid in ("p1", "p2", "p3"):
        _stage_legacy_extension(pid)

    assert sorted(purge_legacy_extension_staging()) == ["p1", "p2", "p3"]
    assert purge_legacy_extension_staging() == []


def test_the_purge_is_quiet_when_there_is_nothing_to_do(xdg):
    from multiprofile.storage.xdg import purge_legacy_extension_staging

    assert purge_legacy_extension_staging() == []
    assert purge_legacy_extension_staging("no-such-profile") == []


def test_the_server_entrypoint_removes_the_legacy_staged_secrets(xdg, monkeypatch):
    """The cleanup must actually run, not merely exist."""
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.app import prepare_data_dir

    staged = _stage_legacy_extension("prof-entry")
    prepare_data_dir()

    assert not staged.exists()


def test_a_launch_removes_this_profiles_legacy_staged_secrets(xdg, monkeypatch):
    """A process that started before the sweep still cleans as it launches."""
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    import multiprofile.browser.launcher as L
    from multiprofile.storage.profiles import create_profile

    pid = create_profile({"name": "legacy-launch", "proxyType": "none"})["id"]
    staged = _stage_legacy_extension(pid)

    monkeypatch.setattr(L, "find_chrome", lambda: "/definitely/not/a/browser")
    # The launch fails (no browser); the purge must already have happened.
    L.launch_profile(pid, url="about:blank", direct=True)

    assert not staged.exists()


def test_no_launcher_write_bypasses_secure_write_json():
    """Every bare write_text in the launcher is a permission decision made by
    the umask. The two that mattered wrote the cookie jar and the cleartext
    proxy password at 0644; this keeps a third from appearing."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "src" / "multiprofile" / "browser" / "launcher.py"
    ).read_text(encoding="utf-8")
    # Only ensure_no_client_frame_shadow writes, to a temp file it then
    # renames over Chrome's own Preferences — a file Chrome owns and rewrites,
    # holding no profile secrets.
    assert source.count(".write_text(") == 1
    assert source.count("tmp.write_text(") == 1
