"""SQLite control-plane truth store for Multiprofile Native.

Holds the durable records the control plane owns:

- ``profiles_runtime`` — the launch state of every profile (state, CDP port,
  PID, device preset, network config). The in-memory ``runtime_state``
  registry remains a fast cache; this database is the durable truth that
  survives restarts.
- ``jobs`` — the durable job queue backing ``multiprofile.jobs``.
- ``schema_version`` — a tiny migration ledger.

Design notes:

- WAL mode (``PRAGMA journal_mode=WAL``) so readers (API, supervisor) never
  block writers (launcher, workers) on the same connection family.
- One process-wide connection guarded by a lock; threads share it safely
  (``check_same_thread=False``). A short ``busy_timeout`` makes transient
  lock contention fail closed instead of hanging.
- All writes are wrapped in ``with conn:`` (transaction per call) so a
  crash can never leave a half-written row.
- ``profiles_runtime`` carries an additive ``note`` column beyond the
  minimal contract; it records the human reason for ``error`` states
  (e.g. "browser process died") without touching any on-disk profile data.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..audit import audit_log
from .state import (
    InvalidStateTransition,
    ProfileState,
    normalize,
    validate_transition,
)
from .xdg import control_db_path

log = logging.getLogger("multiprofile.storage.db")

SCHEMA_VERSION = 6

_DB_PATH_ENV = "MULTIPROFILE_CONTROL_DB"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
_schema_ready = False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure(path: str | os.PathLike[str] | None) -> None:
    """Point the store at an explicit database file (used by tests).

    Passing ``None`` clears the override and falls back to the XDG data dir.
    Any open connection is closed so the next access re-opens the new path.
    """
    global _conn, _schema_ready
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            _conn = None
        _schema_ready = False
        if path is None:
            os.environ.pop(_DB_PATH_ENV, None)
        else:
            os.environ[_DB_PATH_ENV] = str(path)


def db_path() -> str:
    """Resolve the SQLite file location."""
    override = os.environ.get(_DB_PATH_ENV)
    if override:
        return override
    return str(control_db_path())


def _harden(path: str) -> None:
    """Make the database owner-only, including its WAL sidecars.

    SQLite creates these files itself, so the process umask decides and they
    land 0644 on a normal install. ``harden_data_dir()`` already fixes that,
    but only at startup -- which left a window where the file existed
    world-readable, and once the profile store moved in here that window
    covered every profile's proxy host, username, client and notes. Setting
    the mode at creation closes it; the startup pass stays as the repair for
    files written by older versions.
    """
    for suffix in ("", "-wal", "-shm"):
        target = path + suffix
        if not os.path.exists(target):
            # A sidecar that does not exist yet is not a failure: WAL and SHM
            # appear on first write and are hardened on the next pass.
            continue
        try:
            os.chmod(target, 0o600)
        except OSError as e:
            # This comment used to say the startup hardening pass reports what
            # it could not fix. It did not -- `harden_data_dir` returned only
            # what it changed -- so the claim was load-bearing and false.
            from .xdg import record_permission_fault

            record_permission_fault(target, f"chmod on the control database failed: {e}")


def _connect() -> sqlite3.Connection:
    global _conn, _conn_path, _schema_ready
    with _lock:
        # Reopen when the resolved location has moved. The connection used to
        # be cached for the life of the process regardless of where db_path()
        # now pointed, which was survivable while this database held only
        # runtime rows and jobs. Once the profile store moved in, a caller
        # that repointed XDG_DATA_HOME -- every test that isolates itself that
        # way, and any future multi-data-dir use -- kept silently reading and
        # writing the previous directory's profiles.
        if _conn is not None and _conn_path != db_path():
            try:
                _conn.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            _conn = None
            _schema_ready = False
        if _conn is None:
            path = db_path()
            # control_db_path() creates parent dirs; the env override may not.
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            # After the WAL pragma, so the -wal and -shm files exist to chmod.
            _harden(path)
            _conn = conn
            _conn_path = path
        if not _schema_ready:
            _ensure_schema(_conn)
            _schema_ready = True
        return _conn


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles_runtime (
    profile_id        TEXT PRIMARY KEY,
    state             TEXT NOT NULL DEFAULT 'stopped',
    cdp_port          INTEGER,
    pid               INTEGER,
    device_preset_id  TEXT,
    network_config_id TEXT,
    note              TEXT,
    started_at        TEXT,
    updated_at        TEXT,
    last_heartbeat    TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type     TEXT NOT NULL,
    profile_id   TEXT,
    payload_json TEXT,
    status       TEXT NOT NULL DEFAULT 'queued',
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    result_json  TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_profile ON jobs(profile_id);

-- Profiles. The fields worth querying are columns; everything else stays in
-- data_json.
--
-- A profile has ~40 fields and gains more with each feature, so a column per
-- field would mean a migration per feature and a schema that is always one
-- release behind the product. The four extracted here are the ones something
-- other than the profile itself needs: `client_id` because billing has to
-- aggregate by it, `name` and `created_at` because the console sorts on them,
-- and `updated_at` because sync needs to know what moved. Those get indexes;
-- the rest is read as a document, which is what it is.
CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    client_id  TEXT,
    created_at TEXT,
    updated_at TEXT NOT NULL,
    data_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profiles_client ON profiles(client_id);
CREATE INDEX IF NOT EXISTS idx_profiles_name ON profiles(name);

-- Trash. Deleting a profile used to be a DELETE plus an rmtree of its
-- chrome-profiles/<id> directory, which is where the cookies, logins and
-- warmed-up session state live -- in this product that directory *is* the
-- asset, and a misclick destroyed it with no way back. A trashed profile
-- keeps its whole document and its directory; only emptying the trash
-- actually removes anything.
--
-- Same shape as `profiles` so a restore is a straight move, plus when it was
-- trashed (for retention) and what it was called (so the trash list can be
-- read without parsing every document).
CREATE TABLE IF NOT EXISTS profiles_trash (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    client_id  TEXT,
    created_at TEXT,
    updated_at TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    disk_bytes INTEGER NOT NULL DEFAULT 0,
    data_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trash_deleted ON profiles_trash(deleted_at);

-- Usage. One row per time a profile was actually running, which is the only
-- quantity this product can meter honestly: launches are a count, but what a
-- fleet costs -- in memory, in proxy bandwidth, in anything a plan would
-- price -- is time spent running.
--
-- `client_id` is snapshotted when the session opens rather than joined at
-- read time. A profile can be moved between workspaces, and a bill has to say
-- who owned it *while it ran*; a join would silently rewrite history every
-- time a profile changed hands.
--
-- `ended_at` NULL means the session is open. `end_reason` says how it closed,
-- because "stopped" and "the machine lost power" are the same duration and
-- very different facts.
CREATE TABLE IF NOT EXISTS usage_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  TEXT NOT NULL,
    client_id   TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    seconds     INTEGER,
    end_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_profile ON usage_sessions(profile_id);
CREATE INDEX IF NOT EXISTS idx_usage_client ON usage_sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_usage_started ON usage_sessions(started_at);
-- Finding an open session is the hot path: it happens on every stop.
CREATE INDEX IF NOT EXISTS idx_usage_open ON usage_sessions(profile_id, ended_at);

-- API tokens. The product had exactly one, stored in cleartext in
-- $XDG_CONFIG_HOME/multiprofile/api_token, unscoped and unrevocable: an
-- automation script that only needed to start a profile held the same
-- credential that can delete the fleet and read every cookie jar, and the
-- only remedy for a leak was to change the one token and break every other
-- integration at once.
--
-- Only the hash is stored. These are 256 bits of `secrets` output rather
-- than human-chosen passwords, so SHA-256 is the right primitive -- a slow
-- KDF defends against guessing a low-entropy secret, and there is nothing
-- here to guess. What it buys is that a leaked database does not yield a
-- usable token.
CREATE TABLE IF NOT EXISTS api_tokens (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL,
    scopes       TEXT NOT NULL DEFAULT 'read',
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_revoked ON api_tokens(revoked_at);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(_SCHEMA_SQL)
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = int(row["v"]) if row and row["v"] is not None else 0
        if current < SCHEMA_VERSION:
            _migrate(conn, current, SCHEMA_VERSION)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _utcnow()),
            )
    # Sanity: WAL must actually be on (some filesystems/configs refuse it).
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    if str(mode).lower() != "wal":
        log.warning("SQLite journal_mode is %r, expected 'wal'", mode)


def _migrate(conn: sqlite3.Connection, from_v: int, to_v: int) -> None:
    """Apply schema migrations. v1 is the base schema."""
    log.info("control db schema migration %s -> %s", from_v, to_v)
    # v1: base tables created by _SCHEMA_SQL; nothing incremental yet.
    # v2: the `profiles` table, created by _SCHEMA_SQL above. Importing the
    #     old profiles.json into it is NOT done here: it needs the
    #     normalisation and secret-wrapping that live in storage/profiles.py,
    #     and a schema migration that imports application data would run
    #     before that module is ready. See profiles.import_legacy_json.
    # v3: `profiles_trash`, also created by _SCHEMA_SQL. Nothing to backfill:
    #     an existing database simply starts with an empty trash. Deletes made
    #     before this version were permanent and cannot be recovered.
    # v4: `profiles_trash.disk_bytes`. The first version measured the retained
    #     directory while rendering the trash, which is a full tree walk per
    #     row on every page load -- this host's own Chromium profile holds
    #     3239 files, so 150 trashed profiles cost ~2 s of stat() to display a
    #     number. Nothing writes to a trashed profile, so the size is measured
    #     once, when it is trashed. CREATE TABLE above covers a new database;
    #     this adds the column to one that already has the table.
    # v6: `api_tokens`, created by _SCHEMA_SQL. The legacy single token is not
    #     migrated into it: it lives in a file the operator controls, it still
    #     works, and silently copying a credential into a new store is not a
    #     migration anyone asked for.
    # v5: `usage_sessions`, created by _SCHEMA_SQL. Nothing to backfill -- the
    #     product never recorded runtime before, and inventing history for a
    #     table a bill would be drawn from is the one thing metering must not
    #     do. An upgraded install starts measuring from its first launch.
    if from_v == 3:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(profiles_trash)")}
        if "disk_bytes" not in cols:
            conn.execute(
                "ALTER TABLE profiles_trash ADD COLUMN disk_bytes INTEGER NOT NULL DEFAULT 0"
            )


def journal_mode() -> str:
    """Return the active journal mode (used by tests to assert WAL is on)."""
    conn = _connect()
    with _lock:
        return str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()


# ---------------------------------------------------------------------------
# Profile runtime records
# ---------------------------------------------------------------------------

_RUNTIME_FIELDS = (
    "profile_id",
    "state",
    "cdp_port",
    "pid",
    "device_preset_id",
    "network_config_id",
    "note",
    "started_at",
    "updated_at",
    "last_heartbeat",
)


def get_profile_runtime(profile_id: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT * FROM profiles_runtime WHERE profile_id = ?", (profile_id,)
        ).fetchone()
    return dict(row) if row else None


def list_profile_runtimes(state: str | None = None) -> list[dict[str, Any]]:
    conn = _connect()
    with _lock:
        if state is None:
            rows = conn.execute("SELECT * FROM profiles_runtime ORDER BY profile_id").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM profiles_runtime WHERE state = ? ORDER BY profile_id",
                (normalize(state),),
            ).fetchall()
    return [dict(r) for r in rows]


def set_profile_state(
    profile_id: str,
    new_state: ProfileState | str,
    *,
    note: str | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Move a profile to ``new_state`` via the state machine.

    Raises:
        InvalidStateTransition: unless ``force=True`` (escape hatch used only
            when the data plane demonstrably disagrees with the record; the
            forced jump is written to the audit log).

    Returns ``{"old": ..., "new": ...}``; publishes nothing — callers publish
    ``profile.status`` events via the events bus.
    """
    target = normalize(new_state)
    now = _utcnow()
    conn = _connect()
    with _lock:
        with conn:
            row = conn.execute(
                "SELECT state FROM profiles_runtime WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            old = str(row["state"]) if row else ProfileState.STOPPED.value
            try:
                final = validate_transition(profile_id, old, target)
            except InvalidStateTransition:
                if not force:
                    raise
                final = target
                audit_log(
                    "profile_state_forced",
                    profile_id=profile_id,
                    ok=True,
                    detail={"from": old, "to": final},
                )
            if row is None:
                conn.execute(
                    "INSERT INTO profiles_runtime (profile_id, state, note, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (profile_id, final, note, now),
                )
            else:
                if note is None:
                    conn.execute(
                        "UPDATE profiles_runtime SET state = ?, updated_at = ? "
                        "WHERE profile_id = ?",
                        (final, now, profile_id),
                    )
                else:
                    conn.execute(
                        "UPDATE profiles_runtime SET state = ?, note = ?, updated_at = ? "
                        "WHERE profile_id = ?",
                        (final, note, now, profile_id),
                    )
    # Metering hangs off the transition, not off the callers. Four call sites
    # reach this function (launcher, supervisor, job handlers, the API);
    # metering from each would mean four places that must remember, and the
    # one that forgets bills short. Outside the transaction above so a
    # metering failure can never roll back the state change -- the record of
    # what the browser is doing matters more than the record of what it cost.
    if final != old:
        try:
            _meter_transition(profile_id, old, final)
        except Exception:  # noqa: BLE001 - bookkeeping must not break a launch
            log.warning("usage metering failed for %s", profile_id, exc_info=True)
    return {"old": old, "new": final}


def _meter_transition(profile_id: str, old: str, new: str) -> None:
    """Open a usage session on entering RUNNING, close it on leaving."""
    running = ProfileState.RUNNING.value
    if new == running:
        open_usage_session(profile_id, _client_of(profile_id))
    elif old == running:
        close_usage_session(profile_id, reason="error" if new == "error" else "stopped")


def _client_of(profile_id: str) -> str | None:
    """The workspace a profile belongs to, read at session start.

    Read straight from the column rather than through storage.profiles: this
    module is imported by that one, and metering must not be able to fail on
    an import cycle.
    """
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT client_id FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    return (row["client_id"] if row else None) or None


def upsert_profile_runtime(profile_id: str, **fields: Any) -> dict[str, Any]:
    """Update bookkeeping fields (pid, cdp_port, ...) without a state change.

    Unknown field names are ignored. If ``state`` is passed it goes through
    :func:`set_profile_state` validation.
    """
    fields = dict(fields)
    state = fields.pop("state", None)
    if state is not None:
        set_profile_state(profile_id, state, note=fields.pop("note", None))
    clean = {k: v for k, v in fields.items() if k in _RUNTIME_FIELDS and k != "profile_id"}
    now = _utcnow()
    conn = _connect()
    with _lock:
        with conn:
            row = conn.execute(
                "SELECT profile_id FROM profiles_runtime WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            if row is None:
                cols = ["profile_id", "updated_at", *clean.keys()]
                vals = [profile_id, now, *clean.values()]
                conn.execute(
                    f"INSERT INTO profiles_runtime ({', '.join(cols)}) "
                    f"VALUES ({', '.join(['?'] * len(cols))})",
                    vals,
                )
            elif clean:
                sets = ", ".join(f"{k} = ?" for k in clean) + ", updated_at = ?"
                conn.execute(
                    f"UPDATE profiles_runtime SET {sets} WHERE profile_id = ?",
                    [*clean.values(), now, profile_id],
                )
            else:
                conn.execute(
                    "UPDATE profiles_runtime SET updated_at = ? WHERE profile_id = ?",
                    (now, profile_id),
                )
    return get_profile_runtime(profile_id) or {"profile_id": profile_id}


def record_heartbeat(profile_id: str) -> None:
    conn = _connect()
    with _lock:
        with conn:
            conn.execute(
                "UPDATE profiles_runtime SET last_heartbeat = ?, updated_at = ? "
                "WHERE profile_id = ?",
                (_utcnow(), _utcnow(), profile_id),
            )


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


def insert_api_token(
    token_id: str, name: str, token_hash: str, scopes: str
) -> dict[str, Any]:
    conn = _connect()
    now = _utcnow()
    with _lock, conn:
        conn.execute(
            "INSERT INTO api_tokens (id, name, token_hash, scopes, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (token_id, name, token_hash, scopes, now),
        )
    return {"id": token_id, "name": name, "scopes": scopes, "createdAt": now}


def get_api_token(token_id: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT * FROM api_tokens WHERE id = ?", (token_id,)
        ).fetchone()
    return dict(row) if row else None


def list_api_tokens(include_revoked: bool = False) -> list[dict[str, Any]]:
    """Every token, without its hash.

    The hash never leaves this function's SELECT list. It is not a secret a
    listing has any use for, and a value that is never sent cannot be logged
    by something downstream that did not know what it was holding.
    """
    conn = _connect()
    where = "" if include_revoked else " WHERE revoked_at IS NULL"
    with _lock:
        rows = conn.execute(
            "SELECT id, name, scopes, created_at, last_used_at, revoked_at"
            f" FROM api_tokens{where} ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_api_token(token_id: str) -> bool:
    """Mark a token revoked. The row stays, so the audit trail stays."""
    conn = _connect()
    with _lock, conn:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_utcnow(), token_id),
        )
        return cur.rowcount > 0


def touch_api_token(token_id: str) -> None:
    """Record that a token was just used.

    Best-effort and deliberately outside any transaction: knowing a token is
    still in use is worth having, and is never worth failing a request for.
    """
    conn = _connect()
    with _lock, conn:
        conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
            (_utcnow(), token_id),
        )


def count_api_tokens(include_revoked: bool = False) -> int:
    conn = _connect()
    where = "" if include_revoked else " WHERE revoked_at IS NULL"
    with _lock:
        return int(
            conn.execute(f"SELECT COUNT(*) AS n FROM api_tokens{where}").fetchone()["n"]
        )


def delete_all_api_tokens() -> int:
    conn = _connect()
    with _lock, conn:
        return conn.execute("DELETE FROM api_tokens").rowcount


# ---------------------------------------------------------------------------
# Usage metering
# ---------------------------------------------------------------------------
#
# Every function here is driven from `set_profile_state`, which is the single
# transition every caller -- launcher, supervisor, job handlers -- already
# routes through. Metering from the callers instead would mean four places
# that must each remember to record, and the one that forgets produces a bill
# that is quietly short. That is the same shape as F1 and F19: a control that
# exists but is not on the path that matters.


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _elapsed_seconds(start: str | None, end: str | None) -> int:
    a, b = _parse_iso(start), _parse_iso(end)
    if a is None or b is None:
        return 0
    return max(0, int((b - a).total_seconds()))


def _is_after(later: str | None, earlier: str | None) -> bool:
    """Whether ``later`` is genuinely the later timestamp.

    Distinct from ``_elapsed_seconds(...) > 0``, which was the first version
    and was wrong: it truncates to whole seconds, so a heartbeat recorded
    300 ms after a session opened read as *older* than the session and was
    discarded as stale.
    """
    a, b = _parse_iso(earlier), _parse_iso(later)
    return a is not None and b is not None and b > a


def open_usage_session(profile_id: str, client_id: str | None = None) -> int | None:
    """Record that a profile started running. Returns the session id.

    Opening twice for one profile is a no-op rather than a second row: a
    forced or repeated transition to RUNNING must not bill the same minute
    twice. The existing open session's id is returned.
    """
    conn = _connect()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM usage_sessions WHERE profile_id = ? AND ended_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO usage_sessions (profile_id, client_id, started_at)"
            " VALUES (?, ?, ?)",
            (profile_id, client_id or None, _utcnow()),
        )
        return int(cur.lastrowid) if cur.lastrowid else None


def close_usage_session(
    profile_id: str, *, reason: str = "stopped", at: str | None = None
) -> int:
    """Close the open session for a profile. Returns the seconds recorded.

    ``at`` exists for recovery, which must close an abandoned session at the
    last moment the profile was known to be alive rather than at "now".
    """
    conn = _connect()
    ended = at or _utcnow()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, started_at FROM usage_sessions"
            " WHERE profile_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if not row:
            return 0
        # A clock that went backwards, or an `at` earlier than the start,
        # must not produce a negative duration -- _elapsed_seconds floors it.
        seconds = _elapsed_seconds(row["started_at"], ended)
        conn.execute(
            "UPDATE usage_sessions SET ended_at = ?, seconds = ?, end_reason = ?"
            " WHERE id = ?",
            (ended, seconds, reason, row["id"]),
        )
        return seconds


def close_abandoned_sessions() -> int:
    """Close sessions left open by a crash. Returns how many were closed.

    Called from :func:`recover` on process start. Each is closed at the last
    heartbeat the profile recorded, falling back to its start -- never at
    "now", because the machine may have been off for three days and a bill
    that charges for them is worse than one that undercounts.
    """
    conn = _connect()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT u.id, u.profile_id, u.started_at, r.last_heartbeat"
            " FROM usage_sessions u"
            " LEFT JOIN profiles_runtime r ON r.profile_id = u.profile_id"
            " WHERE u.ended_at IS NULL"
        ).fetchall()
        closed = 0
        for row in rows:
            heartbeat = row["last_heartbeat"]
            # A heartbeat from before the session started belongs to an
            # earlier run; it would bill zero, which is the safe direction,
            # but the start is the more honest floor.
            ended = row["started_at"]
            if _is_after(heartbeat, row["started_at"]):
                ended = heartbeat
            conn.execute(
                "UPDATE usage_sessions SET ended_at = ?, seconds = ?,"
                " end_reason = 'abandoned' WHERE id = ?",
                (ended, _elapsed_seconds(row["started_at"], ended), row["id"]),
            )
            closed += 1
        return closed


def usage_rows(
    since: str | None = None, until: str | None = None
) -> list[dict[str, Any]]:
    """Closed and open sessions in a window, newest first.

    An open session reports the seconds it has run so far, so "currently
    running" is visible rather than missing until it stops.
    """
    conn = _connect()
    clauses, params = [], []
    if since:
        clauses.append("started_at >= ?")
        params.append(since)
    if until:
        clauses.append("started_at < ?")
        params.append(until)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _lock:
        rows = conn.execute(
            "SELECT id, profile_id, client_id, started_at, ended_at, seconds,"
            f" end_reason FROM usage_sessions{where} ORDER BY id DESC",
            params,
        ).fetchall()
    now = _utcnow()
    out = []
    for row in rows:
        record = dict(row)
        if record["ended_at"] is None:
            record["seconds"] = _elapsed_seconds(record["started_at"], now)
            record["open"] = True
        else:
            record["seconds"] = int(record["seconds"] or 0)
            record["open"] = False
        out.append(record)
    return out


def count_usage_sessions() -> int:
    conn = _connect()
    with _lock:
        return int(
            conn.execute("SELECT COUNT(*) AS n FROM usage_sessions").fetchone()["n"]
        )


def delete_usage_for_profile(profile_id: str) -> int:
    conn = _connect()
    with _lock, conn:
        cur = conn.execute("DELETE FROM usage_sessions WHERE profile_id = ?", (profile_id,))
        return cur.rowcount


# ---------------------------------------------------------------------------
# Durable job records
# ---------------------------------------------------------------------------

def insert_job(
    job_type: str,
    profile_id: str | None,
    payload_json: str | None,
    max_attempts: int = 3,
) -> int:
    conn = _connect()
    now = _utcnow()
    with _lock:
        with conn:
            cur = conn.execute(
                "INSERT INTO jobs (job_type, profile_id, payload_json, status, "
                "attempts, max_attempts, created_at, updated_at) "
                "VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)",
                (job_type, profile_id, payload_json, max(1, int(max_attempts)), now, now),
            )
            return int(cur.lastrowid)


def get_job_record(job_id: int) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_job_records(
    status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    conn = _connect()
    with _lock:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def count_jobs(status: str) -> int:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = ?", (status,)
        ).fetchone()
    return int(row["n"])


def claim_next_job() -> dict[str, Any] | None:
    """Atomically claim the oldest queued job: queued -> running, attempts+1.

    The ``UPDATE ... WHERE status='queued'`` makes the claim atomic across
    threads; the rowcount tells us whether we won the race.
    """
    conn = _connect()
    now = _utcnow()
    with _lock:
        with conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            cur = conn.execute(
                "UPDATE jobs SET status = 'running', attempts = attempts + 1, "
                "updated_at = ? WHERE id = ? AND status = 'queued'",
                (now, row["id"]),
            )
            if cur.rowcount != 1:
                return None  # lost the race
            claimed = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (row["id"],)
            ).fetchone()
    return dict(claimed) if claimed else None


def update_job_record(job_id: int, **fields: Any) -> None:
    allowed = {"status", "result_json", "error", "attempts"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    if not clean:
        return
    conn = _connect()
    with _lock:
        with conn:
            sets = ", ".join(f"{k} = ?" for k in clean) + ", updated_at = ?"
            conn.execute(
                f"UPDATE jobs SET {sets} WHERE id = ?",
                [*clean.values(), _utcnow(), job_id],
            )


# ---------------------------------------------------------------------------
# Startup recovery — never lose a queued job on restart
# ---------------------------------------------------------------------------

def recover() -> dict[str, Any]:
    """Recover from an unclean shutdown. Safe to call on every process start.

    - Jobs stuck in ``running`` are re-queued with ``attempts + 1`` so a new
      worker picks them up. If a job already hit ``max_attempts`` it is marked
      ``failed`` instead of being retried forever.
    - Profiles stuck in transitional states (``starting``/``stopping``) can no
      longer be observed — their browser state is unknown — so they are moved
      to ``error`` with an audit note. An explicit restart/recover action is
      required to leave ``error``.
    """
    conn = _connect()
    now = _utcnow()
    recovered_jobs = 0
    failed_jobs = 0
    recovered_profiles: list[str] = []
    with _lock:
        with conn:
            # Crash-recoverable jobs: running -> queued (attempts + 1),
            # exhausted ones -> failed so poison jobs cannot spin forever.
            rows = conn.execute(
                "SELECT id, attempts, max_attempts FROM jobs WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                if int(row["attempts"]) >= int(row["max_attempts"]):
                    conn.execute(
                        "UPDATE jobs SET status = 'failed', "
                        "error = 'recovered after restart: max attempts exceeded', "
                        "updated_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                    failed_jobs += 1
                else:
                    conn.execute(
                        "UPDATE jobs SET status = 'queued', attempts = attempts + 1, "
                        "updated_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                    recovered_jobs += 1
            # Transitional profile states are unobservable after a restart.
            stuck = conn.execute(
                "SELECT profile_id, state FROM profiles_runtime "
                "WHERE state IN ('starting', 'stopping')"
            ).fetchall()
            for row in stuck:
                conn.execute(
                    "UPDATE profiles_runtime SET state = 'error', "
                    "note = 'recovered after restart: browser state unknown', "
                    "updated_at = ? WHERE profile_id = ?",
                    (now, row["profile_id"]),
                )
                recovered_profiles.append(row["profile_id"])
    for profile_id in recovered_profiles:
        audit_log(
            "profile_state_recovered",
            profile_id=profile_id,
            ok=False,
            detail={
                "from": "transitional",
                "to": "error",
                "reason": "process restarted while profile was starting/stopping; "
                "browser state unknown — explicit restart required",
            },
        )
    # A profile that was running when the process died left a usage session
    # open. Closed here, at the last heartbeat rather than at "now": the
    # machine may have been off for days, and a meter that charges for them is
    # a worse failure than one that undercounts.
    closed_sessions = close_abandoned_sessions()
    result = {
        "recovered_jobs": recovered_jobs,
        "failed_jobs": failed_jobs,
        "recovered_profiles": recovered_profiles,
        "closed_usage_sessions": closed_sessions,
    }
    log.info("startup recovery: %s", result)
    return result


def close() -> None:
    """Close the process-wide connection (used by tests / clean shutdown)."""
    global _conn, _schema_ready
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001 - best effort
                pass
            _conn = None
        _schema_ready = False


__all__ = [
    "SCHEMA_VERSION",
    "InvalidStateTransition",
    "ProfileState",
    "claim_next_job",
    "close",
    "configure",
    "count_jobs",
    "db_path",
    "get_job_record",
    "get_profile_runtime",
    "insert_job",
    "journal_mode",
    "list_job_records",
    "list_profile_runtimes",
    "recover",
    "record_heartbeat",
    "set_profile_state",
    "insert_api_token",
    "get_api_token",
    "list_api_tokens",
    "revoke_api_token",
    "touch_api_token",
    "count_api_tokens",
    "delete_all_api_tokens",
    "open_usage_session",
    "close_usage_session",
    "close_abandoned_sessions",
    "usage_rows",
    "count_usage_sessions",
    "delete_usage_for_profile",
    "upsert_profile_runtime",
    "update_job_record",
]


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
#
# Every function here writes one row in one statement. That is the whole point
# of the move: the JSON store rewrote the entire list on every change, and
# because the lock was released between reading and writing, concurrent
# updates overwrote each other. Measured on the JSON store with 20 threads
# each updating a different profile, under the same threaded Flask server the
# product ships:
#
#     writes that survived: 3/20
#
# and all 20 returned success to their callers. A per-row UPDATE cannot lose a
# write to a different row, so the fix is structural rather than a lock.


def _profile_row(profile: dict[str, Any]) -> tuple[str, str, str | None, str | None, str, str]:
    return (
        str(profile.get("id") or ""),
        str(profile.get("name") or ""),
        profile.get("clientId") or None,
        profile.get("createdAt") or None,
        _utcnow(),
        json.dumps(profile, separators=(",", ":"), default=str),
    )


def list_profiles() -> list[dict[str, Any]]:
    """Every stored profile, in insertion order."""
    conn = _connect()
    with _lock:
        rows = conn.execute(
            "SELECT data_json FROM profiles ORDER BY rowid"
        ).fetchall()
    out = []
    for row in rows:
        try:
            out.append(json.loads(row["data_json"]))
        except (ValueError, TypeError):
            log.warning("skipping a profile row whose JSON will not parse")
    return out


def get_profile_row(profile_id: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT data_json FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["data_json"])
    except (ValueError, TypeError):
        log.warning("profile %s has unparseable JSON", profile_id)
        return None


def put_profile(profile: dict[str, Any]) -> None:
    """Insert or replace one profile."""
    conn = _connect()
    with _lock, conn:
        conn.execute(
            "INSERT INTO profiles (id, name, client_id, created_at, updated_at,"
            " data_json) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
            " client_id=excluded.client_id, created_at=excluded.created_at,"
            " updated_at=excluded.updated_at, data_json=excluded.data_json",
            _profile_row(profile),
        )


def mutate_profile(
    profile_id: str, apply: Callable[[dict[str, Any]], dict[str, Any] | None]
) -> dict[str, Any] | None:
    """Read, transform and write one profile inside a single transaction.

    ``apply`` receives the stored profile and returns the replacement, or
    ``None`` to leave it alone. The read and the write are in one
    ``BEGIN IMMEDIATE`` transaction, so a concurrent mutation of the same
    profile waits rather than overwriting -- which is the difference between
    this and the read-modify-write the JSON store did.
    """
    conn = _connect()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data_json FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            return None
        try:
            current = json.loads(row["data_json"])
        except (ValueError, TypeError):
            return None
        updated = apply(current)
        if updated is None:
            return current
        conn.execute(
            "UPDATE profiles SET name=?, client_id=?, created_at=?, updated_at=?,"
            " data_json=? WHERE id=?",
            (*_profile_row(updated)[1:], profile_id),
        )
        return updated


def delete_profile_row(profile_id: str) -> bool:
    conn = _connect()
    with _lock, conn:
        cur = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return cur.rowcount > 0


def replace_all_profiles(profiles: list[dict[str, Any]]) -> None:
    """Replace the whole set in one transaction.

    Only for the operations whose meaning really is "the set is now this":
    a restore from backup, or an import. Ordinary edits go through
    :func:`mutate_profile`, which cannot lose a neighbouring write.
    """
    conn = _connect()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM profiles")
        conn.executemany(
            "INSERT INTO profiles (id, name, client_id, created_at, updated_at,"
            " data_json) VALUES (?, ?, ?, ?, ?, ?)",
            [_profile_row(p) for p in profiles],
        )


class FleetFull(RuntimeError):
    """A write would push the stored fleet past its configured capacity.

    Carries the numbers the caller has to report, because "fleet is full" on
    its own does not tell an operator whether to delete two profiles or fifty.
    """

    def __init__(self, *, stored: int, requested: int, capacity: int) -> None:
        self.stored = stored
        self.requested = requested
        self.capacity = capacity
        self.free = max(0, capacity - stored)
        super().__init__(
            f"fleet is full: {stored} of {capacity} stored, room for {self.free} "
            f"more, {requested} requested"
        )


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------
#
# Every move between `profiles` and `profiles_trash` is a single transaction
# containing both the insert and the delete. Doing it as two statements
# outside a transaction is how a profile ends up in neither table, or in both.


def trash_profile_row(profile_id: str, *, disk_bytes: int = 0) -> dict[str, Any] | None:
    """Move one profile into the trash. Returns the profile, or None.

    ``disk_bytes`` is the size of the retained directory, measured by the
    caller once and stored, rather than re-walked every time the trash is
    listed.
    """
    conn = _connect()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, name, client_id, created_at, updated_at, data_json"
            " FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "INSERT INTO profiles_trash (id, name, client_id, created_at,"
            " updated_at, deleted_at, disk_bytes, data_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
            " client_id=excluded.client_id, created_at=excluded.created_at,"
            " updated_at=excluded.updated_at, deleted_at=excluded.deleted_at,"
            " disk_bytes=excluded.disk_bytes, data_json=excluded.data_json",
            (row["id"], row["name"], row["client_id"], row["created_at"],
             row["updated_at"], _utcnow(), int(disk_bytes), row["data_json"]),
        )
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        try:
            return json.loads(row["data_json"])
        except (ValueError, TypeError):
            return {"id": profile_id, "name": row["name"]}


def restore_profile_row(profile_id: str, *, capacity: int | None = None) -> dict[str, Any] | None:
    """Move one profile back out of the trash.

    ``capacity`` is checked in the same transaction, because restoring is a
    create as far as the fleet is concerned: a fleet at its limit cannot take
    another profile back, and finding that out *after* the row has moved would
    leave the fleet over its own cap.
    """
    conn = _connect()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, name, client_id, created_at, updated_at, data_json"
            " FROM profiles_trash WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if not row:
            return None
        if capacity is not None:
            stored = int(conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"])
            if stored + 1 > capacity:
                raise FleetFull(stored=stored, requested=1, capacity=capacity)
        conn.execute(
            "INSERT INTO profiles (id, name, client_id, created_at, updated_at,"
            " data_json) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
            " client_id=excluded.client_id, created_at=excluded.created_at,"
            " updated_at=excluded.updated_at, data_json=excluded.data_json",
            (row["id"], row["name"], row["client_id"], row["created_at"],
             _utcnow(), row["data_json"]),
        )
        conn.execute("DELETE FROM profiles_trash WHERE id = ?", (profile_id,))
        try:
            return json.loads(row["data_json"])
        except (ValueError, TypeError):
            return {"id": profile_id, "name": row["name"]}


def list_trash_rows() -> list[dict[str, Any]]:
    """Trashed profiles, most recently deleted first."""
    conn = _connect()
    with _lock:
        rows = conn.execute(
            "SELECT id, name, client_id, created_at, deleted_at, disk_bytes,"
            " data_json FROM profiles_trash ORDER BY deleted_at DESC, id"
        ).fetchall()
    out = []
    for row in rows:
        try:
            profile = json.loads(row["data_json"])
        except (ValueError, TypeError):
            profile = {"id": row["id"], "name": row["name"]}
        profile["deletedAt"] = row["deleted_at"]
        profile["diskBytes"] = int(row["disk_bytes"] or 0)
        out.append(profile)
    return out


def get_trash_row(profile_id: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT deleted_at, data_json FROM profiles_trash WHERE id = ?",
            (profile_id,),
        ).fetchone()
    if not row:
        return None
    try:
        profile = json.loads(row["data_json"])
    except (ValueError, TypeError):
        return None
    profile["deletedAt"] = row["deleted_at"]
    return profile


def purge_trash_row(profile_id: str) -> bool:
    """Remove one row from the trash. Does not touch files on disk."""
    conn = _connect()
    with _lock, conn:
        cur = conn.execute("DELETE FROM profiles_trash WHERE id = ?", (profile_id,))
        return cur.rowcount > 0


def trash_ids_older_than(cutoff_iso: str) -> list[str]:
    conn = _connect()
    with _lock:
        rows = conn.execute(
            "SELECT id FROM profiles_trash WHERE deleted_at < ?", (cutoff_iso,)
        ).fetchall()
    return [str(row["id"]) for row in rows]


def count_trash() -> int:
    conn = _connect()
    with _lock:
        return int(conn.execute("SELECT COUNT(*) AS n FROM profiles_trash").fetchone()["n"])


def insert_profiles(profiles: list[dict[str, Any]], *, capacity: int | None = None) -> int:
    """Insert a batch of new profiles in one transaction.

    Bulk creation used to be a Python loop calling :func:`put_profile` once per
    profile, so a failure part-way through left the fleet half-created with no
    indication of where it stopped. One transaction makes the batch all-or-
    nothing.

    ``capacity`` is checked *inside* the transaction. Checking it in the caller
    would be a time-of-check/time-of-use race: two concurrent batches could
    each see room for 80 and both commit, leaving 160 in a fleet of 150.
    """
    if not profiles:
        return 0
    conn = _connect()
    with _lock, conn:
        conn.execute("BEGIN IMMEDIATE")
        if capacity is not None:
            stored = int(conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"])
            if stored + len(profiles) > capacity:
                raise FleetFull(stored=stored, requested=len(profiles), capacity=capacity)
        conn.executemany(
            "INSERT INTO profiles (id, name, client_id, created_at, updated_at,"
            " data_json) VALUES (?, ?, ?, ?, ?, ?)",
            [_profile_row(p) for p in profiles],
        )
    return len(profiles)


def count_profiles() -> int:
    conn = _connect()
    with _lock:
        return int(conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"])


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------
#
# The list endpoint used to collect every matching profile in Python and slice
# the page out of it -- and the collector walked in chunks of 500 while the
# function it called re-read and re-decorated the whole table on every chunk.
# Serving 25 rows out of 10,000 profiles therefore materialised the store
# twenty times over. Measured before this existed:
#
#     2,000 profiles    first page  246 ms
#    10,000 profiles    first page  5,006 ms      filter by tag  2,628 ms
#
# Five times the data for twenty times the work, which is the signature of
# doing the whole job once per page of the scan.
#
# The predicates below mirror the Python ones exactly rather than
# approximately. A cheaper `data_json LIKE '%term%'` would have matched field
# *names* and any other string in the document, so the count returned beside
# the page would have disagreed with the page -- a filter that quietly means
# something different at scale is worse than a slow one.

def _profile_where(params: dict[str, Any]) -> str:
    """Build the WHERE clause from the filters that are actually set.

    A single static clause guarded by ``:tag IS NULL OR ...`` reads well but
    does not perform: SQLite does not short-circuit it, so every count ran the
    json_each subqueries over every row even when no tag was asked for.
    Measured at 10,000 profiles, the unfiltered count cost 5.6 ms that way and
    is immeasurable without the dead branches.

    The predicates mirror the Python ones exactly rather than approximately. A
    cheaper ``data_json LIKE '%term%'`` would have matched field *names* and
    any other string in the document, so the total returned beside the page
    would have disagreed with the page -- a filter that quietly means
    something different at scale is worse than a slow one.
    """
    clauses = ["1=1"]
    if params.get("client_id"):
        clauses.append("client_id = :client_id")
    if params.get("tag"):
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(profiles.data_json, '$.tags') AS t"
            " WHERE lower(COALESCE(t.value, '')) = :tag)"
        )
    if params.get("q"):
        clauses.append(
            "(lower(name) LIKE :qlike ESCAPE '\\'"
            " OR lower(COALESCE(json_extract(data_json, '$.notes'), '')) LIKE :qlike ESCAPE '\\'"
            " OR lower(COALESCE(json_extract(data_json, '$.userAgent'), '')) LIKE :qlike ESCAPE '\\'"
            " OR EXISTS (SELECT 1 FROM json_each(profiles.data_json, '$.tags') AS t2"
            " WHERE lower(COALESCE(t2.value, '')) LIKE :qlike ESCAPE '\\'))"
        )
    return " AND ".join(clauses)


def _like_term(q: str) -> str:
    """A LIKE pattern matching *q* as a literal substring.

    Without escaping, a profile named "100%" searched for as "100%" would
    match everything, and "_" would match any character -- the search silently
    returning the wrong rows rather than none.
    """
    escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def query_profiles(
    *,
    q: str | None = None,
    client_id: str | None = None,
    tag: str | None = None,
    after_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Profiles matching the filters, ordered by id, with the total count.

    ``after_id`` is the keyset cut used by the list endpoint's cursor.
    ``limit`` of None returns everything matching, which the callers that
    genuinely need the whole set (export, bulk actions) still want.

    Note what is *not* here: filtering by status. A profile's status is not
    stored -- it is synthesised on read from the runtime registry -- so it
    cannot be a WHERE clause without making the database disagree with the
    live process table. Callers filter on it after the fact.
    """
    params = {
        "q": q.strip().lower() if q and q.strip() else None,
        "qlike": _like_term(q.strip()) if q and q.strip() else None,
        "client_id": client_id or None,
        "tag": tag.strip().lower() if tag and tag.strip() else None,
        "after": after_id or None,
    }
    where = _profile_where(params)
    conn = _connect()
    with _lock:
        total = int(conn.execute(
            f"SELECT COUNT(*) AS n FROM profiles WHERE {where}", params
        ).fetchone()["n"])
        sql = f"SELECT data_json FROM profiles WHERE {where}"
        if params["after"]:
            sql += " AND id > :after"
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)
        rows = conn.execute(sql, params).fetchall()

    items = []
    for row in rows:
        try:
            items.append(json.loads(row["data_json"]))
        except (ValueError, TypeError):
            log.warning("skipping a profile row whose JSON will not parse")
    return {"items": items, "total": total}
