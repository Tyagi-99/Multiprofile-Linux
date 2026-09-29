"""Taking everything out, and destroying everything.

Two rights the brief names under GDPR / India DPDP readiness — "data deletion
and export" — that the product could not honour. `backup.py` exported one
profile; nothing could answer "give me everything you hold" or "delete
everything you hold", and nothing could say what those answers would contain.

They matter before any regulator does: an export is how an operator moves to a
new machine, and a deletion is how they hand a laptop back.

Two properties carry most of these tests.

**The export documents its own gaps.** Proxy passwords are not in it. An
export whose omissions are undocumented is worse than one with no omissions,
because the hole is discovered at restore time — so the field names are in the
document itself, not only in this source.

**The deletion is real.** It removes the Chrome profile directories, the
cookie jars and the database files, not merely the rows that make them
visible. A delete that unlinks an index and leaves the data is the failure
this audit kept finding, one layer down.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from multiprofile import account


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


@pytest.fixture
def client():
    from multiprofile.api.server import create_app

    return create_app().test_client()


SECRET = "hunter2-very-secret"


def _populated(client):
    """A profile with credentials, a session directory and some history."""
    from multiprofile.storage import db
    from multiprofile.storage.xdg import data_dir

    pid = client.post("/api/profiles", json={
        "name": "Seller", "proxyHost": "1.2.3.4", "proxyPort": 8080,
        "proxyUser": "bob", "proxyPass": SECRET,
    }).get_json()["profile"]["id"]
    root = data_dir() / "chrome-profiles" / pid
    root.mkdir(parents=True, exist_ok=True)
    (root / "Cookies").write_bytes(b"session" * 200)
    client.post("/api/tokens", json={"name": "cron", "scopes": ["launch"]})
    for state in ("starting", "running", "stopping", "stopped"):
        db.set_profile_state(pid, state)
    return pid, root


# --------------------------------------------------------------------------
# 1. The export contains what it should
# --------------------------------------------------------------------------

def test_the_export_covers_every_section(client):
    _populated(client)
    document = account.export_all()
    for section in ("profiles", "clients", "templates", "usage",
                    "apiTokens", "plan", "audit", "cookies", "trash"):
        assert section in document["sections"], section
    assert document["errors"] == {}


def test_the_export_includes_the_profiles_and_their_settings(client):
    pid, _ = _populated(client)
    document = account.export_all()
    profile = next(p for p in document["sections"]["profiles"] if p["id"] == pid)
    assert profile["name"] == "Seller"
    assert profile["proxyHost"] == "1.2.3.4"
    assert profile["proxyUser"] == "bob"
    assert profile["seed"]


def test_the_export_includes_measured_usage(client):
    _populated(client)
    document = account.export_all()
    assert document["sections"]["usage"]["summary"]["total"]["sessions"] == 1
    assert len(document["sections"]["usage"]["sessions"]) == 1


def test_the_export_counts_what_it_contains(client):
    _populated(client)
    document = account.export_all()
    assert document["counts"]["profiles"] == len(document["sections"]["profiles"])


# --------------------------------------------------------------------------
# 2. ...and is honest about what it omits
# --------------------------------------------------------------------------

def test_no_proxy_password_reaches_the_export(client):
    pid, _ = _populated(client)
    blob = json.dumps(account.export_all(), default=str)
    assert SECRET not in blob
    assert "mpenc:v1:" not in blob, "the wrapped copy must not travel either"


def test_the_export_names_the_fields_it_redacted(client):
    """Undocumented gaps are discovered at restore time, which is too late."""
    _populated(client)
    document = account.export_all()
    assert set(document["redacted"]["fields"]) == set(account.REDACTED_FIELDS)
    assert "re-entered" in document["redacted"]["why"]


def test_api_token_hashes_do_not_travel(client):
    """Credential material has no business in a file made to be copied."""
    _populated(client)
    blob = json.dumps(account.export_all(), default=str)
    from multiprofile.storage import db

    row = db.list_api_tokens()[0]
    stored = db.get_api_token(row["id"])
    assert stored["token_hash"] not in blob


def test_runtime_facts_are_not_exported_as_if_they_were_data(client):
    """A pid describes a process that is gone by the time anyone reads this."""
    _populated(client)
    for profile in account.export_all()["sections"]["profiles"]:
        for transient in ("pid", "isRunning", "cdpPort", "launchState"):
            assert transient not in profile


# --------------------------------------------------------------------------
# 3. It degrades rather than failing whole
# --------------------------------------------------------------------------

def test_one_unreadable_section_does_not_cost_the_others(client, monkeypatch):
    """And the failure is recorded in the document, not swallowed: an export
    that silently omits a section is the thing to avoid."""
    _populated(client)
    import multiprofile.audit as audit_mod

    def boom(*a, **k):
        raise RuntimeError("audit log is unreadable")

    monkeypatch.setattr(audit_mod, "read_audit", boom)
    document = account.export_all()
    assert "audit" not in document["sections"]
    assert "unreadable" in document["errors"]["audit"]
    assert document["sections"]["profiles"], "the rest still came out"


# --------------------------------------------------------------------------
# 4. Writing it to a file
# --------------------------------------------------------------------------

def test_an_exported_file_is_readable_only_by_its_owner(client, tmp_path):
    """It carries cookie jars, which are live session credentials even with
    the proxy passwords stripped. F4 was exactly this class of mistake."""
    _populated(client)
    result = account.export_to_file(tmp_path / "out" / "export.json")
    mode = pathlib.Path(result["path"]).stat().st_mode
    assert mode & 0o077 == 0, oct(mode)


def test_an_exported_file_is_valid_json(client, tmp_path):
    _populated(client)
    result = account.export_to_file(tmp_path / "export.json")
    parsed = json.loads(pathlib.Path(result["path"]).read_text(encoding="utf-8"))
    assert parsed["format"] == "multiprofile-account-export"
    assert result["bytes"] > 0


# --------------------------------------------------------------------------
# 5. The endpoint
# --------------------------------------------------------------------------

def test_the_export_endpoint_offers_a_download(client):
    _populated(client)
    r = client.get("/api/account/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    assert json.loads(r.data)["format"] == "multiprofile-account-export"


def test_the_export_endpoint_does_not_leak_the_password(client):
    _populated(client)
    assert SECRET.encode() not in client.get("/api/account/export").data


# --------------------------------------------------------------------------
# 6. Deletion: the preview
# --------------------------------------------------------------------------

def test_a_preview_says_what_would_go_without_doing_it(client):
    """Nobody should have to run an irreversible operation to learn what it
    covers."""
    from multiprofile.storage import db

    pid, root = _populated(client)
    preview = client.get("/api/account/deletion-preview").get_json()
    assert preview["profiles"] == db.count_profiles()
    assert preview["usageSessions"] == 1
    assert preview["apiTokens"] == 1
    assert preview["browserDirectories"] == 1
    assert preview["confirmation"] == account.DELETE_CONFIRMATION
    # Nothing was destroyed by asking.
    assert root.exists()
    assert db.count_profiles() > 0


# --------------------------------------------------------------------------
# 7. Deletion: the act
# --------------------------------------------------------------------------

@pytest.mark.parametrize("wrong", [None, "", "yes", "true", "delete everything", "DELETE  EVERYTHING"])
def test_deletion_requires_the_exact_confirmation(client, wrong):
    """A boolean is too easy to send by accident from a script that meant
    something else, and there is no undo behind this one."""
    from multiprofile.storage import db

    _populated(client)
    before = db.count_profiles()
    r = client.post("/api/account/delete", json={"confirm": wrong})
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "confirm"
    assert db.count_profiles() == before


def test_deletion_removes_the_browser_directories(client):
    """The cookies and logins. A delete that leaves these is hiding an index,
    not deleting data."""
    _populated(client)
    pid, root = _populated(client)
    assert root.exists()
    r = client.post("/api/account/delete", json={"confirm": account.DELETE_CONFIRMATION})
    assert r.status_code == 200
    assert not root.exists()


def test_deletion_removes_the_database_and_its_sidecars(client, tmp_path):
    _populated(client)
    client.post("/api/account/delete", json={"confirm": account.DELETE_CONFIRMATION})
    for name in ("control.db", "control.db-wal", "control.db-shm"):
        assert not (tmp_path / "data" / "multiprofile" / name).exists(), name


def test_nothing_of_the_secret_survives_deletion(client, tmp_path):
    """Asserted over the whole data directory, not over the rows."""
    _populated(client)
    client.post("/api/account/delete", json={"confirm": account.DELETE_CONFIRMATION})
    for path in (tmp_path / "data").rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path


def test_deletion_reports_what_it_removed(client):
    _populated(client)
    removed = client.post(
        "/api/account/delete", json={"confirm": account.DELETE_CONFIRMATION}
    ).get_json()["removed"]
    assert removed["database"] is True
    assert removed["browserDirectories"] >= 1
    assert removed["deletedAt"]


def test_the_application_still_works_after_a_deletion(client):
    """An empty install, not a broken one: the database is recreated on the
    next access rather than leaving a process holding a dead handle."""
    _populated(client)
    client.post("/api/account/delete", json={"confirm": account.DELETE_CONFIRMATION})
    r = client.get("/api/profiles")
    assert r.status_code == 200
    r = client.post("/api/profiles", json={"name": "fresh start"})
    assert r.status_code == 200


def test_deleting_twice_does_not_fail(client):
    _populated(client)
    for _ in range(2):
        r = client.post("/api/account/delete", json={"confirm": account.DELETE_CONFIRMATION})
        assert r.status_code == 200
