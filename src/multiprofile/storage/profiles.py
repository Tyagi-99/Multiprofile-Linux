from __future__ import annotations
import json
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .xdg import profiles_path, runtime_state_path

_lock = threading.RLock()

DEFAULT_PROFILES = [
    {
        "id": "profile_demo_desktop",
        "name": "Demo Desktop",
        "color": "#0ea5e9",
        "notes": "Sample profile for learning",
        "userAgentId": "win_chrome",
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "isMobile": False,
        "proxyType": "none",
        "customProxy": "",
        "timezone": "America/New_York",
        "startUrl": "https://example.com",
        "createdAt": "2026-09-20",
        "status": "idle",
        "pid": None,
    }
]


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_profiles() -> list[dict[str, Any]]:
    with _lock:
        items = _read_json(profiles_path(), None)
        if items is None:
            _write_json(profiles_path(), DEFAULT_PROFILES)
            items = list(DEFAULT_PROFILES)
        # merge runtime status
        runtime = _read_json(runtime_state_path(), {})
        for p in items:
            st = runtime.get(p.get("id"), {})
            p["status"] = st.get("status", p.get("status", "idle"))
            p["pid"] = st.get("pid")
        return items


def save_profiles(items: list[dict[str, Any]]) -> None:
    with _lock:
        # strip volatile runtime before persist
        clean = []
        for p in items:
            q = dict(p)
            q.pop("pid", None)
            q["status"] = "idle"
            clean.append(q)
        _write_json(profiles_path(), clean)


def set_runtime(profile_id: str, status: str, pid: int | None = None) -> None:
    with _lock:
        runtime = _read_json(runtime_state_path(), {})
        runtime[profile_id] = {"status": status, "pid": pid}
        _write_json(runtime_state_path(), runtime)


def create_profile(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_profiles()
    profile = dict(payload)
    profile["id"] = "profile_" + uuid.uuid4().hex[:8]
    profile.setdefault("createdAt", date.today().isoformat())
    profile.setdefault("color", "#0ea5e9")
    profile["status"] = "idle"
    profile["pid"] = None
    items.append(profile)
    save_profiles(items)
    return profile


def update_profile(profile_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    items = load_profiles()
    for i, p in enumerate(items):
        if p.get("id") == profile_id:
            items[i] = {**p, **payload, "id": profile_id}
            save_profiles(items)
            return items[i]
    return None


def get_profile(profile_id: str) -> dict[str, Any] | None:
    for p in load_profiles():
        if p.get("id") == profile_id:
            return p
    return None
