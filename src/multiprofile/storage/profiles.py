from __future__ import annotations

import json
import logging
import random
import shutil
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import max_profiles, trash_retention_days
from . import db
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


def _seed_defaults_if_empty() -> None:
    """First run: give a new install the demo profile the JSON store did."""
    if db.count_profiles():
        return
    for p in DEFAULT_PROFILES:
        seeded = dict(p)
        _normalize_fields(seeded)
        if not seeded.get("fingerprint"):
            _attach_fingerprint(seeded)
        db.put_profile(seeded)


def import_legacy_json() -> int:
    """Move a pre-SQLite ``profiles.json`` into the database, once.

    Run before the first read. It imports only into an empty table, so it
    cannot clobber rows written since, and it does **not** delete the JSON:
    the file is renamed to ``profiles.json.migrated`` and left in place. That
    is deliberate -- the file is the only copy of the operator's profiles at
    the moment this runs, and a migration that removes its own input has no
    way back if the import turns out to be wrong.

    Returns the number of profiles imported.
    """
    path = profiles_path()
    if not path.exists() or db.count_profiles():
        return 0
    items = _read_json(path, None)
    if not isinstance(items, list):
        log.warning("legacy profiles.json is not a list; not importing")
        return 0
    clean = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        record = dict(item)
        record.pop("pid", None)
        record.pop("isRunning", None)
        record["status"] = "idle"
        _normalize_fields(record)
        _encrypt_profile_secrets(record)
        clean.append(record)
    if not clean:
        return 0
    db.replace_all_profiles(clean)
    backup = path.with_suffix(path.suffix + ".migrated")
    try:
        path.rename(backup)
        log.info("imported %d profiles into the database; kept %s",
                 len(clean), backup.name)
    except OSError as e:
        log.warning("imported %d profiles but could not rename %s: %s",
                    len(clean), path, e)
    return len(clean)


def _decorate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the live runtime view the console expects."""
    runtime = _read_json(runtime_state_path(), {})
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
                    rt.launch_state.value if hasattr(rt.launch_state, "value")
                    else rt.launch_state
                )
                p["cdpPort"] = rt.cdp_port
            else:
                p.setdefault("launchState", None)
                p.setdefault("cdpPort", None)
        except Exception:
            p.setdefault("launchState", None)
            p.setdefault("cdpPort", None)
    return items


#: Which database the one-time migration and seeding have been done for.
#: Keyed by path rather than a bare flag, because the resolved location can
#: change within a process (tests repoint XDG_DATA_HOME, and db._connect
#: reopens when it does).
_prepared_for: str | None = None


def _ensure_ready() -> None:
    """Run the legacy import and the first-run seed once per database.

    This used to be a call to ``load_profiles()`` at the top of every
    accessor, which made ``get_profile`` read and decorate *every* row before
    doing its indexed lookup -- an O(n) prelude to an O(1) query, which is
    most of what the move to SQLite was supposed to remove.
    """
    global _prepared_for
    path = db.db_path()
    if _prepared_for == path:
        return
    with _lock:
        if _prepared_for == path:
            return
        import_legacy_json()
        _seed_defaults_if_empty()
        _prepared_for = path


def load_profiles() -> list[dict[str, Any]]:
    _ensure_ready()
    with _lock:
        return _decorate(db.list_profiles())


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
    """Replace the whole set.

    Kept for the callers whose meaning really is "the set is now this" -- a
    restore from backup, a secrets re-wrap, an import. Ordinary edits must go
    through :func:`update_profile`, which writes one row and therefore cannot
    lose a change to a neighbouring profile.
    """
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
        db.replace_all_profiles(clean)


def set_runtime(profile_id: str, status: str, pid: int | None = None) -> None:
    with _lock:
        runtime = _read_json(runtime_state_path(), {})
        runtime[profile_id] = {"status": status, "pid": pid}
        _write_json(runtime_state_path(), runtime)


def _new_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a complete profile from a create payload, without storing it.

    Split out of :func:`create_profile` so bulk creation can build the whole
    batch first and then write it in one transaction, rather than interleaving
    a fingerprint derivation with a database write N times over.
    """
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
    return profile


def _for_storage(profile: dict[str, Any]) -> dict[str, Any]:
    stored = dict(profile)
    stored.pop("pid", None)
    _encrypt_profile_secrets(stored)
    return stored


def fleet_capacity() -> dict[str, int]:
    """How much room is left in the fleet.

    ``capacity`` is the configured maximum number of *stored* profiles, which
    is a different limit from how many may run at once.
    """
    _ensure_ready()
    cap = max_profiles()
    stored = db.count_profiles()
    return {"stored": stored, "capacity": cap, "free": max(0, cap - stored)}


def create_profile(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_ready()
    profile = _new_profile(payload)
    # Capacity is enforced here, not only in the bulk endpoint: a cap that a
    # loop of single creates can walk straight past is not a cap.
    db.insert_profiles([_for_storage(profile)], capacity=max_profiles())
    return profile


def create_profiles(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create a batch of profiles atomically.

    Either every profile in the batch is stored or none is, and the fleet
    capacity is checked inside the same transaction. Raises
    :class:`~multiprofile.storage.db.FleetFull` if the batch would not fit.
    """
    _ensure_ready()
    if not payloads:
        return []
    built = [_new_profile(p) for p in payloads]
    db.insert_profiles([_for_storage(p) for p in built], capacity=max_profiles())
    return built


def update_profile(profile_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Merge a change into one profile, atomically.

    The read and the write happen inside one transaction, so a concurrent
    update of a *different* profile cannot overwrite this one -- which is
    exactly what the JSON store did, because it rewrote the entire list from
    a snapshot taken before the change.
    """
    _ensure_ready()

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        merged = {**current, **payload, "id": profile_id}
        _normalize_fields(merged)
        _attach_fingerprint(merged)
        merged.pop("pid", None)
        merged.pop("isRunning", None)
        _encrypt_profile_secrets(merged)
        return merged

    updated = db.mutate_profile(profile_id, _apply)
    if updated is None:
        return None
    return _decorate([dict(updated)])[0]


def get_profile(profile_id: str) -> dict[str, Any] | None:
    _ensure_ready()
    record = db.get_profile_row(profile_id)
    if record is None:
        return None
    return _decorate([record])[0]


def delete_profile(profile_id: str, *, purge_storage: bool = True) -> bool:
    """Remove a profile permanently.

    This is the irreversible one, and it is no longer what a delete from the
    console reaches: that goes to :func:`trash_profile`. Permanent deletion is
    what emptying the trash does, and what a caller asks for explicitly.
    """
    _ensure_ready()
    if not db.delete_profile_row(profile_id):
        return False
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


def _profile_files(profile_id: str) -> tuple[Path, Path | None]:
    from ..cookies_store import cookies_path
    from .xdg import data_dir

    chrome_root = data_dir() / "chrome-profiles" / profile_id
    try:
        cookies = cookies_path(profile_id)
    except Exception:  # noqa: BLE001 - a missing cookie jar is not a failure
        cookies = None
    return chrome_root, cookies


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def trash_profile(profile_id: str) -> dict[str, Any] | None:
    """Move a profile to the trash, keeping everything it owns.

    The distinction that matters is on disk. A permanent delete removes
    ``chrome-profiles/<id>``, which holds the cookies, logins and warmed-up
    session state -- in an anti-detect browser that directory *is* the asset,
    and it cannot be recreated by making a new profile with the same settings.
    Trashing leaves it exactly where it is, so a restore is a move of one row
    rather than a recovery.
    """
    _ensure_ready()
    # Measured here, once, and stored: nothing writes to a trashed profile, so
    # the size taken now is the size for as long as it is in the trash. The
    # first version measured it while rendering the list instead, which is a
    # full tree walk per row on every page load -- a real Chromium profile
    # holds a few thousand files, so at 150 trashed that was ~2 s of stat()
    # calls to show a column.
    chrome_root, _ = _profile_files(profile_id)
    size = _dir_bytes(chrome_root) if chrome_root.exists() else 0
    profile = db.trash_profile_row(profile_id, disk_bytes=size)
    if profile is None:
        return None
    with _lock:
        runtime = _read_json(runtime_state_path(), {})
        if profile_id in runtime:
            runtime.pop(profile_id, None)
            _write_json(runtime_state_path(), runtime)
    return profile


def restore_profile(profile_id: str) -> dict[str, Any] | None:
    """Move a profile back out of the trash.

    Raises :class:`~multiprofile.storage.db.FleetFull` if the fleet has no
    room: restoring is a create as far as capacity is concerned.
    """
    _ensure_ready()
    profile = db.restore_profile_row(profile_id, capacity=max_profiles())
    if profile is None:
        return None
    restored = dict(profile)
    # Trash-only fields do not belong on a live profile.
    restored.pop("deletedAt", None)
    restored.pop("diskBytes", None)
    return _decorate([restored])[0]


def list_trash() -> list[dict[str, Any]]:
    """Trashed profiles, newest first, each with what it is still holding.

    ``diskBytes`` is reported because the trash's whole purpose is that it
    does not free the disk. An operator deciding whether to empty it needs to
    know what they would get back.
    """
    _ensure_ready()
    out = []
    for profile in db.list_trash_rows():
        profile.pop("pid", None)
        profile["status"] = "trashed"
        out.append(profile)
    return out


def purge_profile(profile_id: str) -> bool:
    """Delete a trashed profile for good, including its files."""
    _ensure_ready()
    if not db.purge_trash_row(profile_id):
        return False
    chrome_root, cookies = _profile_files(profile_id)
    if chrome_root.exists():
        try:
            shutil.rmtree(chrome_root)
        except OSError as e:
            log.warning("could not remove %s: %s", chrome_root, e)
    if cookies is not None and cookies.exists():
        try:
            cookies.unlink(missing_ok=True)
        except OSError as e:
            log.warning("could not remove %s: %s", cookies, e)
    return True


def empty_trash() -> int:
    """Purge everything in the trash. Returns how many were removed."""
    _ensure_ready()
    ids = [str(p.get("id") or "") for p in db.list_trash_rows()]
    return sum(1 for pid in ids if pid and purge_profile(pid))


def purge_expired_trash(retention_days: int | None = None) -> int:
    """Purge trashed profiles older than the retention window.

    Called from the trash endpoints rather than from a timer: the trash is
    only observable when someone looks at it, so that is when it is worth
    reconciling, and a background thread doing irreversible deletions is a
    worse thing to own than a slightly late purge.
    """
    _ensure_ready()
    days = trash_retention_days() if retention_days is None else int(retention_days)
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    removed = 0
    for pid in db.trash_ids_older_than(cutoff):
        if purge_profile(pid):
            removed += 1
    if removed:
        log.info("purged %d profile(s) from the trash after %d days", removed, days)
    return removed


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


def _matches_status(profile: dict[str, Any], status: str) -> bool:
    """The status predicate, kept in one place.

    Status is not stored -- it is synthesised on read from the runtime
    registry -- so it cannot be a WHERE clause without the database
    disagreeing with the live process table. It stays a Python filter, and
    this is the only definition of it.
    """
    st = status.lower().strip()
    if st == "running":
        return profile.get("status") == "running" or bool(profile.get("isRunning"))
    if st == "idle":
        return not (profile.get("status") == "running" and profile.get("pid"))
    return (profile.get("status") or "").lower() == st


def filter_profiles(
    *,
    q: str | None = None,
    client_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Offset-paginated filtered profiles.

    The text, client and tag predicates run in SQL now. They used to run in
    Python over a freshly loaded copy of the entire store -- and the caller
    that wanted every match called this in chunks of 500, so the whole store
    was read and decorated once per chunk.
    """
    _ensure_ready()
    result = db.query_profiles(q=q, client_id=client_id, tag=tag)
    items = _decorate(result["items"])
    total = result["total"]
    if status:
        items = [p for p in items if _matches_status(p, status)]
        total = len(items)
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    page = items[offset : offset + limit]
    return {"items": page, "total": total, "limit": limit, "offset": offset}


def query_page(
    *,
    q: str | None = None,
    client_id: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    after_id: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """One keyset page, cut in SQL.

    This is what the list endpoint wants: the database orders, filters and
    limits, so serving 25 rows reads 25 rows instead of the whole table.

    A status filter is the exception, and deliberately so. Status is
    synthesised on read, so SQL cannot cut on it; asking for one falls back to
    scanning the matching set. That is acceptable because status narrows to
    what is *running*, which is bounded by the browser slot limit -- an
    operator filtering by status is asking about a handful of profiles, not
    ten thousand. The cost is stated rather than hidden.
    """
    limit = max(1, min(int(limit or 25), 500))
    _ensure_ready()

    if status:
        result = db.query_profiles(q=q, client_id=client_id, tag=tag)
        items = [p for p in _decorate(result["items"])
                 if _matches_status(p, status)]
        total = len(items)
        if after_id:
            items = [p for p in items if str(p.get("id") or "") > after_id]
        page = items[:limit]
        has_more = len(items) > limit
    else:
        # limit + 1 so "is there a next page" costs one row, not a second
        # query and not a full count of the remainder.
        result = db.query_profiles(
            q=q, client_id=client_id, tag=tag, after_id=after_id, limit=limit + 1
        )
        total = result["total"]
        rows = result["items"]
        has_more = len(rows) > limit
        page = _decorate(rows[:limit])

    return {"items": page, "total": total, "limit": limit, "has_more": has_more}


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
