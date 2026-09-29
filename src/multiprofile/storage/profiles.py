from __future__ import annotations

import json
import logging
import random
import shutil
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .xdg import profiles_path, runtime_state_path, secure_write_json

log = logging.getLogger("multiprofile.storage.profiles")
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
        "clientId": None,
        "tags": [],
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
    # Assigned, not setdefault: an explicit null from a cleared field is
    # already a key, so setdefault would leave None in place and the launcher
    # would be handed it (F21). Falsy of any shape means "use the default".
    profile["notes"] = profile.get("notes") or ""
    profile["startUrl"] = profile.get("startUrl") or "https://example.com"
    profile.setdefault("customProxy", profile.get("customProxy") or "")
    profile.setdefault("proxyHost", profile.get("proxyHost") or "")
    profile.setdefault("proxyPort", profile.get("proxyPort"))
    profile.setdefault("proxyUser", profile.get("proxyUser") or "")
    profile.setdefault("proxyPass", profile.get("proxyPass") or "")
    profile.setdefault("proxyType", profile.get("proxyType") or "none")
    # America/New_York is a fallback, not a decision. Recording which it was
    # is what lets a launch fill the timezone and language in from the exit IP
    # instead of announcing New York from every proxy in the world: before
    # this, a fleet on Brazilian, German and Indian exits all claimed the same
    # city, which is both a contradiction per profile and a value they shared.
    #
    # An operator who deliberately picks America/New_York is left alone --
    # that is the whole point of distinguishing the two.
    if not (profile.get("timezone") or "").strip():
        profile["geoAuto"] = True
    if not (profile.get("language") or "").strip():
        profile["geoAuto"] = True
    profile.setdefault("timezone", profile.get("timezone") or "America/New_York")
    profile.setdefault("geoAuto", False)
    profile.setdefault("clientId", profile.get("clientId"))
    tags = profile.get("tags")
    if tags is None:
        profile["tags"] = []
    elif isinstance(tags, str):
        profile["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        profile["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    else:
        profile["tags"] = []
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
    # Owner-only: the profile schema carries proxyPass. This used to be a
    # bare write_text, so the umask decided and the file landed 0664.
    secure_write_json(path, data)


def load_profiles() -> list[dict[str, Any]]:
    with _lock:
        items = _read_json(profiles_path(), None)
        if items is not None and not isinstance(items, list):
            # A corrupted store that happens to hold a JSON object used to be
            # iterated as a dict, handing its KEYS to _normalize_fields.
            log.warning("profiles.json is not a list (%s); treating as empty",
                        type(items).__name__)
            items = []
        if items is None:
            for p in DEFAULT_PROFILES:
                _normalize_fields(p)
                if not p.get("fingerprint"):
                    _attach_fingerprint(p)
            _write_json(profiles_path(), DEFAULT_PROFILES)
            items = list(DEFAULT_PROFILES)
        runtime = _read_json(runtime_state_path(), {})
        bad = [p for p in items if not isinstance(p, dict)]
        if bad:
            log.warning("dropping %d malformed profile record(s)", len(bad))
            items = [p for p in items if isinstance(p, dict)]
        if not isinstance(runtime, dict):
            runtime = {}
        for p in items:
            _normalize_fields(p)
            st = runtime.get(p.get("id"), {})
            p["status"] = st.get("status", p.get("status", "idle"))
            p["pid"] = st.get("pid")
            # SPA compatibility: isRunning
            p["isRunning"] = p.get("status") == "running" and bool(p.get("pid"))
            p["isQueued"] = p.get("status") == "queued"
            p["isFailed"] = p.get("status") == "failed"
            # Runtime registry (in-memory) — no DB migration
            try:
                from ..browser.runtime_state import get_runtime

                rt = get_runtime(p.get("id") or "")
                if rt:
                    p["launchState"] = (
                        rt.launch_state.value if hasattr(rt.launch_state, "value") else rt.launch_state
                    )
                    p["cdpPort"] = rt.cdp_port
                else:
                    p.setdefault("launchState", None)
                    p.setdefault("cdpPort", None)
            except Exception:
                p.setdefault("launchState", None)
                p.setdefault("cdpPort", None)
        return items


def _encrypt_profile_secrets(profile: dict[str, Any]) -> None:
    """Wrap proxyPass before the profile reaches disk.

    Everything else on a profile -- name, tags, user agent, timezone -- is
    not a secret and stays readable, so profiles.json remains diffable and
    the storage move planned for later is not fighting an encrypted blob.
    """
    from ..secrets import encrypt_secret, is_encrypted

    pw = profile.get("proxyPass")
    if isinstance(pw, str) and pw and not is_encrypted(pw):
        profile["proxyPass"] = encrypt_secret(pw)


def save_profiles(items: list[dict[str, Any]]) -> None:
    with _lock:
        clean = []
        for p in items:
            q = dict(p)
            q.pop("pid", None)
            q.pop("isRunning", None)
            q["status"] = "idle"
            _normalize_fields(q)
            _encrypt_profile_secrets(q)
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
    profile.setdefault("clientId", payload.get("clientId"))
    profile.setdefault("tags", payload.get("tags") or [])
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


def delete_profile(profile_id: str, *, purge_storage: bool = True) -> bool:
    """Remove profile metadata; optionally purge chrome-profiles/<id> and cookies."""
    items = load_profiles()
    new_items = [p for p in items if p.get("id") != profile_id]
    if len(new_items) == len(items):
        return False
    save_profiles(new_items)
    with _lock:
        runtime = _read_json(runtime_state_path(), {})
        if profile_id in runtime:
            runtime.pop(profile_id, None)
            _write_json(runtime_state_path(), runtime)
    if purge_storage:
        from .xdg import data_dir
        chrome_root = data_dir() / "chrome-profiles" / profile_id
        if chrome_root.exists():
            try:
                shutil.rmtree(chrome_root)
            except Exception:
                pass
        try:
            from ..cookies_store import cookies_path
            cp = cookies_path(profile_id)
            if cp.exists():
                cp.unlink(missing_ok=True)
        except Exception:
            pass
    return True


def clone_profile(
    profile_id: str,
    *,
    mode: str = "config_only",
    name: str | None = None,
) -> dict[str, Any] | None:
    """Clone a profile with explicit semantics.

    Modes:
        config_only          — copy configuration, generate new seed, no cookies/session
        config_and_cookies   — copy config + cookies, generate new seed
        full_session         — copy config + cookies + Chrome user-data-dir, new seed

    The new profile always receives a fresh identity seed so two profiles never
    accidentally share the same fingerprint.  'full_session' is the only mode
    that copies actual browser session data and should be used only for explicit
    session backups, not for routine profile creation.
    """
    import shutil as _shutil

    from ..cookies_store import load_cookies as _lc
    from ..cookies_store import save_cookies as _sc
    from .xdg import chrome_profile_dir as _cpd

    src = get_profile(profile_id)
    if not src:
        return None

    payload = dict(src)
    # Strip runtime-only fields
    for f in ("id", "pid", "status", "isRunning", "isQueued", "isFailed",
              "launchState", "cdpPort", "fingerprint"):
        payload.pop(f, None)

    # Always generate a new seed — never duplicate a live fingerprint
    payload["seed"] = _new_seed()
    payload.pop("fingerprint", None)  # force rebuild on next launch

    # Name
    base_name = name or src.get("name") or "Profile"
    suffix_map = {
        "config_only": " (clone)",
        "config_and_cookies": " (clone+cookies)",
        "full_session": " (session clone)",
    }
    if not name:
        payload["name"] = base_name.rstrip(" (clone)").rstrip(" (copy)") + suffix_map.get(mode, " (clone)")

    new_profile = create_profile(payload)
    new_id = new_profile["id"]

    if mode in ("config_and_cookies", "full_session"):
        try:
            cookies = _lc(profile_id)
            if cookies:
                _sc(new_id, cookies)
        except Exception as e:
            log.warning("clone: cookie copy failed: %s", e)  # non-fatal

    if mode == "full_session":
        try:
            src_dir = _cpd(profile_id) / "user-data"
            dst_dir = _cpd(new_id) / "user-data"
            if src_dir.exists():
                if dst_dir.exists():
                    _shutil.rmtree(dst_dir)
                _shutil.copytree(src_dir, dst_dir)
        except Exception as e:
            log.warning("clone: user-data copy failed: %s", e)  # non-fatal

    return new_profile


def duplicate_profile(profile_id: str) -> dict[str, Any] | None:
    """Backwards-compatible alias for clone_profile(mode='config_only')."""
    return clone_profile(profile_id, mode="config_only")


def filter_profiles(
    *,
    q: str | None = None,
    client_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    items = load_profiles()
    filtered = items
    if q:
        ql = q.lower().strip()
        filtered = [
            p
            for p in filtered
            if ql in (p.get("name") or "").lower()
            or ql in (p.get("notes") or "").lower()
            or ql in (p.get("userAgent") or "").lower()
            or any(ql in str(t).lower() for t in (p.get("tags") or []))
        ]
    if client_id:
        filtered = [p for p in filtered if p.get("clientId") == client_id]
    if status:
        st = status.lower().strip()
        if st == "running":
            filtered = [p for p in filtered if p.get("status") == "running" or p.get("isRunning")]
        elif st == "idle":
            filtered = [p for p in filtered if not (p.get("status") == "running" and p.get("pid"))]
        else:
            filtered = [p for p in filtered if (p.get("status") or "").lower() == st]
    if tag:
        tl = tag.lower().strip()
        filtered = [p for p in filtered if any(tl == str(t).lower() for t in (p.get("tags") or []))]
    total = len(filtered)
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    page = filtered[offset : offset + limit]
    return {"items": page, "total": total, "limit": limit, "offset": offset}


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import os
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def reap_orphans() -> list[dict[str, Any]]:
    """Mark profiles idle when status=running but pid is dead."""
    try:
        from ..browser import manager as browser_manager
    except Exception:
        browser_manager = None  # type: ignore

    reaped: list[dict[str, Any]] = []
    for p in load_profiles():
        if p.get("status") == "running":
            pid = p.get("pid")
            if not pid_alive(pid):
                set_runtime(p["id"], "idle", None)
                if browser_manager is not None:
                    try:
                        browser_manager.release_slot(p["id"])
                    except Exception:
                        pass
                reaped.append({"profileId": p["id"], "oldPid": pid})
    return reaped
