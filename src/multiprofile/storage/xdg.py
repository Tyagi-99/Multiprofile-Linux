from __future__ import annotations
import os
from pathlib import Path

APP = "multiprofile"

def _xdg(env: str, default: str) -> Path:
    base = os.environ.get(env) or str(Path.home() / default)
    p = Path(base) / APP
    p.mkdir(parents=True, exist_ok=True)
    return p

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

def chrome_profile_dir(profile_id: str) -> Path:
    p = data_dir() / "chrome-profiles" / profile_id
    p.mkdir(parents=True, exist_ok=True)
    return p
