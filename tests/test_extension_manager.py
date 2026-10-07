"""Assignment bookkeeping: idempotency, isolation between profiles, and the
rule that nothing claims an install it has not observed.

These are the behaviours a bulk feature gets wrong quietly. A batch that
reports success when one profile failed, a re-assignment that duplicates or
downgrades a working install, an unassign that reaches profiles it was not
asked about -- none of those raise an exception, so each one needs a test that
would notice.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from multiprofile.extensions import manager, store
from multiprofile.extensions.capability import BrowserCapability
from multiprofile.extensions.identity import (
    looks_like_extension_id,
    predict_unpacked_id,
)
from multiprofile.extensions.package import PackageError
from multiprofile.storage.profiles import create_profile

MANIFEST = {
    "manifest_version": 3,
    "name": "Assignable",
    "version": "1.0.0",
    "permissions": ["storage"],
}

UNSUPPORTED = BrowserCapability(
    binary="/usr/bin/google-chrome", brand="google-chrome",
    version="150.0.0.0", major=150, unpacked=False, crx=False,
    reason="Google Chrome 150 removed --load-extension.",
    remediation="Use Chromium or Chrome for Testing.",
)

SUPPORTED = BrowserCapability(
    binary="/usr/bin/chromium", brand="chromium", version="151.0.0.0",
    major=151, unpacked=True, crx=False, reason="chromium accepts it.",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.storage import db

    db.close()
    yield
    db.close()


def _zip(tmp_path: Path, name: str = "ext.zip", **overrides) -> Path:
    data = dict(MANIFEST)
    data.update(overrides)
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", json.dumps(data))
        zf.writestr("sw.js", "// nothing")
    return p


def _ext(tmp_path, **overrides):
    return manager.import_zip(_zip(tmp_path, **overrides))


def _profiles(n: int, **extra):
    return [
        create_profile({"name": f"p{i}", "proxyType": "none", **extra})["id"]
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def test_an_imported_package_is_not_called_installed(tmp_path):
    """The whole point of the two-table split."""
    ext = _ext(tmp_path)
    assert ext["packageState"] == store.PACKAGE_IMPORTED
    assert ext["packageState"] != "installed"
    # and no profile has it
    assert store.list_assignments(ext_uuid=ext["id"]) == []


def test_the_payload_lands_in_managed_storage_with_a_stable_path(tmp_path):
    ext = _ext(tmp_path)
    payload = Path(ext["payloadPath"])
    assert payload.is_dir()
    assert (payload / "manifest.json").is_file()
    assert payload.name == "payload", payload
    # Re-reading the row must not move it: the ID depends on this path.
    again = store.get_extension(ext["id"])
    assert again is not None and again["payloadPath"] == ext["payloadPath"]


def test_the_expected_extension_id_is_derived_from_that_path(tmp_path):
    ext = _ext(tmp_path)
    assert ext["extensionId"] == predict_unpacked_id(ext["payloadPath"])
    assert looks_like_extension_id(ext["extensionId"])


def test_a_folder_import_copies_rather_than_loading_in_place(tmp_path):
    """Loading the operator's own folder would tie the extension's identity --
    and so its storage -- to a directory they may move or delete."""
    src = tmp_path / "unpacked"
    src.mkdir()
    (src / "manifest.json").write_text(json.dumps(MANIFEST))
    ext = manager.import_folder(src)
    assert Path(ext["payloadPath"]).resolve() != src.resolve()
    assert (Path(ext["payloadPath"]) / "manifest.json").is_file()
    assert ext["sourceDetail"] == str(src.resolve())
    # The original is untouched.
    assert (src / "manifest.json").is_file()


def test_an_archive_missing_a_referenced_file_leaves_nothing_behind(tmp_path):
    z = _zip(tmp_path, background={"service_worker": "absent.js"})
    with pytest.raises(PackageError) as e:
        manager.import_zip(z)
    assert e.value.code == "missing_files"
    assert store.list_extensions() == []


# ---------------------------------------------------------------------------
# assignment
# ---------------------------------------------------------------------------

def test_assigning_to_three_of_four_leaves_the_fourth_alone(tmp_path):
    ext = _ext(tmp_path)
    pids = _profiles(4)
    result = manager.assign(ext["id"], pids[:3], capability=SUPPORTED)
    assert result.ok
    assert result.to_dict()["succeeded"] == 3
    assigned = {a["profileId"] for a in store.list_assignments(ext_uuid=ext["id"])}
    assert assigned == set(pids[:3])
    assert pids[3] not in assigned


def test_a_stopped_profile_is_assigned_for_its_next_launch(tmp_path):
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    result = manager.assign(ext["id"], [pid], capability=SUPPORTED)
    assert result.results[0].state == store.STATE_ASSIGNED
    assert "next launch" in result.results[0].detail


def test_repeating_an_assignment_is_idempotent(tmp_path):
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    rows = store.list_assignments(ext_uuid=ext["id"])
    assert len(rows) == 1, rows


def test_the_same_profile_named_twice_in_one_call_is_one_profile(tmp_path):
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    result = manager.assign(ext["id"], [pid, pid, pid], capability=SUPPORTED)
    assert len(result.results) == 1
    assert len(store.list_assignments(ext_uuid=ext["id"])) == 1


def test_reassigning_does_not_downgrade_a_verified_install(tmp_path):
    """A verified install re-assigned must not drop back to "assigned" and
    make the console report work still to do."""
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    store.set_assignment_state(ext["id"], pid, store.STATE_INSTALLED_VERIFIED,
                               observed_ext_id=ext["extensionId"])
    result = manager.assign(ext["id"], [pid], capability=SUPPORTED)
    assert result.results[0].state == store.STATE_INSTALLED_VERIFIED
    row = store.get_assignment(ext["id"], pid)
    assert row is not None and row["state"] == store.STATE_INSTALLED_VERIFIED


def test_an_unsupported_browser_fails_the_assignment_with_remediation(tmp_path):
    """Never silently succeed on a browser that cannot load it."""
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    result = manager.assign(ext["id"], [pid], capability=UNSUPPORTED)
    assert not result.ok
    assert result.results[0].state == store.STATE_FAILED
    assert "Chrome for Testing" in result.results[0].detail


def test_a_missing_payload_fails_every_profile_rather_than_passing_a_bad_path(tmp_path):
    """--load-extension with a path that is not there makes Chromium complain
    and can take the other extensions down with it."""
    import shutil

    ext = _ext(tmp_path)
    pids = _profiles(2)
    shutil.rmtree(ext["payloadPath"])
    result = manager.assign(ext["id"], pids, capability=SUPPORTED)
    assert not result.ok
    assert all(r.state == store.STATE_FAILED for r in result.results)
    assert "missing" in result.results[0].detail


def test_an_unknown_profile_fails_only_itself(tmp_path):
    """One bad ID must not take the batch down."""
    ext = _ext(tmp_path)
    pids = _profiles(2)
    result = manager.assign(ext["id"], [*pids, "profile_nope"],
                            capability=SUPPORTED)
    body = result.to_dict()
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["failedProfileIds"] == ["profile_nope"]
    assert not result.ok, "a batch with a failure is not a successful batch"


def test_a_partial_failure_is_retryable_on_its_own(tmp_path):
    ext = _ext(tmp_path)
    pids = _profiles(3)
    store.upsert_assignment(ext["id"], pids[0], state=store.STATE_FAILED,
                            last_error="simulated")
    store.upsert_assignment(ext["id"], pids[1],
                            state=store.STATE_INSTALLED_VERIFIED)
    failed = [a["profileId"] for a in store.list_assignments(ext_uuid=ext["id"])
              if a["state"] == store.STATE_FAILED]
    assert failed == [pids[0]]
    result = manager.assign(ext["id"], failed, capability=SUPPORTED)
    assert result.ok
    # the already-verified profile was not touched by the retry
    other = store.get_assignment(ext["id"], pids[1])
    assert other is not None
    assert other["state"] == store.STATE_INSTALLED_VERIFIED


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def test_verification_requires_the_browser_to_report_the_id(tmp_path):
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    results = manager.verify_profile(pid, [ext["extensionId"]])
    assert results[0].ok
    assert results[0].state == store.STATE_INSTALLED_VERIFIED
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["observedExtensionId"] == ext["extensionId"]
    assert row["verifiedAt"]


def test_an_absent_id_is_a_failure_not_an_install(tmp_path):
    """The bug this whole design exists to prevent."""
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    results = manager.verify_profile(pid, ["cccccccccccccccccccccccccccccccc"])
    assert not results[0].ok
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["state"] == store.STATE_FAILED
    assert "did not report" in (row["lastError"] or "")


def test_applied_is_not_verified(tmp_path):
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    manager.mark_applied(pid, [ext["id"]])
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["appliedAt"]
    assert row["state"] == store.STATE_ASSIGNED
    assert row["verifiedAt"] is None


# ---------------------------------------------------------------------------
# launch wiring
# ---------------------------------------------------------------------------

def test_only_enabled_assignments_are_handed_to_the_browser(tmp_path):
    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    assert len(manager.payload_paths_for_profile(pid)) == 1
    store.set_enabled(ext["id"], pid, False)
    assert manager.payload_paths_for_profile(pid) == []


def test_a_payload_that_vanished_is_skipped_and_recorded(tmp_path):
    import shutil

    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    shutil.rmtree(ext["payloadPath"])
    assert manager.payload_paths_for_profile(pid) == []
    row = store.get_assignment(ext["id"], pid)
    assert row is not None and row["state"] == store.STATE_FAILED


# ---------------------------------------------------------------------------
# removal
# ---------------------------------------------------------------------------

def test_unassigning_one_profile_leaves_the_others(tmp_path):
    ext = _ext(tmp_path)
    pids = _profiles(3)
    manager.assign(ext["id"], pids, capability=SUPPORTED)
    manager.unassign(ext["id"], pids[0])
    left = {a["profileId"] for a in store.list_assignments(ext_uuid=ext["id"])}
    assert left == set(pids[1:])


def test_unassigning_does_not_delete_the_shared_payload(tmp_path):
    """Other profiles still reference these files."""
    ext = _ext(tmp_path)
    pids = _profiles(2)
    manager.assign(ext["id"], pids, capability=SUPPORTED)
    manager.unassign(ext["id"], pids[0])
    assert Path(ext["payloadPath"], "manifest.json").is_file()
    assert manager.payload_paths_for_profile(pids[1])


def test_deleting_while_assigned_is_refused_by_default(tmp_path):
    ext = _ext(tmp_path)
    pids = _profiles(2)
    manager.assign(ext["id"], pids, capability=SUPPORTED)
    with pytest.raises(PackageError) as e:
        manager.delete(ext["id"])
    assert e.value.code == "still_assigned"
    assert Path(ext["payloadPath"], "manifest.json").is_file()


def test_forced_delete_removes_the_assignments_and_the_files(tmp_path):
    ext = _ext(tmp_path)
    pids = _profiles(2)
    manager.assign(ext["id"], pids, capability=SUPPORTED)
    result = manager.delete(ext["id"], force=True)
    assert result["removedAssignments"] == 2
    assert result["filesRemoved"] is True
    assert not Path(ext["payloadPath"]).exists()
    assert store.get_extension(ext["id"]) is None


# ---------------------------------------------------------------------------
# update and rollback
# ---------------------------------------------------------------------------

def test_an_update_keeps_the_payload_path_and_so_the_identity(tmp_path):
    """A versioned directory would give the new payload a new extension ID and
    hand every profile an empty extension instead of an update."""
    ext = _ext(tmp_path)
    before_path, before_id = ext["payloadPath"], ext["extensionId"]
    newer = _zip(tmp_path, name="v2.zip", version="2.0.0")
    updated = manager.update_extension(ext["id"], zip_path=newer)
    assert updated["version"] == "2.0.0"
    assert updated["payloadPath"] == before_path
    assert predict_unpacked_id(updated["payloadPath"]) == before_id


def test_an_update_retains_the_previous_version_for_rollback(tmp_path):
    ext = _ext(tmp_path)
    manager.update_extension(ext["id"], zip_path=_zip(tmp_path, name="v2.zip",
                                                      version="2.0.0"))
    versions = store.list_versions(ext["id"])
    assert any(v["version"] == "1.0.0" for v in versions), versions
    restored = manager.rollback(ext["id"])
    assert restored["version"] == "1.0.0"
    manifest = json.loads(
        Path(restored["payloadPath"], "manifest.json").read_text())
    assert manifest["version"] == "1.0.0"


def test_a_rejected_update_leaves_the_old_payload_in_place(tmp_path):
    ext = _ext(tmp_path)
    bad = _zip(tmp_path, name="bad.zip", manifest_version=1)
    with pytest.raises(PackageError):
        manager.update_extension(ext["id"], zip_path=bad)
    row = store.get_extension(ext["id"])
    assert row is not None and row["version"] == "1.0.0"
    manifest = json.loads(Path(row["payloadPath"], "manifest.json").read_text())
    assert manifest["version"] == "1.0.0"


def test_rollback_without_a_previous_version_is_refused(tmp_path):
    ext = _ext(tmp_path)
    with pytest.raises(PackageError) as e:
        manager.rollback(ext["id"])
    assert e.value.code == "no_previous"


# ---------------------------------------------------------------------------
# bulk selection
# ---------------------------------------------------------------------------

def test_explicit_ids_are_used_verbatim(tmp_path):
    pids = _profiles(3)
    found, missing = manager.resolve_profiles(profile_ids=pids[:2])
    assert [p["id"] for p in found] == pids[:2]
    assert missing == []


def test_a_missing_id_is_reported_not_silently_dropped(tmp_path):
    pids = _profiles(1)
    found, missing = manager.resolve_profiles(profile_ids=[*pids, "profile_gone"])
    assert len(found) == 1
    assert missing == ["profile_gone"]


def test_select_all_matching_is_scoped_by_the_filter(tmp_path):
    create_profile({"name": "alpha-one", "proxyType": "none", "tags": ["x"]})
    create_profile({"name": "alpha-two", "proxyType": "none", "tags": ["y"]})
    create_profile({"name": "beta-one", "proxyType": "none", "tags": ["x"]})
    found, _ = manager.resolve_profiles(search="alpha", select_all_matching=True)
    assert sorted(p["name"] for p in found) == ["alpha-one", "alpha-two"]
    found, _ = manager.resolve_profiles(tags=["x"], select_all_matching=True)
    assert sorted(p["name"] for p in found) == ["alpha-one", "beta-one"]


def test_select_all_matching_is_resolved_once_not_re_evaluated(tmp_path):
    """A profile created while a bulk assignment runs must not be swept in."""
    ext = _ext(tmp_path)
    create_profile({"name": "zz-one", "proxyType": "none"})
    found, _ = manager.resolve_profiles(search="zz", select_all_matching=True)
    chosen = [p["id"] for p in found]
    create_profile({"name": "zz-two", "proxyType": "none"})   # after resolution
    manager.assign(ext["id"], chosen, capability=SUPPORTED)
    rows = {a["profileId"] for a in store.list_assignments(ext_uuid=ext["id"])}
    assert rows == set(chosen)
    assert len(rows) == 1


def test_the_running_filter_separates_running_from_stopped(tmp_path):
    """`isRunning` is status plus a live pid, which is the same field the
    console filters on -- so the filter is tested against the real one rather
    than against the in-memory launch state, which a stopped profile can also
    carry."""
    from multiprofile.storage.profiles import set_runtime

    pids = _profiles(2)
    set_runtime(pids[0], "running", pid=4242)
    running, _ = manager.resolve_profiles(running="running", select_all_matching=True)
    stopped, _ = manager.resolve_profiles(running="stopped", select_all_matching=True)
    # Scoped to the two profiles this test made: a fresh store can be seeded
    # with a demo profile, and asserting over the whole fleet would make the
    # test about the fixture rather than about the filter.
    assert [p["id"] for p in running] == [pids[0]]
    stopped_ids = [p["id"] for p in stopped]
    assert pids[1] in stopped_ids
    assert pids[0] not in stopped_ids


def test_a_running_profile_is_pending_restart_not_installed(tmp_path):
    """The flag that loads an unpacked extension is a command-line argument,
    so it cannot be retrofitted to a browser that is already running. The
    product says Pending restart rather than killing a live session."""
    from multiprofile.storage.profiles import set_runtime

    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    set_runtime(pid, "running", pid=4242)
    result = manager.assign(ext["id"], [pid], capability=SUPPORTED)
    assert result.ok
    assert result.results[0].state == store.STATE_PENDING_RESTART
    assert "next launch" in result.results[0].detail
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["state"] == store.STATE_PENDING_RESTART
    assert row["state"] != store.STATE_INSTALLED_VERIFIED


def test_unassigning_a_running_profile_says_the_browser_still_has_it(tmp_path):
    """Honesty about the live session: removing the row does not unload the
    extension from a browser that is already running it."""
    from multiprofile.storage.profiles import set_runtime

    ext = _ext(tmp_path)
    pid = _profiles(1)[0]
    manager.assign(ext["id"], [pid], capability=SUPPORTED)
    set_runtime(pid, "running", pid=4242)
    result = manager.unassign(ext["id"], pid)
    assert result.ok
    assert "until it restarts" in result.detail


# ---------------------------------------------------------------------------
# default for new profiles
# ---------------------------------------------------------------------------

def test_a_default_extension_reaches_profiles_created_afterwards(tmp_path):
    ext = _ext(tmp_path)
    store.update_extension(ext["id"], default_for_new=True)
    pid = create_profile({"name": "later", "proxyType": "none"})["id"]
    row = store.get_assignment(ext["id"], pid)
    assert row is not None, "a default-for-new extension was not assigned"


def test_marking_a_default_does_not_reach_backwards(tmp_path):
    """The flag is about future profiles. Reaching into existing ones would be
    an install nobody asked for."""
    ext = _ext(tmp_path)
    existing = _profiles(2)
    store.update_extension(ext["id"], default_for_new=True)
    for pid in existing:
        assert store.get_assignment(ext["id"], pid) is None
