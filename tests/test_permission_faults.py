"""F11, security path: a permission tightening that fails must say so.

Four places wrote a file, called ``chmod(0o600)``, and swallowed the
``OSError``:

    storage/xdg.py  secure_write_json   every profiles/gateways/pools write
    storage/xdg.py  harden_existing     the startup repair pass
    storage/db.py   _harden             control.db and its WAL sidecars
    secrets.py      _store_in_file      the key every proxy password uses

Not raising is correct — a filesystem with no permission bits is a legitimate
place to run, and crashing a launch over a mode bit would be worse than the
problem. But "must not raise" was never a reason for "must not tell anyone".
The result was the quietest possible version of **F4**, where the cookie jar
and the cleartext proxy password were found world-readable: the code intends
0600, the chmod fails, the file is renamed into place at whatever the umask
allows, and every later reader sees 0600 in the source and 0644 on disk.

`storage/db.py` even carried a comment asserting that "the startup hardening
pass reports what it could not fix". It did not — ``harden_data_dir`` returned
only the names it *changed*, and a failure was indistinguishable from a file
that was already fine. That claim was load-bearing and false, which is the
same defect shape as F19: a control believed to exist somewhere else.

These tests pin that each of the four reports, that nothing raises, and that
the report reaches ``/api/health``.
"""
from __future__ import annotations

import os
import stat

import pytest

from multiprofile.storage import xdg


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    with xdg._permission_lock:
        xdg._permission_faults.clear()
    return tmp_path


def _break_chmod(monkeypatch, module=os):
    """Make every chmod fail, as a filesystem without mode bits would."""
    def boom(*a, **k):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(module, "chmod", boom)


# --------------------------------------------------------------------------
# 1. Each write path reports
# --------------------------------------------------------------------------

def test_secure_write_json_reports_a_failed_chmod(tmp_path, monkeypatch):
    """The file is still written -- the data matters more than the mode --
    but the fact that it is not owner-only is no longer secret."""
    _break_chmod(monkeypatch)
    target = tmp_path / "profiles.json"
    xdg.secure_write_json(target, {"a": 1})

    assert target.exists(), "the write must still happen"
    faults = xdg.permission_faults()
    assert len(faults) == 1
    assert str(target) in faults[0]["path"]
    assert "chmod" in faults[0]["detail"]


def test_a_failed_chmod_does_not_raise(tmp_path, monkeypatch):
    """Crashing over a mode bit would be worse than the problem, and is how
    this policy would get reverted."""
    _break_chmod(monkeypatch)
    xdg.secure_write_json(tmp_path / "x.json", {"a": 1})  # must not raise


def test_harden_existing_reports_rather_than_returning_a_bare_false(tmp_path, monkeypatch):
    """False used to mean both "already fine" and "could not fix it", which
    made the two indistinguishable to every caller."""
    target = tmp_path / "old.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o644)
    _break_chmod(monkeypatch)

    assert xdg.harden_existing(target) is False
    faults = xdg.permission_faults()
    assert len(faults) == 1
    assert "existing file" in faults[0]["detail"]


def test_a_file_that_is_already_owner_only_reports_nothing(tmp_path):
    target = tmp_path / "fine.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o600)
    assert xdg.harden_existing(target) is False
    assert xdg.permission_faults() == []


def test_the_control_database_reports_a_failed_chmod(tmp_path, monkeypatch):
    from multiprofile.storage import db

    path = tmp_path / "control.db"
    path.write_text("", encoding="utf-8")
    _break_chmod(monkeypatch)
    db._harden(str(path))

    faults = xdg.permission_faults()
    assert faults, "a readable control.db carries every profile's proxy host and notes"
    assert "control database" in faults[0]["detail"]


def test_a_missing_wal_sidecar_is_not_reported_as_a_failure(tmp_path):
    """WAL and SHM appear on first write; their absence is not a fault, and
    reporting it would bury the real ones in noise."""
    from multiprofile.storage import db

    path = tmp_path / "control.db"
    path.write_text("", encoding="utf-8")
    db._harden(str(path))
    assert xdg.permission_faults() == []


def test_the_encryption_key_reports_a_failed_chmod(tmp_path, monkeypatch):
    """The most consequential one: a readable key makes every encrypted
    proxy password decorative."""
    from multiprofile import secrets as secrets_mod

    _break_chmod(monkeypatch)
    secrets_mod._store_in_file(b"k" * 32)

    faults = xdg.permission_faults()
    assert faults
    assert "key file" in faults[0]["detail"]


# --------------------------------------------------------------------------
# 2. The report reaches the operator
# --------------------------------------------------------------------------

def test_health_carries_an_empty_block_when_everything_is_fine():
    """[] is a statement -- every write landed 0600 -- not an absence. A block
    that appeared only on trouble could not confirm the absence of it."""
    from multiprofile.api.server import create_app

    body = create_app().test_client().get("/api/health").get_json()
    assert body["permissions"] == {"faults": [], "faultCount": 0, "truncated": False}


def test_health_reports_a_file_that_could_not_be_tightened(tmp_path, monkeypatch):
    from multiprofile.api.server import create_app

    client = create_app().test_client()
    _break_chmod(monkeypatch)
    xdg.secure_write_json(tmp_path / "leaky.json", {"proxyPass": "x"})

    body = client.get("/api/health").get_json()
    paths = [f["path"] for f in body["permissions"]["faults"]]
    assert any("leaky.json" in p for p in paths), paths
    # Serving the request touches the control database, whose own chmod is
    # broken too under this monkeypatch -- so more than one fault here is the
    # correct answer, not noise. Asserting exactly one would have been
    # asserting that the database quietly failed to report.
    assert body["permissions"]["faultCount"] >= 1


# --------------------------------------------------------------------------
# 3. The registry itself
# --------------------------------------------------------------------------

def test_the_registry_is_bounded():
    """An unbounded list fed by every profile write is a memory leak."""
    for i in range(xdg._MAX_PERMISSION_FAULTS + 50):
        xdg.record_permission_fault(f"/tmp/f{i}", "detail")
    faults = xdg.permission_faults()
    assert len(faults) == xdg._MAX_PERMISSION_FAULTS
    assert xdg.permission_health()["truncated"] is True
    # The newest are kept: the oldest failure is the least actionable.
    assert faults[-1]["path"].endswith(str(xdg._MAX_PERMISSION_FAULTS + 49))


def test_recording_never_raises_whatever_it_is_handed():
    """It is called from inside except blocks. A recorder that could throw
    turns a permissions problem into a crash."""
    class Nasty:
        def __str__(self):
            raise RuntimeError("no")

    xdg.record_permission_fault(Nasty(), "fine")
    xdg.record_permission_fault("/tmp/x", Nasty())
    xdg.record_permission_fault(None, None)


def test_a_long_detail_is_truncated():
    xdg.record_permission_fault("/tmp/x", "y" * 5000)
    assert len(xdg.permission_faults()[0]["detail"]) <= 200


def test_faults_carry_a_timestamp():
    xdg.record_permission_fault("/tmp/x", "detail")
    assert xdg.permission_faults()[0]["ts"] > 0


# --------------------------------------------------------------------------
# 4. The behaviour that must not have regressed
# --------------------------------------------------------------------------

def test_a_normal_write_is_still_owner_only(tmp_path):
    """The whole point. F4 was this being 0644."""
    target = tmp_path / "profiles.json"
    xdg.secure_write_json(target, {"proxyPass": "secret"})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert xdg.permission_faults() == []
