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


# ---------------------------------------------------------------------------
# the real stores
# ---------------------------------------------------------------------------


def test_profiles_json_is_not_world_readable(xdg):
    """The regression that mattered: this file can carry proxyPass."""
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import profiles_path

    create_profile({"name": "perm test", "proxyPass": "secret"})

    assert _is_private(profiles_path()), f"profiles.json is {oct(_mode(profiles_path()))}"


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
    from multiprofile.storage.xdg import harden_data_dir, profiles_path

    create_profile({"name": "x"})
    os.chmod(profiles_path(), 0o664)  # simulate a pre-existing install

    changed = harden_data_dir()

    assert profiles_path().name in changed
    assert _is_private(profiles_path())


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
    from multiprofile.storage.xdg import profiles_path

    create_profile({"name": "x"})
    os.chmod(profiles_path(), 0o664)

    prepare_data_dir()

    assert _is_private(profiles_path())


def test_create_app_does_not_change_permissions(xdg, monkeypatch):
    """The factory stays free of side effects on operator data."""
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import profiles_path

    create_profile({"name": "x"})
    os.chmod(profiles_path(), 0o664)

    from multiprofile.api.server import create_app
    create_app()

    assert _mode(profiles_path()) == 0o664, "create_app touched operator data"
