"""An older build must refuse a database a newer build has migrated.

Migrations are forward-only: ``_migrate`` has no down path and nothing writes
one. So the dangerous direction is not upgrading, it is an operator who
upgrades, migrates their store, and then launches the previous build -- from a
distro package that did not update, a second machine, or a rollback.

Before this, ``_ensure_schema`` compared only ``current < SCHEMA_VERSION``.
With ``current`` ahead that is False, so the function fell straight through to
normal operation and the old build ran against a schema it did not know. For
the v7/v8 boundary that means the extension tables exist and the old build
ignores them, so an operator's extension assignments silently do not apply
while the console still lists them.
"""
from __future__ import annotations

import sqlite3

import pytest

from multiprofile.storage import db


def _profile_count(path: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "control.db"
    monkeypatch.setenv("MULTIPROFILE_CONTROL_DB", str(path))
    db.close()
    yield path
    db.close()


def test_a_database_at_the_current_version_opens(store):
    db._connect()
    assert store.exists()


def test_a_future_schema_is_refused_by_name(store):
    db._connect()
    # Stamp it as migrated by a build two versions ahead.
    conn = sqlite3.connect(str(store))
    with conn:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (db.SCHEMA_VERSION + 2, "2026-01-01T00:00:00Z"),
        )
    conn.close()
    db.close()

    with pytest.raises(db.SchemaTooNewError) as caught:
        db._connect()

    message = str(caught.value)
    assert f"v{db.SCHEMA_VERSION + 2}" in message
    assert f"v{db.SCHEMA_VERSION}" in message
    # The operator is told what to do, not just that it broke.
    assert "forward-only" in message
    assert "backup" in message


def test_the_refusal_does_not_damage_the_newer_database(store):
    """The whole point is that the old build touches nothing."""
    db._connect()
    db.close()
    conn = sqlite3.connect(str(store))
    with conn:
        conn.execute(
            "INSERT INTO profiles (id, name, created_at, updated_at, data_json) "
            "VALUES ('p1', 'kept', '2026-01-01', '2026-01-01', '{}')"
        )
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (db.SCHEMA_VERSION + 1, "2026-01-01T00:00:00Z"),
        )
    conn.close()
    db.close()

    with pytest.raises(db.SchemaTooNewError):
        db._connect()

    assert _profile_count(str(store)) == 1
    conn = sqlite3.connect(str(store))
    try:
        still = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        conn.close()
    assert still == db.SCHEMA_VERSION + 1, "the refusal must not rewrite the version"


def test_equal_version_is_not_refused(store):
    db._connect()
    db.close()
    conn = sqlite3.connect(str(store))
    with conn:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (db.SCHEMA_VERSION, "2026-01-01T00:00:00Z"),
        )
    conn.close()
    db.close()
    db._connect()  # must not raise
