"""F51: a permanent delete that leaves the data behind must say so.

`delete_profile(purge_storage=True)` removed the database row and then tried
to remove the profile's Chrome directory and its cookie jar. Both failures
were swallowed -- the Chrome directory's without even a log line -- and the
function returned ``True`` regardless. The endpoint then answered

    {"ok": true, "permanent": true, "purgeStorage": true}

where `purgeStorage` was the **query parameter echoed back**, not the
outcome.

So a permanent delete could leave `chrome-profiles/<id>` intact -- every
cookie, every logged-in session, the browsing history, the cache -- and tell
the operator the storage was purged.

Why this one is worse than the other swallowed failures F11 covered: an
operator permanently deleting a profile is doing it **to destroy an
identity**. That is the entire purpose of the call. It is also the operation
`07_SECURITY_AND_COMPLIANCE.md` §6 points at when it answers "can we delete
our data?" with yes.

`shutil.rmtree` failing part-way is the ordinary case, not an exotic one: a
file still held open by a Chrome that was not stopped, a read-only mount,
ENOSPC, a permission change. It leaves part of the tree, and what remains is
as likely to be `Cookies` and `Login Data` as anything else -- which is why
these tests check the **filesystem** afterwards rather than trusting that no
exception was raised.
"""
from __future__ import annotations

import os
import stat

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


def _make_profile_with_storage(name: str = "doomed"):
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import data_dir

    profile = create_profile({"name": name})
    pid = profile["id"]
    root = data_dir() / "chrome-profiles" / pid
    (root / "Default").mkdir(parents=True)
    (root / "Default" / "Cookies").write_bytes(b"SQLite format 3\x00cookie-jar")
    (root / "Default" / "Login Data").write_bytes(b"SQLite format 3\x00logins")
    return pid, root


# --------------------------------------------------------------------------
# 1. The happy path still reports honestly
# --------------------------------------------------------------------------

def test_a_purge_that_succeeds_reports_purged():
    from multiprofile.storage.profiles import delete_profile

    pid, root = _make_profile_with_storage()
    report = delete_profile(pid, purge_storage=True)
    assert report is not None
    assert report["purged"] is True
    assert report["leftBehind"] == []
    assert not root.exists()


def test_not_asking_for_a_purge_is_not_a_failed_purge():
    from multiprofile.storage.profiles import delete_profile

    pid, root = _make_profile_with_storage()
    report = delete_profile(pid, purge_storage=False)
    assert report == {"purgeRequested": False, "purged": False, "leftBehind": []}
    assert root.exists(), "purge_storage=False must leave the directory alone"


# --------------------------------------------------------------------------
# 2. The finding
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_purge_that_cannot_remove_the_cookies_does_not_claim_it_did():
    """The directory is made unremovable the way a real failure arrives:
    its parent is read-only, so unlink inside it fails and rmtree leaves the
    tree standing."""
    from multiprofile.storage.profiles import delete_profile

    pid, root = _make_profile_with_storage()
    inner = root / "Default"
    original = stat.S_IMODE(inner.stat().st_mode)
    os.chmod(inner, 0o500)  # readable and traversable, not writable
    try:
        report = delete_profile(pid, purge_storage=True)
        assert report is not None
        assert report["purgeRequested"] is True
        assert report["purged"] is False, (
            "the purge left data on disk and reported success"
        )
        assert report["leftBehind"], "nothing was reported as left behind"
        assert str(root) in report["leftBehind"]
        # The thing that matters: the cookies are still there.
        assert (inner / "Cookies").exists()
    finally:
        os.chmod(inner, original)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_the_endpoint_reports_the_outcome_not_the_request():
    """`purgeStorage` in the response must describe what happened."""
    from multiprofile.api.server import create_app
    from multiprofile.storage.xdg import data_dir

    app = create_app()
    client = app.test_client()
    created = client.post("/api/profiles", json={"name": "doomed-api"})
    pid = created.get_json()["profile"]["id"]
    root = data_dir() / "chrome-profiles" / pid
    inner = root / "Default"
    inner.mkdir(parents=True)
    (inner / "Cookies").write_bytes(b"SQLite format 3\x00cookie-jar")
    original = stat.S_IMODE(inner.stat().st_mode)
    os.chmod(inner, 0o500)
    try:
        r = client.delete(f"/api/profiles/{pid}?permanent=1&purgeStorage=1")
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["purgeRequested"] is True
        assert body["purgeStorage"] is False, (
            f"the response claims the storage was purged: {body}"
        )
        assert body.get("warnings"), "no warning about what was left behind"
        assert any(str(root) in w for w in body["warnings"])
        assert (inner / "Cookies").exists()
    finally:
        os.chmod(inner, original)


def test_deleting_something_that_is_not_there_is_still_not_found():
    from multiprofile.storage.profiles import delete_profile

    assert delete_profile("profile_does_not_exist") is None


# --------------------------------------------------------------------------
# 3. The trash path shares the implementation, so it cannot drift
# --------------------------------------------------------------------------

def test_emptying_the_trash_uses_the_same_purge():
    from multiprofile.storage import profiles as store

    pid, root = _make_profile_with_storage("trashed")
    assert store.trash_profile(pid) is not None
    calls: list[str] = []
    real = store.purge_profile_files

    def spy(profile_id: str):
        calls.append(profile_id)
        return real(profile_id)

    store.purge_profile_files = spy
    try:
        assert store.purge_profile(pid) is True
    finally:
        store.purge_profile_files = real
    assert calls == [pid], "purge_profile no longer shares purge_profile_files"
    assert not root.exists()


# --------------------------------------------------------------------------
# 4. The same thing one level up: deleting the whole account
# --------------------------------------------------------------------------

def test_an_account_deletion_reports_completeness():
    from multiprofile import account

    _make_profile_with_storage("acct")
    report = account.delete_everything(account.DELETE_CONFIRMATION)
    assert report["complete"] is True, report
    assert report["leftBehind"] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_account_deletion_that_leaves_a_profile_says_so():
    """`delete_everything` reported `browserDirectories: N` and nothing about
    what it could not remove, so three of three and three of five looked the
    same. The cookie-jar loop was worse: a bare `except OSError: continue`.
    """
    from multiprofile import account
    from multiprofile.storage.xdg import data_dir

    _, root = _make_profile_with_storage("acct-stuck")
    inner = root / "Default"
    original = stat.S_IMODE(inner.stat().st_mode)
    os.chmod(inner, 0o500)
    try:
        report = account.delete_everything(account.DELETE_CONFIRMATION)
        assert report["complete"] is False, (
            f"an incomplete deletion reported as complete: {report}"
        )
        assert any(str(root) in p for p in report["leftBehind"]), report["leftBehind"]
        assert (inner / "Cookies").exists()
        assert (data_dir() / "chrome-profiles").is_dir()
    finally:
        os.chmod(inner, original)
