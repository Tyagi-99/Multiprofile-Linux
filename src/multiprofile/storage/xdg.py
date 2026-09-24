from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

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
    except OSError:  # pragma: no cover - a filesystem without POSIX modes
        pass
    tmp.replace(path)


def harden_existing(path: Path) -> bool:
    """Tighten a file written before this rule existed. True when changed."""
    try:
        if not path.exists():
            return False
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            os.chmod(path, 0o600)
            return True
    except OSError:
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
    return changed
