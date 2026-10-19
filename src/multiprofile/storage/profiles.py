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
    """Derive and attach this profile's fingerprint.

    F11, on the identity path. A failure here used to leave the profile with
    no fingerprint and say nothing, which is indistinguishable from a profile
    that simply has not been launched yet -- the launcher builds one on
    demand for exactly that case. So the profile was created, looked normal
    in the console, and the reason its identity was derived later rather than
    at creation was nowhere on record.
    """
    try:
        from ..fingerprint import build_fingerprint

        profile["fingerprint"] = build_fingerprint(profile)
    except Exception as e:  # noqa: BLE001 - creation must not fail over this
        try:
            from ..browser import identity_faults

            identity_faults.record(
                "fingerprint_not_built",
                f"could not derive a fingerprint at creation ({e}); the first "
                "launch will build one instead",
                where="storage.profiles",
                profile_id=str(profile.get("id") or ""),
            )
        except Exception:  # noqa: BLE001,S110 - the recorder is the last thing that may break a create
            pass


def _resolve_user_agent_id(profile: dict[str, Any]) -> None:
    """Turn a picked catalogue id into the identity it names.

    ``userAgentId`` was accepted by the API, validated, aliased from ``uaId``,
    stored on the profile and carried by templates -- and read by nothing that
    builds a fingerprint, which derives everything from ``userAgent`` and
    ``isMobile``. Picking "macOS Sonoma - Chrome 131" therefore produced a
    profile claiming Windows, silently. That is F16 and F19's shape again: a
    control present in the vocabulary and absent from the path.

    A UA the operator typed still wins: they are matching a fingerprint they
    got elsewhere, and overwriting it from a leftover id would break the
    pairing they came for.

    A UA this catalogue produced does *not* win, and the distinction matters
    on update. Changing the picker from Windows to macOS leaves the previous
    resolution sitting in ``userAgent``; treating that as "the operator chose
    this" would make the picker work exactly once per profile and then jam.
    A UA that matches a catalogue entry was derived, so a newer id replaces
    it.

    **Provenance is recorded rather than guessed.** ``userAgentFromId`` is
    written whenever this function resolves a UA, and it is the signal a
    later pick reads. Guessing from the string cannot work in either
    direction: an exact catalogue match decays, because
    ``user_agent_catalog()`` realigns to the installed engine and a UA
    written at 151 stops matching after an upgrade to 154 -- which jams the
    picker in exactly the way this docstring says it must not, just on an
    upgrade rather than on the second pick. Found by setting
    ``userAgentId: linux_chrome`` on a profile whose UA the catalogue had
    written at 151: the id was stored and the Windows UA stayed. And
    comparing without the version is worse, because a plain Linux-Chrome
    string is both what ``linux_chrome`` resolves to and something an
    operator may well paste; ``test_an_explicit_user_agent_is_not_overwritten``
    is that case, and it must keep passing.

    Profiles created before the marker existed fall back to the exact match,
    which is conservative in the direction that matters here: it may decline
    to replace a UA this product wrote, and the operator can clear the field
    to get what they asked for. It will not overwrite one they chose.
    """
    uaid = (profile.get("userAgentId") or "").strip()
    if not uaid:
        return
    existing = (profile.get("userAgent") or "").strip()
    try:
        from ..api.user_agents import is_catalogue_user_agent, resolve_user_agent_id

        ours = (profile.get("userAgentFromId") or "").strip()
        if existing and not ours and not is_catalogue_user_agent(existing):
            return
        entry = resolve_user_agent_id(uaid)
    except Exception as e:  # noqa: BLE001 - a catalogue failure must not block a create
        log.warning("could not resolve userAgentId %r: %s", uaid, e)
        return
    if not entry:
        # Unknown ids are refused at the API boundary; reaching here means a
        # stored profile predates the catalogue entry it names. Leaving the UA
        # empty lets the fingerprint pool default apply, which is the old
        # behaviour -- wrong to invent an identity from a name we do not know.
        log.warning("profile names an unknown userAgentId %r; leaving the UA unset", uaid)
        return
    profile["userAgent"] = entry.get("ua") or ""
    profile["isMobile"] = bool(entry.get("isMobile"))
    # The record that this UA is ours, so neither the picker nor the version
    # aligner has to infer it from the string later.
    profile["userAgentFromId"] = uaid


def _normalize_fields(profile: dict[str, Any]) -> dict[str, Any]:
    _resolve_user_agent_id(profile)
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
        except Exception:  # noqa: BLE001,S110 - nothing here can be acted on; the caller's result is unchanged
            pass
    _ensure_seed(profile)
    return profile


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
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


def _status_for_state(state: str, stored: str | None) -> str:
    """Console status word for a ``profiles_runtime`` state.

    ``parked`` is a catalogue flag on the profile, not a process state, so a
    stopped profile keeps it. Everything else the console counts — running,
    idle, failed — comes from the state machine.
    """
    if state == "running":
        return "running"
    if state == "error":
        return "failed"
    if state in ("starting", "stopping"):
        return state
    if str(stored or "").lower() == "parked":
        return "parked"
    return "idle"


def _decorate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the live runtime view the console expects.

    The database row wins when one exists. ``runtime.json`` is only the
    fallback for a profile the state machine has never recorded, which is
    how a handful of callers still mark a run. Reading the file first is
    how the profile list and the Resources card disagreed: the file keeps
    entries the database has already dropped.
    """
    runtime = _read_json(runtime_state_path(), {})
    if not isinstance(runtime, dict):
        runtime = {}
    try:
        rows = {r["profile_id"]: r for r in db.list_profile_runtimes()}
    except Exception as e:  # noqa: BLE001 - the JSON cache is the fallback
        log.debug("runtime table unreadable, using the JSON cache: %s", e)
        rows = {}
    for p in items:
        _normalize_fields(p)
        row = rows.get(p.get("id") or "")
        if row is not None:
            p["status"] = _status_for_state(
                str(row.get("state") or "stopped"), p.get("status")
            )
            p["pid"] = row.get("pid")
        else:
            st = runtime.get(p.get("id"), {})
            if not isinstance(st, dict):
                st = {}
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
        except Exception:  # noqa: BLE001 - the failure is recorded in the value this assigns
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
        if not isinstance(runtime, dict):
            runtime = {}
        runtime[profile_id] = {"status": status, "pid": pid}
        _write_json(runtime_state_path(), runtime)


def mirror_runtime_cache(profile_id: str) -> None:
    """Copy one profile's database runtime row into ``runtime.json``.

    Called after a state-machine write. The JSON file is a cache of that
    row, not a second place a status can be decided. No row means the cache
    entry is left as it is: a caller that only uses :func:`set_runtime` has
    not been through the state machine, and wiping its entry would hide the
    run it just recorded.
    """
    try:
        row = db.get_profile_runtime(profile_id)
    except Exception as e:  # noqa: BLE001 - the cache is not the record
        log.debug("runtime cache mirror skipped for %s: %s", profile_id, e)
        return
    if not row:
        return
    status = _status_for_state(str(row.get("state") or "stopped"), None)
    with _lock:
        runtime = _read_json(runtime_state_path(), {})
        if not isinstance(runtime, dict):
            runtime = {}
        runtime[str(profile_id)] = {"status": status, "pid": row.get("pid")}
        _write_json(runtime_state_path(), runtime)


def prune_stale_runtime() -> dict[str, int]:
    """Drop runtime state for profiles that no longer exist, and resync the rest.

    On the machine this was found, ``runtime.json`` held 151 entries, 42 of
    them marked running, and ``profiles_runtime`` held 42 rows for 5 live
    profiles. Deleting a profile removed the profile and, later, that one
    JSON key. It did not remove the database row, and nothing ever walked
    the entries already there. Metrics stopped reading the file. The file
    and the orphan rows were still what the profile list and the parking
    check could fall back to.

    Returns counts, and never raises past a logged failure of one half: a
    startup sweep that cannot rewrite a cache file must not refuse to boot,
    and a sweep that cannot see the database must not delete the cache on
    a guess.
    """
    _ensure_ready()
    removed_rows = db.prune_orphan_profile_runtimes()
    live_ids = {str(p["id"]) for p in db.list_profiles() if p.get("id")}
    rows = {r["profile_id"]: r for r in db.list_profile_runtimes()}
    dropped = 0
    rewritten = 0
    with _lock:
        runtime = _read_json(runtime_state_path(), {})
        if not isinstance(runtime, dict):
            runtime = {}
        new: dict[str, Any] = {}
        for pid, entry in runtime.items():
            if str(pid) not in live_ids:
                dropped += 1
                continue
            row = rows.get(str(pid))
            if row is None:
                new[str(pid)] = entry if isinstance(entry, dict) else {}
                continue
            projected = {
                "status": _status_for_state(str(row.get("state") or "stopped"), None),
                "pid": row.get("pid"),
            }
            if entry != projected:
                rewritten += 1
            new[str(pid)] = projected
        for pid, row in rows.items():
            if pid not in live_ids or pid in new:
                continue
            new[pid] = {
                "status": _status_for_state(str(row.get("state") or "stopped"), None),
                "pid": row.get("pid"),
            }
            rewritten += 1
        if new != runtime:
            _write_json(runtime_state_path(), new)
    return {
        "runtimeRows": len(removed_rows),
        "jsonDropped": dropped,
        "jsonRewritten": rewritten,
    }


#: Catalogue id per platform claim, for the create-time default below.
_NATIVE_UA_ID: dict[str, str] = {
    "linux": "linux_chrome",
    "windows": "win_chrome",
    "macos": "mac_chrome",
}


def _default_identity_claim(profile: dict[str, Any]) -> None:
    """Give a new profile the host's own platform, and *record* the choice.

    A profile with neither ``userAgent`` nor ``userAgentId`` used to fall
    through ``_detect_pool("")``, whose default is the Windows pool -- so the
    shipped default claimed Windows on a Linux host. Measured against a stock
    Chromium control on this machine, that claim is contradicted by fonts in
    both directions at once: every Windows-exclusive face is absent, and
    ``Liberation Mono`` is present, which no Windows machine has. See
    ``fingerprint/identity_matrix.py``.

    So a new profile defaults to the host's platform, which the matrix rates
    ``supported`` because nothing has to be emulated for it to be coherent.

    **The choice is written onto the profile, not left to a fallback.** That
    is the whole point of doing it here rather than changing
    ``_detect_pool``'s default:

    * an existing profile already in the database has no ``userAgentId``, and
      changing the derivation default would silently re-identify it -- a
      profile with live logged-in sessions would come back as a different
      machine, which is the opposite of what a profile manager is for;
    * the value is visible in the API and the console, so the operator can
      see what their profile claims and change it;
    * and ``_detect_pool``'s Windows fallback stays exactly where it is, so
      profiles created before this keep the identity they have.

    An operator who asks for a cross-OS claim still gets it. The matrix marks
    it experimental; it does not refuse it.
    """
    if (profile.get("userAgent") or "").strip():
        return
    if (profile.get("userAgentId") or "").strip():
        return
    # A device preset carries its own platform; stamping one here would fight
    # it. Every spelling, because the canonical field is `deviceId` and
    # `validation.py` already warns that this exact confusion has cost a test
    # before -- guarding only `devicePresetId` let a `deviceId: pixel-9`
    # profile be stamped linux_chrome, which overrode the Android preset's
    # User-Agent and was caught by test_integration_v16.
    if any(profile.get(key) for key in
           ("deviceId", "device_id", "devicePresetId")):
        return
    if profile.get("isMobile"):
        return
    try:
        from ..fingerprint.identity_matrix import host_platform_claim

        claim = host_platform_claim()
    except Exception as e:  # noqa: BLE001 - fall through to the old behaviour
        log.warning("could not determine the host platform claim: %s", e)
        return
    ua_id = _NATIVE_UA_ID.get(claim)
    if ua_id:
        profile["userAgentId"] = ua_id


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
    _default_identity_claim(profile)
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


def _apply_default_extensions(profile_id: str) -> None:
    """Assign any default-for-new extensions to a profile just created.

    Guarded and imported lazily: profile creation is the product's most basic
    operation and must not start failing because the extension manager is
    unavailable or its tables have not been created yet.
    """
    try:
        from ..extensions.manager import apply_defaults_to_new_profile

        apply_defaults_to_new_profile(profile_id)
    except Exception as e:  # noqa: BLE001 - never block profile creation
        log.warning("default extensions not applied to %s: %s", profile_id, e)


def create_profile(payload: dict[str, Any]) -> dict[str, Any]:
    _ensure_ready()
    profile = _new_profile(payload)
    # Capacity is enforced here, not only in the bulk endpoint: a cap that a
    # loop of single creates can walk straight past is not a cap.
    db.insert_profiles([_for_storage(profile)], capacity=max_profiles())
    _apply_default_extensions(profile["id"])
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


def purge_profile_files(profile_id: str) -> list[str]:
    """Remove a profile's Chrome directory and cookie jar.

    Returns the paths it could **not** remove, so a caller can say so. This
    used to be two swallowing handlers inside :func:`delete_profile`, one of
    them without even a log line, and the function returned ``True`` either
    way -- so a permanent delete could leave the entire Chrome profile
    directory, with every cookie and every logged-in session in it, and
    report success (F51).

    `shutil.rmtree` failing part-way is the realistic case, not the exotic
    one: a file still held open by a Chrome that was not stopped, a
    read-only mount, ENOSPC, or a permission change. It leaves *some* of the
    tree, and what is left is as likely to be `Cookies` and `Login Data` as
    anything else.
    """
    chrome_root, cookies = _profile_files(profile_id)
    left: list[str] = []
    if chrome_root.exists():
        try:
            shutil.rmtree(chrome_root)
        except OSError as e:
            log.warning("could not remove %s: %s", chrome_root, e)
        # rmtree can fail part-way, so ask the filesystem rather than
        # trusting the absence of an exception.
        if chrome_root.exists():
            left.append(str(chrome_root))
    if cookies is not None and cookies.exists():
        try:
            cookies.unlink(missing_ok=True)
        except OSError as e:
            log.warning("could not remove %s: %s", cookies, e)
        if cookies.exists():
            left.append(str(cookies))
    return left


def delete_profile(profile_id: str, *, purge_storage: bool = True) -> dict[str, Any] | None:
    """Remove a profile permanently.

    This is the irreversible one, and it is no longer what a delete from the
    console reaches: that goes to :func:`trash_profile`. Permanent deletion is
    what emptying the trash does, and what a caller asks for explicitly.

    Returns ``None`` when there was no such profile -- so ``if not
    delete_profile(...)`` still reads as "not found" -- and otherwise a
    report of what actually happened:

        {"purgeRequested": bool, "purged": bool, "leftBehind": [str, ...]}

    ``purged`` is the **outcome**, not the request. The operator deleting a
    profile permanently is usually doing it to destroy an identity, so being
    told the storage went when it did not is the one answer that cannot be
    allowed here.
    """
    _ensure_ready()
    if not db.delete_profile_row(profile_id):
        return None
    with _lock:
        runtime = _read_json(runtime_state_path(), {})
        if profile_id in runtime:
            runtime.pop(profile_id, None)
            _write_json(runtime_state_path(), runtime)
    left: list[str] = []
    if purge_storage:
        left = purge_profile_files(profile_id)
    return {
        "purgeRequested": purge_storage,
        "purged": purge_storage and not left,
        "leftBehind": left,
    }


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
    """Delete a trashed profile for good, including its files.

    Shares :func:`purge_profile_files` with :func:`delete_profile` so the two
    permanent-delete paths cannot drift: this one logged its failures and the
    other did not, which is how F51 stayed invisible on the path an operator
    is more likely to use.
    """
    _ensure_ready()
    if not db.purge_trash_row(profile_id):
        return False
    purge_profile_files(profile_id)
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
        except Exception as e:  # noqa: BLE001 - broad by design; the failure is logged, not hidden
            log.warning("clone: cookie copy failed: %s", e)  # non-fatal

    if mode == "full_session":
        try:
            src_dir = _cpd(profile_id) / "user-data"
            dst_dir = _cpd(new_id) / "user-data"
            if src_dir.exists():
                if dst_dir.exists():
                    _shutil.rmtree(dst_dir)
                _shutil.copytree(src_dir, dst_dir)
        except Exception as e:  # noqa: BLE001 - broad by design; the failure is logged, not hidden
            log.warning("clone: user-data copy failed: %s", e)  # non-fatal

    return new_profile


def duplicate_profile(profile_id: str) -> dict[str, Any] | None:
    """Backwards-compatible alias for clone_profile(mode='config_only')."""
    return clone_profile(profile_id, mode="config_only")


def _matches_status(profile: dict[str, Any], status: str) -> bool:
    """The status predicate, kept in one place.

    Status is not stored on the profile row. It is read from
    ``profiles_runtime`` (the JSON cache only when that profile has no row),
    so it cannot be a WHERE clause without a second copy of the state
    machine in SQL. It stays a Python filter, and this is the only
    definition of it.
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
    except Exception:  # noqa: BLE001 - the failure is recorded in the value this assigns
        browser_manager = None  # type: ignore

    reaped: list[dict[str, Any]] = []
    for p in load_profiles():
        if p.get("status") != "running":
            continue
        pid = p.get("pid")
        if pid_alive(pid):
            continue
        if browser_manager is not None:
            try:
                browser_manager.release_slot(p["id"])
            except Exception:  # noqa: BLE001,S110 - nothing here can be acted on; the caller's result is unchanged
                pass
        # A database row still in `running` belongs to the supervisor: it
        # restarts an unexpected exit from RUNNING, and marking the JSON
        # cache idle underneath it is the drift this function used to write
        # on every pass. With the supervisor off, nothing else will ever
        # move the row, so the reaper records the death itself.
        row = None
        try:
            row = db.get_profile_runtime(p["id"])
        except Exception as e:  # noqa: BLE001 - fall through to the JSON cache
            log.debug("reaper could not read runtime for %s: %s", p["id"], e)
        if row is not None and str(row.get("state") or "") == "running":
            try:
                from ..config import get_settings

                supervisor_on = get_settings().supervisor_enabled
            except Exception:  # noqa: BLE001 - leave the row for the supervisor
                supervisor_on = True
            if supervisor_on:
                continue
            try:
                db.set_profile_state(
                    p["id"], "error", note="reaped: process gone, supervisor disabled"
                )
                db.upsert_profile_runtime(p["id"], pid=None)
                mirror_runtime_cache(p["id"])
            except Exception as e:  # noqa: BLE001 - reported, not swallowed silently
                log.warning("reaper could not record %s as dead: %s", p["id"], e)
                continue
        else:
            set_runtime(p["id"], "idle", None)
        reaped.append({"profileId": p["id"], "oldPid": pid})
    return reaped
