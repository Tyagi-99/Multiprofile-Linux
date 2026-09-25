"""Proxy passwords must not sit on disk in the clear.

Seventeen gateway passwords were plaintext on this machine. File
permissions were fixed separately, but 0600 only stops another *user* --
it does nothing for a stolen disk, a backup, or a synced home directory.

Scope is deliberate and narrow: the values that are actually secrets.
Profile names, tags and user agents stay readable so the files remain
diffable and the storage migration planned for later is not fighting an
encrypted blob. Anyone reading "no plaintext JSON on disk" should know that
is not what this claims.
"""
from __future__ import annotations

import pytest

from multiprofile import secrets as S


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # These tests exercise migration on purpose, in an isolated dir.
    monkeypatch.setenv("MULTIPROFILE_ALLOW_MIGRATION_IN_TESTS", "1")
    # Never touch the real login keyring from a test.
    monkeypatch.setattr(S, "_store_in_keyring", lambda key: False)
    monkeypatch.setattr(S, "_load_from_keyring", lambda: None)
    S.reset_cache()
    yield
    S.reset_cache()


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


def test_a_secret_round_trips():
    token = S.encrypt_secret("hunter2")
    assert token != "hunter2"
    assert "hunter2" not in token
    assert S.decrypt_secret(token) == "hunter2"


def test_the_envelope_is_self_describing():
    token = S.encrypt_secret("x")
    assert token.startswith("mpenc:v1:")
    assert S.is_encrypted(token)
    assert not S.is_encrypted("hunter2")


def test_encryption_is_randomised():
    """Same input twice must not give the same ciphertext."""
    a, b = S.encrypt_secret("same"), S.encrypt_secret("same")
    assert a != b
    assert S.decrypt_secret(a) == S.decrypt_secret(b) == "same"


def test_empty_stays_empty():
    assert S.encrypt_secret("") == ""
    assert S.encrypt_secret(None) == ""
    assert S.decrypt_secret("") == ""
    assert S.decrypt_secret(None) == ""


def test_encrypting_twice_does_not_double_wrap():
    once = S.encrypt_secret("p")
    assert S.encrypt_secret(once) == once


def test_legacy_plaintext_is_still_readable():
    """An un-migrated install must keep launching."""
    assert S.decrypt_secret("old-plaintext-password") == "old-plaintext-password"


def test_a_tampered_value_is_refused_not_silently_wrong():
    token = S.encrypt_secret("hunter2")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(S.SecretsUnavailable):
        S.decrypt_secret(tampered)


def test_a_value_from_another_key_is_refused():
    token = S.encrypt_secret("hunter2")
    S.reset_cache()
    (S._key_file()).unlink()  # force a brand new key
    with pytest.raises(S.SecretsUnavailable):
        S.decrypt_secret(token)


# ---------------------------------------------------------------------------
# key handling
# ---------------------------------------------------------------------------


def test_the_key_file_fallback_is_owner_only():
    S.get_master_key()
    assert S.backend() == "file"
    assert (S._key_file().stat().st_mode & 0o077) == 0


def test_the_key_is_stable_across_calls():
    assert S.get_master_key() == S.get_master_key()
    S.reset_cache()
    assert S.get_master_key() == S.get_master_key()


def test_status_reports_the_weaker_backend_honestly():
    st = S.status()
    assert st["ok"] is True
    assert st["backend"] == "file"
    assert st["keychain"] is False
    assert "keychain" in st["message"].lower()


def test_the_keychain_is_preferred_when_available(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(S, "_store_in_keyring", lambda key: store.setdefault("k", key) or True)
    monkeypatch.setattr(S, "_load_from_keyring", lambda: store.get("k"))
    S.reset_cache()

    key = S.get_master_key()

    assert S.backend() == "keyring"
    assert S.status()["keychain"] is True
    assert not S._key_file().exists(), "no key file should be written when a keychain exists"
    assert key == store["k"]


# ---------------------------------------------------------------------------
# the real stores
# ---------------------------------------------------------------------------


def test_a_gateway_password_is_not_plaintext_on_disk():
    from multiprofile.storage.gateways import _gateways_path, create_gateway, get_gateway

    gw = create_gateway({"label": "G", "providerType": "custom", "scheme": "socks5",
                         "host": "198.51.100.1", "port": 1080,
                         "username": "u", "password": "s3cret-gateway"})

    raw = _gateways_path().read_text()
    assert "s3cret-gateway" not in raw
    assert "mpenc:v1:" in raw
    # ...and the launch path still gets the real value.
    assert get_gateway(gw["id"], safe=False)["password"] == "s3cret-gateway"


def test_a_masked_gateway_read_never_exposes_the_secret():
    from multiprofile.storage.gateways import create_gateway, get_gateway

    gw = create_gateway({"label": "G", "providerType": "custom", "host": "h",
                         "port": 1, "password": "s3cret-gateway"})

    assert get_gateway(gw["id"])["password"] == "***"


def test_a_pool_member_password_is_not_plaintext_on_disk():
    from multiprofile.storage.pools import create_pool, get_pool, pools_path, resolve_member

    pool = create_pool({"label": "P", "members": [
        {"label": "a", "host": "198.51.100.1", "port": 1080,
         "username": "u", "password": "s3cret-pool"}]})

    raw = pools_path().read_text()
    assert "s3cret-pool" not in raw
    assert "mpenc:v1:" in raw

    live = get_pool(pool["id"])
    assert resolve_member(live, live["members"][0])["password"] == "s3cret-pool"


def test_a_profile_proxy_password_is_not_plaintext_on_disk():
    from multiprofile.network import network_config_for_profile
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import profiles_path

    p = create_profile({"name": "P", "proxyType": "socks5", "proxyHost": "198.51.100.1",
                        "proxyPort": 1080, "proxyUser": "u", "proxyPass": "s3cret-profile"})

    raw = profiles_path().read_text()
    assert "s3cret-profile" not in raw

    # The route built for launch still carries the real password.
    from multiprofile.storage.profiles import get_profile
    net = network_config_for_profile(get_profile(p["id"]))
    assert net.proxy.password == "s3cret-profile"


def test_non_secret_profile_fields_stay_readable():
    """Deliberate: the files must remain diffable."""
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import profiles_path

    create_profile({"name": "Readable Name", "tags": ["shop"]})
    raw = profiles_path().read_text()

    assert "Readable Name" in raw
    assert "shop" in raw


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def test_migration_encrypts_what_was_written_in_the_clear():
    import json

    from multiprofile.storage.gateways import _gateways_path, get_gateway

    # Write a gateway the old way, bypassing the encrypting writer.
    _gateways_path().parent.mkdir(parents=True, exist_ok=True)
    _gateways_path().write_text(json.dumps([{
        "id": "gw_legacy", "label": "Legacy", "providerType": "custom",
        "host": "h", "port": 1, "username": "u", "password": "legacy-cleartext",
    }]))

    counts = S.migrate_plaintext_secrets()

    assert counts["gateways"] == 1
    assert "legacy-cleartext" not in _gateways_path().read_text()
    assert get_gateway("gw_legacy", safe=False)["password"] == "legacy-cleartext"


def test_migration_is_idempotent():
    from multiprofile.storage.gateways import create_gateway

    create_gateway({"label": "G", "providerType": "custom", "host": "h",
                    "port": 1, "password": "p"})
    S.migrate_plaintext_secrets()
    assert S.migrate_plaintext_secrets() == {"gateways": 0, "pools": 0, "profiles": 0}


def test_migration_never_raises_on_a_broken_store(monkeypatch):
    """Startup must not die because one file is unreadable."""
    from multiprofile.storage import gateways

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(gateways, "load_gateways", boom)
    assert S.migrate_plaintext_secrets()["gateways"] == 0


def _seed_legacy_gateway() -> None:
    import json

    from multiprofile.storage.gateways import _gateways_path

    _gateways_path().parent.mkdir(parents=True, exist_ok=True)
    _gateways_path().write_text(json.dumps([{
        "id": "gw_legacy", "label": "L", "providerType": "custom",
        "host": "h", "port": 1, "password": "cleartext-at-boot",
    }]))


def test_create_app_does_not_touch_operator_data(monkeypatch):
    """The factory must have no side effects on the operator's files.

    This is the regression guard for a real incident. Migration used to run
    inside create_app(), which every API test calls. One test's isolation
    patched xdg.data_dir but not the name gateways.py had already bound at
    import, so the migration reached the operator's REAL home directory and
    encrypted 31 live gateway passwords with a key that lived in a pytest
    temp dir. The data was only recoverable because that temp dir had not
    been cleaned up yet.
    """
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.storage.gateways import _gateways_path

    _seed_legacy_gateway()

    from multiprofile.api.server import create_app
    create_app()

    assert "cleartext-at-boot" in _gateways_path().read_text(), (
        "create_app rewrote operator data; migrations belong in the entrypoint"
    )


def test_the_server_entrypoint_does_run_the_migration(monkeypatch):
    """...and the real startup path still does the work."""
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.app import prepare_data_dir
    from multiprofile.storage.gateways import _gateways_path, get_gateway

    _seed_legacy_gateway()

    prepare_data_dir()

    assert "cleartext-at-boot" not in _gateways_path().read_text()
    assert get_gateway("gw_legacy", safe=False)["password"] == "cleartext-at-boot"


def test_migration_refuses_to_run_under_pytest_without_opt_in(monkeypatch):
    """Defence in depth, independent of any fixture being correct."""
    monkeypatch.delenv("MULTIPROFILE_ALLOW_MIGRATION_IN_TESTS", raising=False)
    from multiprofile.storage.gateways import _gateways_path

    _seed_legacy_gateway()

    assert S.migrate_plaintext_secrets() == {"gateways": 0, "pools": 0, "profiles": 0}
    assert "cleartext-at-boot" in _gateways_path().read_text()


def test_health_reports_the_secrets_backend(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    from multiprofile.api.server import create_app

    app = create_app()
    app.config["TESTING"] = True
    body = app.test_client().get("/api/health").get_json()

    assert "secrets" in body
    assert body["secrets"]["backend"] in {"keyring", "file"}
    assert isinstance(body["secrets"]["keychain"], bool)
