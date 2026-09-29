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

SCHEMA_VERSION = 2

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
        try:
            os.chmod(path + suffix, 0o600)
        except OSError:
            # Missing sidecar, or a filesystem with no permission bits. The
            # startup hardening pass reports what it could not fix.
            pass


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
    return {"old": old, "new": final}


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
    result = {
        "recovered_jobs": recovered_jobs,
        "failed_jobs": failed_jobs,
        "recovered_profiles": recovered_profiles,
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


def count_profiles() -> int:
    conn = _connect()
    with _lock:
        return int(conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"])
