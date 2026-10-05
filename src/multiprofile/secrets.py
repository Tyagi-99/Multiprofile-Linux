"""Encryption at rest for the few values that are actually secrets.

WHAT THIS PROTECTS
------------------
Proxy passwords, in gateways.json, pools.json and a profile's proxyPass.
Seventeen of them sat in cleartext on this machine. File permissions were
fixed separately, but 0600 only stops another *user*; it does nothing for a
stolen disk, a backup, a synced home directory, or anything running as the
operator's own account.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not encrypt whole files. Profile names, tags, user agents and
timezones are not secrets, and encrypting them would mean re-doing the work
when the storage layer moves to SQLite. Secrets are encrypted per value, in
place, so the files stay readable and diffable and the migration is
reversible. Anyone claiming "no plaintext JSON on disk" should be pointed
at this paragraph: that is a Phase 2 statement, not a Phase 1 one.

THE KEY
-------
A 32-byte master key in the OS keychain -- libsecret/Secret Service on
Linux, DPAPI-backed Credential Manager on Windows, Keychain on macOS --
reached through the ``keyring`` package.

There is a fallback to a 0600 key file, because a headless server has no
keyring daemon and refusing to start there would be worse than a documented
downgrade. The fallback is NEVER silent: ``backend()`` reports which one is
in use, the API exposes it, and the server logs a warning at startup. A
security control you cannot see the state of is not a control.

THE ENVELOPE
------------
``mpenc:v1:<base64(nonce || ciphertext||tag)>`` -- AES-256-GCM, 12-byte
nonce, no associated data. Self-describing on purpose: a value that does
not carry the prefix is treated as legacy plaintext and read as-is, so an
existing install keeps working and re-encrypts on its next write.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets as _secrets
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("multiprofile.secrets")

_PREFIX = "mpenc:v1:"
_SERVICE = "multiprofile-native"
_KEY_NAME = "master-key"
_KEY_BYTES = 32
_NONCE_BYTES = 12

_lock = threading.RLock()
_cached_key: bytes | None = None
_backend: str = "unknown"


class SecretsUnavailable(RuntimeError):
    """Raised when a value must be encrypted but no key can be obtained."""


# ---------------------------------------------------------------------------
# key material
# ---------------------------------------------------------------------------


def _key_file() -> Path:
    from .storage.xdg import config_dir

    return config_dir() / "master.key"


def _load_from_keyring() -> bytes | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        raw = keyring.get_password(_SERVICE, _KEY_NAME)
    except Exception as e:  # noqa: BLE001 - no daemon, locked keyring, D-Bus down
        log.debug("keyring read failed: %s", e)
        return None
    if not raw:
        return None
    try:
        key = base64.b64decode(raw)
    except Exception:  # noqa: BLE001
        return None
    return key if len(key) == _KEY_BYTES else None


def _store_in_keyring(key: bytes) -> bool:
    try:
        import keyring
    except ImportError:
        return False
    try:
        keyring.set_password(_SERVICE, _KEY_NAME, base64.b64encode(key).decode())
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("keyring write failed: %s", e)
        return False


def _load_from_file() -> bytes | None:
    path = _key_file()
    try:
        if not path.is_file():
            return None
        key = base64.b64decode(path.read_text(encoding="utf-8").strip())
    except Exception as e:  # noqa: BLE001
        log.warning("could not read the key file: %s", e)
        return None
    return key if len(key) == _KEY_BYTES else None


def _store_in_file(key: bytes) -> None:
    path = _key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(base64.b64encode(key).decode(), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError as e:
        # The most consequential of these: this file is the key every proxy
        # password is encrypted with. A readable key makes the encryption
        # decorative, so the failure is reported even though it cannot raise.
        from .storage.xdg import record_permission_fault

        record_permission_fault(path, f"chmod on the key file failed: {e}")
    tmp.replace(path)


def get_master_key() -> bytes:
    """Return the master key, creating it on first use.

    Prefers the OS keychain. Falls back to a 0600 file and says so.
    """
    global _cached_key, _backend
    with _lock:
        if _cached_key is not None:
            return _cached_key

        key = _load_from_keyring()
        if key is not None:
            _backend = "keyring"
            _cached_key = key
            return key

        key = _load_from_file()
        if key is not None:
            # A key file exists. Try to promote it into the keychain so the
            # next start is protected properly, but keep working either way.
            _backend = "keyring" if _store_in_keyring(key) else "file"
            if _backend == "keyring":
                log.info("master key promoted from the key file into the OS keychain")
            _cached_key = key
            return key

        key = _secrets.token_bytes(_KEY_BYTES)
        if _store_in_keyring(key):
            _backend = "keyring"
            log.info("master key created in the OS keychain")
        else:
            _store_in_file(key)
            _backend = "file"
            log.warning(
                "No OS keychain available; the master key is in %s with mode 0600. "
                "Anything able to read that file can read the proxy passwords. "
                "Install the 'keyring' package, or run under a session with a "
                "Secret Service, to fix this.",
                _key_file(),
            )
        _cached_key = key
        return key


def backend() -> str:
    """'keyring', 'file', or 'unknown' until a key has been needed."""
    return _backend


def status() -> dict[str, Any]:
    """Honest state, for /api/health and the console."""
    try:
        get_master_key()
        return {
            "ok": True,
            "backend": _backend,
            "keychain": _backend == "keyring",
            "message": (
                "Secrets are encrypted with a key held in the OS keychain."
                if _backend == "keyring"
                else f"No OS keychain available; the key is in {_key_file()} (0600)."
            ),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": "none", "keychain": False, "message": str(e)}


def reset_cache() -> None:
    """Forget the cached key. For tests and after a key rotation."""
    global _cached_key, _backend
    with _lock:
        _cached_key = None
        _backend = "unknown"


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


def is_encrypted(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt_secret(plaintext: str | None) -> str:
    """Wrap a secret. An empty value stays empty -- nothing to protect."""
    if not plaintext:
        return ""
    if is_encrypted(plaintext):
        return plaintext
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise SecretsUnavailable(
            "the 'cryptography' package is required to store secrets"
        ) from e
    nonce = _secrets.token_bytes(_NONCE_BYTES)
    blob = AESGCM(get_master_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.b64encode(nonce + blob).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    """Unwrap a secret. A legacy plaintext value is returned unchanged.

    That leniency is what lets an existing install keep launching while its
    files are migrated, and it is safe because the prefix is unambiguous: a
    real proxy password does not begin with "mpenc:v1:".
    """
    if not value:
        return ""
    if not is_encrypted(value):
        return value  # legacy plaintext, still readable
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise SecretsUnavailable(
            "the 'cryptography' package is required to read stored secrets"
        ) from e
    raw = base64.b64decode(value[len(_PREFIX):])
    nonce, blob = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    try:
        return AESGCM(get_master_key()).decrypt(nonce, blob, None).decode("utf-8")
    except Exception as e:  # noqa: BLE001 - wrong key or tampered value
        raise SecretsUnavailable(
            "stored secret could not be decrypted: wrong master key, or the "
            "value was modified"
        ) from e


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

# Where secrets live: (loader, saver, secret key names).
_SECRET_FIELDS = ("password", "proxyPass", "proxy_pass", "proxyPassword")


def _encrypt_fields(record: dict[str, Any]) -> bool:
    """Encrypt any secret field in place. True when something changed."""
    changed = False
    for field in _SECRET_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value and not is_encrypted(value):
            record[field] = encrypt_secret(value)
            changed = True
    return changed


def migrate_plaintext_secrets() -> dict[str, int]:
    """Encrypt secrets already written in the clear. Idempotent.

    Returns a count per store, for the startup log. Never raises: a store
    that cannot be migrated is logged and left alone rather than taking the
    application down on boot.
    """
    counts = {"gateways": 0, "pools": 0, "profiles": 0}

    # Defence in depth. This function rewrites the operator's live files, and
    # a test process has no business doing that -- a leaky fixture once let
    # it encrypt 31 real gateway passwords with a key in a pytest temp dir.
    # Tests that mean to exercise migration call it themselves.
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get(
        "MULTIPROFILE_ALLOW_MIGRATION_IN_TESTS"
    ):
        log.debug("secret migration skipped: running under pytest")
        return counts

    try:
        from .storage import gateways as gw

        items = gw.load_gateways(safe=False)
        touched = sum(1 for g in items if _encrypt_fields(g))
        if touched:
            gw._write_gateways(items)
            counts["gateways"] = touched
    except Exception as e:  # noqa: BLE001
        log.warning("gateway secret migration skipped: %s", e)

    try:
        from .storage import pools

        items = pools.load_pools()
        touched = 0
        for pool in items:
            for member in pool.get("members") or []:
                if _encrypt_fields(member):
                    touched += 1
        if touched:
            pools._write(items)
            counts["pools"] = touched
    except Exception as e:  # noqa: BLE001
        log.warning("pool secret migration skipped: %s", e)

    try:
        from .storage import profiles

        items = profiles.load_profiles()
        touched = sum(1 for p in items if _encrypt_fields(p))
        if touched:
            profiles.save_profiles(items)
            counts["profiles"] = touched
    except Exception as e:  # noqa: BLE001
        log.warning("profile secret migration skipped: %s", e)

    return counts
