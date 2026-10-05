from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("multiprofile.storage.xdg")

APP = "multiprofile"

def _xdg(env: str, default: str) -> Path:
    base = os.environ.get(env) or str(Path.home() / default)
    p = Path(base) / APP
    p.mkdir(parents=True, exist_ok=True)
    return p

def secure_write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write JSON to ``path`` atomically and owner-only.

    Every operator data file goes through here so the permission decision is
    made once. profiles.json used to be written with a bare ``write_text``,
    which meant the process umask decided: 0664 in practice, world-readable,
    on a file whose schema carries ``proxyPass``. gateways.json and pools.json
    were 0600 only because two separate authors remembered.

    The mode is set on the temporary file BEFORE the rename, so the final
    path is never briefly readable by anyone else.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError as e:
        # Not raised: a filesystem with no permission bits is a legitimate
        # place to run. Recorded, because the file is about to be renamed into
        # place at whatever the umask allows, and a secret landing 0644 in
        # silence is exactly how F4 happened.
        record_permission_fault(path, f"chmod on write failed: {e}")
    tmp.replace(path)


#: Permission tightenings that did not take, kept so they can be reported.
#:
#: Every one of these was a swallowed ``OSError`` around a ``chmod(0o600)``.
#: That is the quietest possible version of **F4**, where the cookie jar and
#: the cleartext proxy password were found world-readable: the code tries to
#: tighten the file, fails, and says nothing, so the next reader sees a 0600
#: intent in the source and a 0644 file on disk. The handler still must not
#: raise -- a filesystem with no permission bits is a legitimate place to run
#: -- but "must not raise" was never a reason for "must not tell anyone".
#:
#: Bounded and in-memory for the same reason the identity faults are (F11):
#: the audience is an operator looking at the console now, and the durable
#: record is the audit log.
_MAX_PERMISSION_FAULTS = 100
_permission_faults: deque = deque(maxlen=_MAX_PERMISSION_FAULTS)
_permission_lock = threading.Lock()


def record_permission_fault(path: Path | str, detail: str) -> None:
    """Note that a file could not be made owner-only. Never raises.

    Called from inside ``except`` blocks, so a recorder that could throw would
    turn a permissions problem into a crash -- worse than the bug it reports,
    and the reason such a policy gets reverted.
    """
    try:
        with _permission_lock:
            _permission_faults.append({
                "path": str(path),
                "detail": str(detail)[:200],
                "ts": time.time(),
            })
        log.warning("could not tighten permissions on %s: %s", path, detail)
    except Exception:  # noqa: BLE001,S110 - reporting must not be able to fail
        pass


def permission_faults() -> list[dict]:
    with _permission_lock:
        return list(_permission_faults)


def permission_health() -> dict:
    """The block ``/api/health`` carries.

    ``faults: []`` is a statement -- every file this process wrote or repaired
    is owner-only -- not an absence. A block that appeared only on trouble
    could not be used to confirm the absence of it.
    """
    faults = permission_faults()
    return {
        "faults": faults,
        "faultCount": len(faults),
        "truncated": len(faults) >= _MAX_PERMISSION_FAULTS,
    }


def harden_existing(path: Path) -> bool:
    """Tighten a file written before this rule existed. True when changed.

    A failure here leaves a readable file readable, so it is recorded rather
    than folded into the ``False`` that also means "already fine" -- those two
    outcomes were indistinguishable to every caller.
    """
    try:
        if not path.exists():
            return False
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            os.chmod(path, 0o600)
            return True
    except OSError as e:
        record_permission_fault(path, f"could not tighten an existing file: {e}")
        return False
    return False


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")

def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")

def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")

def profiles_path() -> Path:
    return data_dir() / "profiles.json"

def clients_path() -> Path:
    return data_dir() / "clients.json"

def templates_path() -> Path:
    return data_dir() / "templates.json"

def audit_path() -> Path:
    return data_dir() / "audit.jsonl"

def backups_dir() -> Path:
    p = data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p

def runtime_state_path() -> Path:
    return cache_dir() / "runtime.json"


def github_accounts_path() -> Path:
    return data_dir() / "github-accounts.json"


def control_db_path() -> Path:
    """SQLite control-plane truth store (profiles_runtime, jobs)."""
    p = data_dir() / "control.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def chrome_profile_dir(profile_id: str) -> Path:
    p = data_dir() / "chrome-profiles" / profile_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# Files that must never be group- or world-readable. Listed once so a new
# store cannot quietly join the set without someone deciding.
def _sensitive_paths() -> list[Path]:
    d = data_dir()
    paths = [
        profiles_path(), clients_path(), templates_path(), audit_path(),
        d / "gateways.json", d / "pools.json", d / "github-accounts.json",
        d / "control.db", d / "control.db-wal", d / "control.db-shm",
    ]
    cookies = d / "cookies"
    if cookies.is_dir():
        paths.extend(sorted(cookies.glob("*.json")))
    return paths


def harden_data_dir() -> list[str]:
    """Tighten operator files written before secure_write_json existed.

    profiles.json shipped at 0664 and control.db at 0644 on a real install.
    Fixing new writes does nothing for the files already sitting on disk, so
    this runs once at startup. Returns the names it changed, for the log.
    """
    changed: list[str] = []
    for path in _sensitive_paths():
        if harden_existing(path):
            changed.append(path.name)
    # Failures are in the registry rather than in this return value, which
    # callers treat as "what I fixed". `db._harden` used to claim this pass
    # "reports what it could not fix"; it did not, and now something does.
    return changed


# Files the old per-profile extension staging step wrote. Named here so the
# purge below is explicit about what it is deleting and why.
LEGACY_STAGED_SECRETS = ("proxy.json", "cookies.json")


def purge_legacy_extension_staging(profile_id: str | None = None) -> list[str]:
    """Delete the per-profile extension copies earlier versions staged.

    Those directories hold two files that should never have been on disk:

      ``proxy.json``   the proxy username and password in CLEARTEXT, decrypted
                       straight out of the OS keychain
      ``cookies.json`` the profile's entire cookie jar

    Both were written with a bare ``write_text``, so the process umask decided
    the mode: 0644 on a real install, world-readable — undoing the encryption
    the keychain had just provided. Every other writer in this codebase goes
    through :func:`secure_write_json`.

    The launcher no longer writes or loads any of it (the identity script goes
    over CDP), but removing the writer does nothing for the copies already
    sitting in a user's data directory. Tightening the mode would still leave a
    plaintext password on disk that nothing reads, so the directory is
    **removed**. Returns the profile ids cleaned, for the log.

    Pass ``profile_id`` to clean one profile; omit it to sweep them all.
    """
    import shutil

    root = data_dir() / "chrome-profiles"
    if profile_id:
        candidates = [root / profile_id]
    elif root.is_dir():
        candidates = sorted(p for p in root.iterdir() if p.is_dir())
    else:
        return []

    cleaned: list[str] = []
    for profile_dir in candidates:
        staged = profile_dir / "extension"
        if not staged.is_dir():
            continue
        try:
            shutil.rmtree(staged)
            cleaned.append(profile_dir.name)
        except OSError:
            # Leave it rather than crash a launch or a startup; the next
            # sweep tries again. Tighten what we could not remove.
            for name in LEGACY_STAGED_SECRETS:
                harden_existing(staged / name)
    return cleaned
