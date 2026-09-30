"""Opening a database that already exists must work, not just creating one.

Found by migrating a copy of a real install rather than by running the suite.
v7 put ``CREATE INDEX idx_jobs_batch ON jobs(batch_id)`` in the base schema.
That is correct for a new database and **fatal for an existing one**:

    sqlite3.OperationalError: no such column: batch_id

``_ensure_schema`` runs ``_SCHEMA_SQL`` *before* ``_migrate``. On an existing
database every ``CREATE TABLE IF NOT EXISTS`` in that script is a no-op, so
the ``jobs`` table is still the old one and the column the index needs does
not exist yet. Every install that already had a control database would have
failed to open it on the first launch after the update.

**The whole suite missed it because every test starts from an empty
directory**, which is precisely the case that works. So these tests do the
opposite: they build a database at an older version and open it with the
current code. One of them is a static check over `_SCHEMA_SQL` itself, since
the next person to add an index over a migrated column will not think of this
either.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from multiprofile.storage import db

#: Columns v7 adds to `jobs` by ALTER. Anything indexed over one of these
#: belongs after the migration, not in the base schema.
V7_JOB_COLUMNS = ("batch_id", "batch_seq", "cancel_requested")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    db.close()
    yield
    db.close()


def _make_old_database(path, version: int) -> None:
    """A control database as it looked before v7.

    Only the parts that matter are recreated: the version marker, and a
    ``jobs`` table without the columns v7 adds. That is enough to reproduce
    the failure exactly, and a full historical schema dump would rot.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE jobs (
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
        CREATE INDEX idx_jobs_status ON jobs(status);
        CREATE INDEX idx_jobs_profile ON jobs(profile_id);
        """
    )
    for v in range(1, version + 1):
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (v, "2026-01-01T00:00:00+00:00"),
        )
    conn.execute(
        "INSERT INTO jobs (job_type, profile_id, payload_json, status, "
        "created_at, updated_at) VALUES ('health_check', 'p_old', '{}', "
        "'queued', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# 1. The failure, reproduced and fixed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6])
def test_a_database_from_any_earlier_version_still_opens(tmp_path, version):
    """The `jobs` table has existed since v1, so every one of these needs the
    ALTER — not only an upgrade from v6."""
    _make_old_database(tmp_path / "data" / "multiprofile" / "control.db", version)
    assert db.count_profiles() == 0  # opens, migrates, answers


@pytest.mark.parametrize("version", [1, 6])
def test_the_migration_adds_the_v7_columns(tmp_path, version):
    _make_old_database(tmp_path / "data" / "multiprofile" / "control.db", version)
    db.count_profiles()
    conn = sqlite3.connect(str(db.db_path()))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    conn.close()
    assert set(V7_JOB_COLUMNS) <= cols, cols


def test_the_batch_index_is_created_on_an_upgraded_database(tmp_path):
    """The index still has to exist afterwards. Dropping it to dodge the
    error would trade a crash for a table scan on every batch read."""
    _make_old_database(tmp_path / "data" / "multiprofile" / "control.db", 6)
    db.count_profiles()
    conn = sqlite3.connect(str(db.db_path()))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_jobs_batch" in names


def test_job_batches_appears_on_an_upgraded_database(tmp_path):
    _make_old_database(tmp_path / "data" / "multiprofile" / "control.db", 6)
    db.count_profiles()
    conn = sqlite3.connect(str(db.db_path()))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "job_batches" in tables


def test_existing_rows_survive_the_migration(tmp_path):
    """A migration that loses the queue is worse than one that fails."""
    _make_old_database(tmp_path / "data" / "multiprofile" / "control.db", 6)
    db.count_profiles()
    rows = db.list_job_records(limit=10)
    assert len(rows) == 1
    assert rows[0]["profile_id"] == "p_old"
    assert rows[0]["job_type"] == "health_check"
    # The new columns take their defaults rather than NULL-ing the row.
    assert rows[0]["batch_id"] is None
    assert rows[0]["batch_seq"] == 0
    assert rows[0]["cancel_requested"] == 0


def test_the_version_marker_is_recorded(tmp_path):
    _make_old_database(tmp_path / "data" / "multiprofile" / "control.db", 6)
    db.count_profiles()
    conn = sqlite3.connect(str(db.db_path()))
    versions = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
    conn.close()
    assert db.SCHEMA_VERSION in versions


def test_opening_an_upgraded_database_twice_is_fine(tmp_path):
    """`_ensure_late_indexes` runs unconditionally, so it has to be
    idempotent — and a second open must not try to migrate again."""
    _make_old_database(tmp_path / "data" / "multiprofile" / "control.db", 6)
    db.count_profiles()
    db.close()
    assert db.count_profiles() == 0


def test_a_database_already_at_the_current_version_gains_a_missing_index(tmp_path):
    """A startup that died between the ALTER and the CREATE INDEX would
    otherwise leave the index missing forever, because the version marker
    already says v7 and migrations would never run again."""
    path = tmp_path / "data" / "multiprofile" / "control.db"
    _make_old_database(path, 6)
    db.count_profiles()
    db.close()
    conn = sqlite3.connect(str(path))
    conn.execute("DROP INDEX idx_jobs_batch")
    conn.commit()
    conn.close()

    db.count_profiles()
    conn = sqlite3.connect(str(path))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_jobs_batch" in names


# --------------------------------------------------------------------------
# 2. The guard, so the next one is caught by reading rather than by an outage
# --------------------------------------------------------------------------

def test_the_base_schema_indexes_no_migrated_column():
    """`_SCHEMA_SQL` runs before `_migrate`, so an index over a column that
    only a migration adds cannot work on an existing database. This is the
    static form of the bug above, and it catches the next one at the point it
    is written instead of on somebody's install."""
    offenders = []
    for match in re.finditer(r"CREATE INDEX[^;]+;", db._SCHEMA_SQL, re.IGNORECASE):
        statement = match.group(0)
        for column in V7_JOB_COLUMNS:
            if re.search(rf"\b{column}\b", statement):
                offenders.append(statement.strip())
    assert not offenders, (
        "these indexes reference a column added by a migration and must move "
        f"to _ensure_late_indexes: {offenders}"
    )


def test_a_fresh_database_gets_the_same_shape_as_an_upgraded_one(tmp_path):
    """The two paths must converge, or a bug only reproduces on one of them —
    which is exactly how this was missed."""
    db.count_profiles()  # fresh
    conn = sqlite3.connect(str(db.db_path()))
    fresh_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    fresh_idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'")}
    fresh_tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    db.close()

    upgraded = tmp_path / "upgraded" / "multiprofile" / "control.db"
    _make_old_database(upgraded, 6)
    import os

    os.environ["XDG_DATA_HOME"] = str(tmp_path / "upgraded")
    db.close()
    db.count_profiles()
    conn = sqlite3.connect(str(db.db_path()))
    up_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    up_idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'")}
    conn.close()

    assert fresh_cols == up_cols, (fresh_cols ^ up_cols)
    assert fresh_idx == up_idx, (fresh_idx ^ up_idx)
    assert "job_batches" in fresh_tables
