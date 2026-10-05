"""F11's residual, where swallowing a failure cost the operator something.

The policy the finding asks for is not "remove every blind except" -- a launch
path must never crash a browser, and that rationale is legitimate. It is:
**a handler on a path with consequences must say what it swallowed.**

The identity and security paths were done in earlier passes
(``browser/identity_faults.py``, ``storage/xdg.permission_faults``). These
tests cover the two that were left, both found by asking of each remaining
handler "what does the operator lose if this fires?":

**A restore could lose every session and report success.** `restore` swallowed
per-member cookie failures and returned ``ok: True`` with no mention of
cookies at all. Restoring a profile is usually *done for* the logins, so the
one thing the operator was checking for was the one thing not reported.

**An unreadable cookie jar was indistinguishable from an empty one.**
`load_cookies` returned ``[]`` for no file, an empty jar, and a truncated or
half-written one alike. A profile whose jar was damaged looked exactly like a
profile that had never been logged into, and started signed out of everything
with nothing said anywhere.

Returning ``[]`` and continuing is still correct in both. Refusing to launch
over a cookie file would be worse than launching without cookies. Doing it
silently was the defect.
"""
from __future__ import annotations

import json
import zipfile

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


# --------------------------------------------------------------------------
# 1. An unreadable cookie jar is not an empty one
# --------------------------------------------------------------------------

def test_a_truncated_jar_is_reported(caplog):
    from multiprofile import cookies_store

    path = cookies_store.cookies_path("p1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[{"name": "session", "value": "abc"', encoding="utf-8")  # truncated

    with caplog.at_level("WARNING"):
        assert cookies_store.load_cookies("p1") == []
    assert any("could not be read" in r.message for r in caplog.records), caplog.text


def test_the_damaged_file_is_left_in_place():
    """The operator may be able to recover it. Deleting it to tidy up would
    remove that chance, and this function is not the one to decide."""
    from multiprofile import cookies_store

    path = cookies_store.cookies_path("p1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")
    cookies_store.load_cookies("p1")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "not json at all"


def test_valid_json_of_the_wrong_shape_is_reported(caplog):
    from multiprofile import cookies_store

    path = cookies_store.cookies_path("p1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"unexpected": true}', encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert cookies_store.load_cookies("p1") == []
    assert any("not a cookie list" in r.message for r in caplog.records), caplog.text


def test_a_genuinely_absent_jar_says_nothing(caplog):
    """No file is not a failure, and reporting it would bury the real ones."""
    from multiprofile import cookies_store

    with caplog.at_level("WARNING"):
        assert cookies_store.load_cookies("never-existed") == []
    assert not caplog.records


def test_a_healthy_jar_still_loads():
    from multiprofile import cookies_store

    cookies_store.save_cookies("p1", [{"name": "a", "value": "1", "domain": "x.test"}])
    loaded = cookies_store.load_cookies("p1")
    assert len(loaded) == 1 and loaded[0]["name"] == "a"


# --------------------------------------------------------------------------
# 2. A restore says what it restored
# --------------------------------------------------------------------------

def _backup_with_cookie_members(tmp_path, members: dict[str, bytes]):
    """A plaintext backup zip carrying the given cookies/ members."""
    from multiprofile.storage.profiles import create_profile

    pid = create_profile({"name": "src", "proxyType": "none"})["id"]
    zpath = tmp_path / "b.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "version": "2.0", "profileId": pid,
            "profile": {"id": pid, "name": "src"},
        }))
        for name, blob in members.items():
            zf.writestr(name, blob)
    return pid, zpath


def test_a_restore_reports_how_many_cookies_it_restored(tmp_path):
    from multiprofile.backup import restore_backup

    pid, zpath = _backup_with_cookie_members(tmp_path, {
        "cookies/jar.json": json.dumps(
            [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
        ).encode(),
    })
    out = restore_backup(backup_path=str(zpath), profile_id=pid)
    assert out["ok"] is True
    assert out["restoredCookies"] == 2
    assert out["cookieFailures"] == []


def test_a_restore_that_could_not_read_the_cookies_says_so(tmp_path):
    """The finding, stated as what it cost: ok:True, no sessions, no word."""
    from multiprofile.backup import restore_backup

    pid, zpath = _backup_with_cookie_members(tmp_path, {
        "cookies/jar.json": b'[{"name": "a"',  # truncated
    })
    out = restore_backup(backup_path=str(zpath), profile_id=pid)
    assert out["restoredCookies"] == 0
    assert out["cookieFailures"], "a restore that lost every session must say so"
    assert "jar.json" in out["cookieFailures"][0]


def test_one_bad_member_does_not_cost_the_good_ones(tmp_path):
    """Aborting the whole restore would be the opposite overcorrection."""
    from multiprofile.backup import restore_backup

    pid, zpath = _backup_with_cookie_members(tmp_path, {
        "cookies/bad.json": b"{{{",
        "cookies/good.json": json.dumps([{"name": "a", "value": "1"}]).encode(),
    })
    out = restore_backup(backup_path=str(zpath), profile_id=pid)
    assert out["ok"] is True
    assert out["restoredCookies"] == 1
    assert len(out["cookieFailures"]) == 1


def test_both_fields_are_always_present(tmp_path):
    """0 with no failures is "this backup had no cookies"; 0 with failures is
    "it had cookies and you did not get them". A field that appeared only on
    trouble would make the two look alike again."""
    from multiprofile.backup import restore_backup

    pid, zpath = _backup_with_cookie_members(tmp_path, {})
    out = restore_backup(backup_path=str(zpath), profile_id=pid)
    assert out["restoredCookies"] == 0
    assert out["cookieFailures"] == []
