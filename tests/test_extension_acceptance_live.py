"""Acceptance: the workflows an operator actually performs, against a browser.

The existing live suite proved an extension runs in the profiles it was
assigned to. These are the workflows around that, each of which has a way of
looking right while being wrong:

* assigning to a **running** profile, which cannot be done by a command-line
  flag, so it must say Pending restart and then actually work after a
  restart;
* disabling, removing and updating on a running profile, where the UI must
  not report the new state before the browser is in it;
* three profiles with their own ``chrome.storage.local`` and IndexedDB,
  surviving a restart of every browser;
* removing one assignment and proving the others still run, then
  reinstalling and recording whether the removed profile's data came back;
* two extensions at once, each with its own identity;
* bulk assignment over a mixture of stopped, running and missing profiles,
  with a retry limited to the failures and an unselected negative control.

Evidence everywhere is what the browser reports: a marker the extension's own
content script writes into a page, and values read out of extension storage
from inside the extension's own popup. Never a database row.
"""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.network

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "extensions"
FIXTURE_A = FIXTURES / "mp-test-ext"
FIXTURE_B = FIXTURES / "mp-test-ext-b"


def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


pytest.importorskip("websocket")
if not _chrome_available():
    pytest.skip("Chrome is not installed", allow_module_level=True)

from multiprofile.extensions.capability import detect as _detect  # noqa: E402

_CAP = _detect()
if not _CAP.unpacked:
    pytest.skip(f"this browser cannot load unpacked extensions: {_CAP.reason}",
                allow_module_level=True)

# Reuse the site + probe helpers from the first live suite rather than
# duplicating them; they are the measurement, and two copies would drift.
from tests.test_extension_live import (  # noqa: E402
    _evaluate,
    _extension_storage,
    _read_marker,
    _Site,
)

_MARKER_B = """
JSON.stringify({
  marker: !!document.getElementById('mp-test-ext-b-marker'),
  id: (document.getElementById('mp-test-ext-b-marker')||{dataset:{}}).dataset.extid || null,
  version: (document.getElementById('mp-test-ext-b-marker')||{dataset:{}}).dataset.version || null
})
"""


def _read_marker_b(port: int) -> dict:
    return _evaluate(port, _MARKER_B)


def _zip_of(source: Path, out: Path, version: str | None = None) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(source.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(source)
            data = item.read_bytes()
            if version and rel.name == "manifest.json":
                manifest = json.loads(data)
                manifest["version"] = version
                data = json.dumps(manifest).encode()
            zf.writestr(str(rel), data)
    return out


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """An isolated install with a real site, and launch/stop bookkeeping."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    from multiprofile.storage import db

    db.close()
    site = _Site()
    started: list[str] = []

    class Lab:
        url = site.url
        tmp = tmp_path

        def profile(self, name, **extra):
            from multiprofile.storage.profiles import create_profile

            return create_profile({"name": name, "proxyType": "none",
                                   "startUrl": site.url, **extra})["id"]

        def launch(self, pid):
            import multiprofile.browser.launcher as L
            from multiprofile.browser.runtime_state import get_runtime

            out = L.launch_profile(pid, url=site.url, direct=True)
            assert out.get("ok") is True, out
            if pid not in started:
                started.append(pid)
            return out, get_runtime(pid).cdp_port

        def stop(self, pid):
            import multiprofile.browser.launcher as L

            try:
                L.stop_profile(pid)
            except Exception:  # noqa: BLE001 - teardown
                pass

        def mark_running(self, pid, port):
            """Record what the product's own `isRunning` reads, which is
            status plus a live pid rather than the in-memory launch state."""
            import os

            from multiprofile.browser.runtime_state import get_runtime
            from multiprofile.storage.profiles import set_runtime

            rt = get_runtime(pid)
            set_runtime(pid, "running", pid=getattr(rt, "pid", None) or os.getpid())

        def zip_a(self, name="a.zip", version=None):
            return _zip_of(FIXTURE_A, tmp_path / name, version)

        def zip_b(self, name="b.zip", version=None):
            return _zip_of(FIXTURE_B, tmp_path / name, version)

    yield Lab()
    import multiprofile.browser.launcher as L

    for pid in started:
        try:
            L.stop_profile(pid)
        except Exception:  # noqa: BLE001 - teardown
            pass
    site.close()
    db.close()


# ===========================================================================
# 1. the running-profile workflow
# ===========================================================================

def test_assigning_to_a_running_profile_is_pending_then_works_after_restart(lab):
    """The headline workflow, end to end.

    An unpacked extension is loaded by a command-line flag, so it cannot be
    given to a browser that is already running. The product must say so, not
    kill the session, and actually deliver the extension on the next start.
    """
    from multiprofile.extensions import manager, store

    ext = manager.import_zip(lab.zip_a())
    pid = lab.profile("running-assign")

    # Running, and demonstrably without the extension.
    _out, port = lab.launch(pid)
    assert _read_marker(port).get("marker") is False, "started with it already"
    lab.mark_running(pid, port)

    result = manager.assign(ext["id"], [pid])
    assert result.ok
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["state"] == store.STATE_PENDING_RESTART, row
    # The UI must not claim it is installed while the browser does not have it.
    assert row["state"] != store.STATE_INSTALLED_VERIFIED
    assert row["verifiedAt"] is None
    assert _read_marker(port).get("marker") is False, \
        "it appeared in a running browser, which the flag cannot do"

    # The controlled restart.
    lab.stop(pid)
    time.sleep(1.0)
    _out, port = lab.launch(pid)
    marker = _read_marker(port)
    assert marker.get("marker") is True, (marker, "not delivered after restart")
    assert marker.get("id") == ext["extensionId"]
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["state"] == store.STATE_INSTALLED_VERIFIED, row
    assert row["observedExtensionId"] == ext["extensionId"]


def test_profile_data_survives_the_restart_that_applies_an_extension(lab):
    """A restart to apply an extension must not cost the operator their
    profile. Browser profile data lives in the user-data directory, which the
    restart does not touch -- but open TABS are not restored, and that is a
    real limitation rather than a detail."""
    from multiprofile.extensions import manager

    ext = manager.import_zip(lab.zip_a())
    pid = lab.profile("keeps-data")
    _out, port = lab.launch(pid)

    # Write something into the PAGE's own origin storage, which belongs to the
    # profile rather than to any extension.
    wrote = _evaluate(port, (
        "(() => { localStorage.setItem('mp-acceptance', 'kept');"
        " return JSON.stringify({set: localStorage.getItem('mp-acceptance')}); })()"
    ))
    assert wrote.get("set") == "kept", wrote
    lab.mark_running(pid, port)

    manager.assign(ext["id"], [pid])
    lab.stop(pid)
    time.sleep(1.0)
    _out, port = lab.launch(pid)

    after = _evaluate(port, (
        "JSON.stringify({kept: localStorage.getItem('mp-acceptance')})"))
    assert after.get("kept") == "kept", (after, "profile data lost on restart")
    assert _read_marker(port).get("marker") is True


def test_disabling_takes_effect_on_the_next_launch_not_immediately(lab):
    from multiprofile.extensions import manager, store

    ext = manager.import_zip(lab.zip_a())
    pid = lab.profile("disable-flow")
    manager.assign(ext["id"], [pid])
    _out, port = lab.launch(pid)
    assert _read_marker(port).get("marker") is True
    lab.mark_running(pid, port)

    store.set_enabled(ext["id"], pid, False)
    # Still running in the live browser: the flag was already passed.
    assert _read_marker(port).get("marker") is True, \
        "disabling appeared to unload it from a running browser"
    assert store.get_assignment(ext["id"], pid) is not None, \
        "disabling deleted the assignment"

    lab.stop(pid)
    time.sleep(1.0)
    _out, port = lab.launch(pid)
    assert _read_marker(port).get("marker") is False, "still loaded after restart"


def test_removing_from_a_running_profile_says_so_and_takes_effect_on_restart(lab):
    from multiprofile.extensions import manager, store

    ext = manager.import_zip(lab.zip_a())
    pid = lab.profile("remove-flow")
    manager.assign(ext["id"], [pid])
    _out, port = lab.launch(pid)
    assert _read_marker(port).get("marker") is True
    lab.mark_running(pid, port)

    result = manager.unassign(ext["id"], pid)
    assert result.ok
    assert "until it restarts" in result.detail, result.detail
    assert _read_marker(port).get("marker") is True, \
        "it vanished from a running browser, which removal cannot do"
    assert store.get_assignment(ext["id"], pid) is None

    lab.stop(pid)
    time.sleep(1.0)
    _out, port = lab.launch(pid)
    assert _read_marker(port).get("marker") is False


def test_updating_a_running_profile_keeps_the_old_version_until_restart(lab):
    from multiprofile.extensions import manager

    ext = manager.import_zip(lab.zip_a())
    pid = lab.profile("update-flow")
    manager.assign(ext["id"], [pid])
    _out, port = lab.launch(pid)
    assert _read_marker(port).get("version") == "1.0.0"
    lab.mark_running(pid, port)

    manager.update_extension(ext["id"], zip_path=lab.zip_a("a2.zip", "4.0.0"))
    assert _read_marker(port).get("version") == "1.0.0", \
        "a running browser reported the new version without restarting"

    lab.stop(pid)
    time.sleep(1.0)
    _out, port = lab.launch(pid)
    after = _read_marker(port)
    assert after.get("version") == "4.0.0", after
    assert after.get("id") == ext["extensionId"], "identity changed on update"


# ===========================================================================
# 2. isolation and persistence across three profiles
# ===========================================================================

_IDB_WRITE = """
(async () => {
  const put = (v) => new Promise((res, rej) => {
    const open = indexedDB.open('mp-acceptance', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('kv');
    open.onsuccess = () => {
      const tx = open.result.transaction('kv', 'readwrite');
      tx.objectStore('kv').put(v, 'tag');
      tx.oncomplete = () => res(true);
      tx.onerror = () => rej(tx.error);
    };
    open.onerror = () => rej(open.error);
  });
  await put('__VALUE__');
  return JSON.stringify({wrote: '__VALUE__'});
})()
"""

_IDB_READ = """
(async () => {
  const get = () => new Promise((res) => {
    const open = indexedDB.open('mp-acceptance', 1);
    open.onupgradeneeded = () => open.result.createObjectStore('kv');
    open.onsuccess = () => {
      const tx = open.result.transaction('kv', 'readonly');
      const r = tx.objectStore('kv').get('tag');
      r.onsuccess = () => res(r.result ?? null);
      r.onerror = () => res(null);
    };
    open.onerror = () => res(null);
  });
  return JSON.stringify({tag: await get()});
})()
"""


def test_three_profiles_keep_their_own_extension_and_page_storage(lab):
    """Same extension files, same extension ID, three separate stores --
    checked in chrome.storage.local AND IndexedDB, and after a restart of
    every browser."""
    from multiprofile.extensions import manager

    ext = manager.import_zip(lab.zip_a())
    pids = [lab.profile(f"iso3-{i}") for i in range(3)]
    result = manager.assign(ext["id"], pids)
    assert result.ok, result.to_dict()

    expected = {}
    for i, pid in enumerate(pids):
        _out, port = lab.launch(pid)
        assert _read_marker(port).get("marker") is True, pid
        value = f"profile-{i}-value"
        got = _extension_storage(port, ext["extensionId"], write={"tag": value})
        assert got.get("tag") == value, (pid, got)
        idb = _evaluate(port, _IDB_WRITE.replace("__VALUE__", value),
                        await_promise=True)
        assert idb.get("wrote") == value, (pid, idb)
        expected[pid] = value
        lab.stop(pid)
        time.sleep(0.5)

    assert len(set(expected.values())) == 3, "the test wrote the same value"

    # Every browser restarted; each must see only its own.
    for pid in pids:
        _out, port = lab.launch(pid)
        store_got = _extension_storage(port, ext["extensionId"])
        idb_got = _evaluate(port, _IDB_READ, await_promise=True)
        assert store_got.get("tag") == expected[pid], (pid, store_got, expected)
        assert idb_got.get("tag") == expected[pid], (pid, idb_got, expected)
        others = {v for k, v in expected.items() if k != pid}
        assert store_got.get("tag") not in others
        lab.stop(pid)
        time.sleep(0.3)


def test_removing_one_profile_leaves_the_others_working_and_records_its_data(lab):
    """Then reinstall, and record what happened to the removed profile's
    extension data -- which is a fact to document rather than a guarantee."""
    from multiprofile.extensions import manager

    ext = manager.import_zip(lab.zip_a())
    keep, drop = lab.profile("keeps"), lab.profile("drops")
    manager.assign(ext["id"], [keep, drop])

    for pid, value in ((keep, "keep-value"), (drop, "drop-value")):
        _out, port = lab.launch(pid)
        assert _read_marker(port).get("marker") is True
        got = _extension_storage(port, ext["extensionId"], write={"tag": value})
        assert got.get("tag") == value
        lab.stop(pid)
        time.sleep(0.4)

    manager.unassign(ext["id"], drop)

    # The other profile is untouched and still has its own value.
    _out, port = lab.launch(keep)
    assert _read_marker(port).get("marker") is True, \
        "removing one assignment broke another profile"
    assert _extension_storage(port, ext["extensionId"]).get("tag") == "keep-value"
    assert Path(ext["payloadPath"], "manifest.json").is_file(), \
        "the shared payload was deleted while another profile used it"
    lab.stop(keep)
    time.sleep(0.4)

    # The removed profile no longer runs it.
    _out, port = lab.launch(drop)
    assert _read_marker(port).get("marker") is False
    lab.stop(drop)
    time.sleep(0.4)

    # Reinstall, and observe whether its data came back.
    manager.assign(ext["id"], [drop])
    _out, port = lab.launch(drop)
    assert _read_marker(port).get("marker") is True, "reinstallation failed"
    after = _extension_storage(port, ext["extensionId"])
    retained = after.get("tag") == "drop-value"
    # Either answer is acceptable; an undocumented answer is not. The
    # assertion records which one this product actually produces.
    assert retained is True, (
        "reinstalling did NOT restore the removed profile's extension data "
        f"(read {after!r}). If this is now the behaviour, docs/11_EXTENSIONS.md "
        "says the opposite and must be corrected."
    )


def test_default_for_new_profiles_does_not_reach_existing_ones(lab):
    from multiprofile.extensions import manager, store

    ext = manager.import_zip(lab.zip_a())
    existing = lab.profile("pre-existing")
    store.update_extension(ext["id"], default_for_new=True)
    assert store.get_assignment(ext["id"], existing) is None, \
        "marking a default reached backwards into an existing profile"

    fresh = lab.profile("created-after")
    assert store.get_assignment(ext["id"], fresh) is not None

    _out, port = lab.launch(fresh)
    assert _read_marker(port).get("marker") is True
    lab.stop(fresh)
    time.sleep(0.4)
    _out, port = lab.launch(existing)
    assert _read_marker(port).get("marker") is False, \
        "an existing profile was retroactively given the default extension"


# ===========================================================================
# 3. two extensions, and bulk partial failure
# ===========================================================================

def test_two_extensions_run_together_with_distinct_identities(lab):
    from multiprofile.extensions import manager

    a = manager.import_zip(lab.zip_a())
    b = manager.import_zip(lab.zip_b())
    assert a["extensionId"] != b["extensionId"], "two payloads, one identity"
    pid = lab.profile("two-ext")
    manager.assign(a["id"], [pid])
    manager.assign(b["id"], [pid])

    out, port = lab.launch(pid)
    flags = [x for x in (out.get("cmd") or []) if x.startswith("--load-extension=")]
    assert len(flags) == 1, flags
    assert a["payloadPath"] in flags[0] and b["payloadPath"] in flags[0]

    ma, mb = _read_marker(port), _read_marker_b(port)
    assert ma.get("marker") is True, ma
    assert mb.get("marker") is True, mb
    assert ma.get("id") == a["extensionId"]
    assert mb.get("id") == b["extensionId"]
    assert ma.get("id") != mb.get("id")


def test_two_extensions_keep_separate_storage_in_each_profile(lab):
    from multiprofile.extensions import manager

    a = manager.import_zip(lab.zip_a())
    b = manager.import_zip(lab.zip_b())
    pids = [lab.profile(f"two-iso-{i}") for i in range(2)]
    manager.assign(a["id"], pids)
    manager.assign(b["id"], pids)

    for i, pid in enumerate(pids):
        _out, port = lab.launch(pid)
        assert _read_marker(port).get("marker") is True
        assert _read_marker_b(port).get("marker") is True
        got_a = _extension_storage(port, a["extensionId"],
                                   write={"tag": f"A-{i}"})
        got_b = _extension_storage(port, b["extensionId"],
                                   write={"tag": f"B-{i}"})
        assert got_a.get("tag") == f"A-{i}"
        assert got_b.get("tag") == f"B-{i}"
        lab.stop(pid)
        time.sleep(0.4)

    for i, pid in enumerate(pids):
        _out, port = lab.launch(pid)
        assert _extension_storage(port, a["extensionId"]).get("tag") == f"A-{i}"
        assert _extension_storage(port, b["extensionId"]).get("tag") == f"B-{i}"
        lab.stop(pid)
        time.sleep(0.3)


def test_bulk_assignment_over_a_mixed_fleet_reports_each_profile(lab):
    """Stopped, running, and missing in one call, with an unselected control."""
    from multiprofile.extensions import manager, store

    ext = manager.import_zip(lab.zip_a())
    stopped = lab.profile("bulk-stopped")
    running = lab.profile("bulk-running")
    control = lab.profile("bulk-control")
    _out, running_port = lab.launch(running)
    lab.mark_running(running, running_port)

    result = manager.assign(ext["id"], [stopped, running, "profile_missing"])
    body = result.to_dict()
    assert body["total"] == 3
    assert body["failed"] == 1
    assert body["failedProfileIds"] == ["profile_missing"]
    assert result.ok is False, "a batch with a failure is not a success"
    states = {r.profile_id: r.state for r in result.results}
    assert states[stopped] == store.STATE_ASSIGNED
    assert states[running] == store.STATE_PENDING_RESTART
    assert store.get_assignment(ext["id"], control) is None, "control was touched"

    # Retry limited to the failures leaves the successes alone.
    failed = [a["profileId"] for a in store.list_assignments(ext_uuid=ext["id"])
              if a["state"] == store.STATE_FAILED]
    assert failed == [], "a nonexistent profile should not leave a failed row"

    # The stopped one delivers on launch; the control still does not.
    lab.stop(running)
    time.sleep(0.6)
    _out, port = lab.launch(stopped)
    assert _read_marker(port).get("marker") is True
    lab.stop(stopped)
    time.sleep(0.4)
    _out, port = lab.launch(control)
    assert _read_marker(port).get("marker") is False, "the control ran it"


def test_a_failing_profile_is_retryable_and_the_batch_is_not_called_ok(lab):
    """A payload that has gone missing fails every profile; restoring it and
    retrying only the failures recovers them."""
    import shutil

    from multiprofile.extensions import manager, store

    ext = manager.import_zip(lab.zip_a())
    pids = [lab.profile(f"retry-{i}") for i in range(2)]
    backup = lab.tmp / "payload-backup"
    shutil.copytree(ext["payloadPath"], backup)
    shutil.rmtree(ext["payloadPath"])

    result = manager.assign(ext["id"], pids)
    assert result.ok is False
    assert result.to_dict()["failed"] == 2
    assert all(r.state == store.STATE_FAILED for r in result.results)

    shutil.copytree(backup, ext["payloadPath"])
    failed = [a["profileId"] for a in store.list_assignments(ext_uuid=ext["id"])
              if a["state"] == store.STATE_FAILED]
    assert sorted(failed) == sorted(pids)
    retry = manager.assign(ext["id"], failed)
    assert retry.ok is True, retry.to_dict()

    for pid in pids:
        _out, port = lab.launch(pid)
        assert _read_marker(port).get("marker") is True, pid
        lab.stop(pid)
        time.sleep(0.4)
