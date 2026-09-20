from __future__ import annotations
import json
import random
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
        "proxyHost": "",
        "proxyPort": None,
        "proxyUser": "",
        "proxyPass": "",
        "timezone": "America/New_York",
        "language": "en-US",
        "webrtcMode": "default",
        "seed": 424242,
        "startUrl": "https://example.com",
        "fingerprint": None,
        "createdAt": "2026-09-20",
        "status": "idle",
        "pid": None,
    }
]


def _new_seed() -> int:
    return random.randint(1, 2_147_483_646)


def _ensure_seed(profile: dict[str, Any]) -> None:
    if profile.get("seed") is None:
        profile["seed"] = _new_seed()
    else:
        try:
            profile["seed"] = abs(int(profile["seed"])) % (2**31 - 1) or 1
        except (TypeError, ValueError):
            profile["seed"] = _new_seed()


def _attach_fingerprint(profile: dict[str, Any]) -> None:
    try:
        from ..fingerprint import build_fingerprint

        profile["fingerprint"] = build_fingerprint(profile)
    except Exception:
        pass


def _normalize_fields(profile: dict[str, Any]) -> dict[str, Any]:
    profile.setdefault("language", "en-US")
    profile.setdefault("webrtcMode", "default")
    profile.setdefault("notes", profile.get("notes") or "")
    profile.setdefault("startUrl", profile.get("startUrl") or "https://example.com")
    profile.setdefault("customProxy", profile.get("customProxy") or "")
    profile.setdefault("proxyHost", profile.get("proxyHost") or "")
    profile.setdefault("proxyPort", profile.get("proxyPort"))
    profile.setdefault("proxyUser", profile.get("proxyUser") or "")
    profile.setdefault("proxyPass", profile.get("proxyPass") or "")
    profile.setdefault("proxyType", profile.get("proxyType") or "none")
    profile.setdefault("timezone", profile.get("timezone") or "America/New_York")
    # Sync structured proxy fields from customProxy if host empty
    if profile.get("customProxy") and not profile.get("proxyHost"):
        try:
            from ..proxy_util import parse_proxy

            parsed = parse_proxy(profile["customProxy"], "custom")
            if parsed:
                profile["proxyHost"] = parsed["host"]
                profile["proxyPort"] = parsed["port"]
                if parsed.get("username"):
                    profile["proxyUser"] = parsed["username"]
                if parsed.get("password"):
                    profile["proxyPass"] = parsed["password"]
        except Exception:
            pass
    _ensure_seed(profile)
    return profile


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
            for p in DEFAULT_PROFILES:
                _normalize_fields(p)
                if not p.get("fingerprint"):
                    _attach_fingerprint(p)
            _write_json(profiles_path(), DEFAULT_PROFILES)
            items = list(DEFAULT_PROFILES)
        runtime = _read_json(runtime_state_path(), {})
        for p in items:
            _normalize_fields(p)
            st = runtime.get(p.get("id"), {})
            p["status"] = st.get("status", p.get("status", "idle"))
            p["pid"] = st.get("pid")
            # SPA compatibility: isRunning
            p["isRunning"] = p.get("status") == "running" and bool(p.get("pid"))
        return items


def save_profiles(items: list[dict[str, Any]]) -> None:
    with _lock:
        clean = []
        for p in items:
            q = dict(p)
            q.pop("pid", None)
            q.pop("isRunning", None)
            q["status"] = "idle"
            _normalize_fields(q)
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
    profile.setdefault("language", "en-US")
    profile.setdefault("webrtcMode", "default")
    profile.setdefault("notes", "")
    profile.setdefault("startUrl", "https://example.com")
    profile.setdefault("proxyHost", "")
    profile.setdefault("proxyPort", None)
    profile.setdefault("proxyUser", "")
    profile.setdefault("proxyPass", "")
    if "seed" not in profile or profile.get("seed") is None:
        profile["seed"] = _new_seed()
    profile["status"] = "idle"
    profile["pid"] = None
    _normalize_fields(profile)
    _attach_fingerprint(profile)
    items.append(profile)
    save_profiles(items)
    return profile


def update_profile(profile_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    items = load_profiles()
    for i, p in enumerate(items):
        if p.get("id") == profile_id:
            merged = {**p, **payload, "id": profile_id}
            _normalize_fields(merged)
            _attach_fingerprint(merged)
            items[i] = merged
            save_profiles(items)
            return items[i]
    return None


def get_profile(profile_id: str) -> dict[str, Any] | None:
    for p in load_profiles():
        if p.get("id") == profile_id:
            return p
    return None


def delete_profile(profile_id: str) -> bool:
    items = load_profiles()
    new_items = [p for p in items if p.get("id") != profile_id]
    if len(new_items) == len(items):
        return False
    save_profiles(new_items)
    return True


def duplicate_profile(profile_id: str) -> dict[str, Any] | None:
    src = get_profile(profile_id)
    if not src:
        return None
    payload = dict(src)
    payload.pop("id", None)
    payload.pop("pid", None)
    payload.pop("status", None)
    payload.pop("isRunning", None)
    payload.pop("fingerprint", None)
    payload["seed"] = _new_seed()
    name = payload.get("name") or "Profile"
    if not name.endswith(" (copy)"):
        payload["name"] = f"{name} (copy)"
    return create_profile(payload)
