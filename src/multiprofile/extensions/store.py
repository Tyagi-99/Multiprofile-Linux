"""Persistence for managed extensions and their per-profile assignments.

Two tables, two different kinds of truth, kept apart on purpose.

``extensions`` describes a **package**: files that exist in managed storage.
Its ``install_state`` is ``imported`` or ``downloaded`` and it is never
allowed to say "installed", because a file on disk is not an installation.

``extension_assignments`` describes what a **profile** actually has. Only this
table can reach ``installed_verified``, and only after the browser has been
observed running the extension and its ID recorded. The split exists because
the obvious bug in a feature like this is a UI that reports success the moment
a row is written.
"""
from __future__ import annotations

import json
from typing import Any

from ..storage.db import _connect, _lock, _utcnow

#: Package states. Describe bytes in managed storage, nothing more.
PACKAGE_IMPORTED = "imported"
PACKAGE_DOWNLOADED = "downloaded"

#: Per-profile assignment states.
STATE_ASSIGNED = "assigned"
STATE_PENDING_RESTART = "pending_restart"
STATE_INSTALLED_VERIFIED = "installed_verified"
STATE_FAILED = "failed"

ASSIGNMENT_STATES = (
    STATE_ASSIGNED, STATE_PENDING_RESTART, STATE_INSTALLED_VERIFIED, STATE_FAILED,
)

#: Human wording, so the API, the console and the CLI cannot drift apart.
STATE_LABELS = {
    STATE_ASSIGNED: "Assigned (applies on next launch)",
    STATE_PENDING_RESTART: "Pending restart",
    STATE_INSTALLED_VERIFIED: "Installed and verified",
    STATE_FAILED: "Failed",
}


def _loads(raw: Any) -> list[str]:
    try:
        got = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in got] if isinstance(got, list) else []


def _row_to_extension(row: Any) -> dict[str, Any]:
    d = dict(row)
    return {
        "id": d["ext_uuid"],
        "name": d["name"],
        "version": d["version"],
        "manifestVersion": d["manifest_version"],
        "description": d["description"],
        "source": d["source"],
        "sourceDetail": d["source_detail"],
        "permissions": _loads(d["permissions"]),
        "hostPermissions": _loads(d["host_permissions"]),
        "hasKey": bool(d["has_key"]),
        "payloadPath": d["payload_path"],
        "sizeBytes": d["size_bytes"],
        "packageState": d["install_state"],
        "extensionId": d["expected_ext_id"],
        "defaultForNewProfiles": bool(d["default_for_new"]),
        "createdAt": d["created_at"],
        "updatedAt": d["updated_at"],
    }


def _row_to_assignment(row: Any) -> dict[str, Any]:
    d = dict(row)
    return {
        "extensionId": d["ext_uuid"],
        "profileId": d["profile_id"],
        "enabled": bool(d["enabled"]),
        "state": d["state"],
        "stateLabel": STATE_LABELS.get(d["state"], d["state"]),
        "assignedAt": d["assigned_at"],
        "appliedAt": d["applied_at"],
        "verifiedAt": d["verified_at"],
        "observedExtensionId": d["observed_ext_id"],
        "observedVersion": d["observed_version"],
        "lastError": d["last_error"],
    }


# ---------------------------------------------------------------------------
# packages
# ---------------------------------------------------------------------------

def create_extension(
    ext_uuid: str,
    *,
    name: str,
    version: str,
    manifest_version: int,
    description: str = "",
    source: str,
    source_detail: str = "",
    permissions: list[str] | None = None,
    host_permissions: list[str] | None = None,
    has_key: bool = False,
    payload_path: str,
    size_bytes: int = 0,
    install_state: str = PACKAGE_IMPORTED,
) -> dict[str, Any]:
    conn = _connect()
    now = _utcnow()
    with _lock, conn:
        conn.execute(
            "INSERT INTO extensions (ext_uuid, name, version, manifest_version,"
            " description, source, source_detail, permissions, host_permissions,"
            " has_key, payload_path, size_bytes, install_state, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ext_uuid, name, version, int(manifest_version), description, source,
             source_detail, json.dumps(permissions or []),
             json.dumps(host_permissions or []), 1 if has_key else 0,
             payload_path, int(size_bytes), install_state, now, now),
        )
    got = get_extension(ext_uuid)
    assert got is not None
    return got


def get_extension(ext_uuid: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT * FROM extensions WHERE ext_uuid = ?", (ext_uuid,)
        ).fetchone()
    return _row_to_extension(row) if row else None


def list_extensions() -> list[dict[str, Any]]:
    conn = _connect()
    with _lock:
        rows = conn.execute("SELECT * FROM extensions ORDER BY name, created_at").fetchall()
    return [_row_to_extension(r) for r in rows]


def update_extension(ext_uuid: str, **fields: Any) -> dict[str, Any] | None:
    """Update whitelisted columns. Unknown keys are refused, not ignored."""
    allowed = {
        "name", "version", "manifest_version", "description", "permissions",
        "host_permissions", "has_key", "size_bytes", "install_state",
        "expected_ext_id", "default_for_new", "source", "source_detail",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"not an extension column: {sorted(unknown)}")
    if not fields:
        return get_extension(ext_uuid)
    sets = []
    values: list[Any] = []
    for key, value in fields.items():
        sets.append(f"{key} = ?")
        if key in ("permissions", "host_permissions"):
            values.append(json.dumps(value or []))
        elif key in ("has_key", "default_for_new"):
            values.append(1 if value else 0)
        else:
            values.append(value)
    sets.append("updated_at = ?")
    values.append(_utcnow())
    values.append(ext_uuid)
    conn = _connect()
    with _lock, conn:
        conn.execute(f"UPDATE extensions SET {', '.join(sets)} WHERE ext_uuid = ?", values)
    return get_extension(ext_uuid)


def delete_extension(ext_uuid: str) -> int:
    """Remove the package row and every assignment of it."""
    conn = _connect()
    with _lock, conn:
        conn.execute("DELETE FROM extension_assignments WHERE ext_uuid = ?", (ext_uuid,))
        conn.execute("DELETE FROM extension_versions WHERE ext_uuid = ?", (ext_uuid,))
        cur = conn.execute("DELETE FROM extensions WHERE ext_uuid = ?", (ext_uuid,))
    return cur.rowcount


def defaults_for_new_profiles() -> list[dict[str, Any]]:
    conn = _connect()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM extensions WHERE default_for_new = 1 ORDER BY name"
        ).fetchall()
    return [_row_to_extension(r) for r in rows]


# ---------------------------------------------------------------------------
# retained versions
# ---------------------------------------------------------------------------

def record_version(
    ext_uuid: str, *, version: str, payload_path: str,
    manifest_version: int = 3, size_bytes: int = 0,
) -> None:
    conn = _connect()
    with _lock, conn:
        conn.execute(
            "INSERT OR REPLACE INTO extension_versions"
            " (ext_uuid, version, payload_path, manifest_version, size_bytes, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (ext_uuid, version, payload_path, int(manifest_version),
             int(size_bytes), _utcnow()),
        )


def list_versions(ext_uuid: str) -> list[dict[str, Any]]:
    """Newest first. The row at index 0 is what a rollback would restore."""
    conn = _connect()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM extension_versions WHERE ext_uuid = ?"
            " ORDER BY created_at DESC", (ext_uuid,)
        ).fetchall()
    return [
        {"version": r["version"], "payloadPath": r["payload_path"],
         "manifestVersion": r["manifest_version"], "sizeBytes": r["size_bytes"],
         "createdAt": r["created_at"]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# assignments
# ---------------------------------------------------------------------------

def upsert_assignment(
    ext_uuid: str, profile_id: str, *,
    state: str = STATE_ASSIGNED,
    enabled: bool = True,
    last_error: str | None = None,
) -> dict[str, Any]:
    """Assign, idempotently.

    Re-assigning an extension a profile already has must not duplicate it,
    must not reset a verified install back to "assigned", and must not lose a
    recorded error by overwriting it with a default. So an existing row keeps
    its verified state unless the caller is explicitly moving it.
    """
    conn = _connect()
    now = _utcnow()
    with _lock, conn:
        row = conn.execute(
            "SELECT * FROM extension_assignments WHERE ext_uuid = ? AND profile_id = ?",
            (ext_uuid, profile_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO extension_assignments"
                " (ext_uuid, profile_id, enabled, state, assigned_at, last_error)"
                " VALUES (?,?,?,?,?,?)",
                (ext_uuid, profile_id, 1 if enabled else 0, state, now, last_error),
            )
        else:
            keep_verified = (
                row["state"] == STATE_INSTALLED_VERIFIED and state == STATE_ASSIGNED
            )
            conn.execute(
                "UPDATE extension_assignments SET enabled = ?, state = ?,"
                " last_error = ? WHERE ext_uuid = ? AND profile_id = ?",
                (1 if enabled else 0,
                 row["state"] if keep_verified else state,
                 last_error if last_error is not None else row["last_error"],
                 ext_uuid, profile_id),
            )
    got = get_assignment(ext_uuid, profile_id)
    assert got is not None
    return got


def get_assignment(ext_uuid: str, profile_id: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT * FROM extension_assignments WHERE ext_uuid = ? AND profile_id = ?",
            (ext_uuid, profile_id),
        ).fetchone()
    return _row_to_assignment(row) if row else None


def list_assignments(
    *, ext_uuid: str | None = None, profile_id: str | None = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM extension_assignments WHERE 1=1"
    args: list[Any] = []
    if ext_uuid is not None:
        sql += " AND ext_uuid = ?"
        args.append(ext_uuid)
    if profile_id is not None:
        sql += " AND profile_id = ?"
        args.append(profile_id)
    if enabled_only:
        sql += " AND enabled = 1"
    sql += " ORDER BY ext_uuid, profile_id"
    conn = _connect()
    with _lock:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_assignment(r) for r in rows]


def set_assignment_state(
    ext_uuid: str, profile_id: str, state: str, *,
    last_error: str | None = None,
    observed_ext_id: str | None = None,
    observed_version: str | None = None,
    applied: bool = False,
) -> dict[str, Any] | None:
    if state not in ASSIGNMENT_STATES:
        raise ValueError(f"not an assignment state: {state!r}")
    now = _utcnow()
    conn = _connect()
    with _lock, conn:
        sets = ["state = ?", "last_error = ?"]
        args: list[Any] = [state, last_error]
        if applied:
            sets.append("applied_at = ?")
            args.append(now)
        if state == STATE_INSTALLED_VERIFIED:
            sets.append("verified_at = ?")
            args.append(now)
        if observed_ext_id is not None:
            sets.append("observed_ext_id = ?")
            args.append(observed_ext_id)
        if observed_version is not None:
            sets.append("observed_version = ?")
            args.append(observed_version)
        args.extend([ext_uuid, profile_id])
        conn.execute(
            f"UPDATE extension_assignments SET {', '.join(sets)}"
            f" WHERE ext_uuid = ? AND profile_id = ?", args)
    return get_assignment(ext_uuid, profile_id)


def set_enabled(ext_uuid: str, profile_id: str, enabled: bool) -> dict[str, Any] | None:
    conn = _connect()
    with _lock, conn:
        conn.execute(
            "UPDATE extension_assignments SET enabled = ?"
            " WHERE ext_uuid = ? AND profile_id = ?",
            (1 if enabled else 0, ext_uuid, profile_id),
        )
    return get_assignment(ext_uuid, profile_id)


def remove_assignment(ext_uuid: str, profile_id: str) -> bool:
    """Unassign from ONE profile. Must not touch any other profile's row."""
    conn = _connect()
    with _lock, conn:
        cur = conn.execute(
            "DELETE FROM extension_assignments WHERE ext_uuid = ? AND profile_id = ?",
            (ext_uuid, profile_id),
        )
    return cur.rowcount > 0


def assignment_counts() -> dict[str, int]:
    """How many profiles each extension is assigned to."""
    conn = _connect()
    with _lock:
        rows = conn.execute(
            "SELECT ext_uuid, COUNT(*) AS n FROM extension_assignments GROUP BY ext_uuid"
        ).fetchall()
    return {r["ext_uuid"]: int(r["n"]) for r in rows}


def reference_count(payload_path: str) -> int:
    """How many extensions point at this payload path.

    Guards the delete path: shared payload files must not be removed while
    another extension row still references them.
    """
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM extensions WHERE payload_path = ?", (payload_path,)
        ).fetchone()
    return int(row["n"]) if row else 0
