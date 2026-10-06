"""Does an assigned extension actually run, only where it was assigned, with
storage isolated per profile, and does any of it survive a restart?

The evidence throughout is the marker the extension's own **content script**
writes into a page, read per profile over CDP, plus values read back out of
``chrome.storage.local`` inside each profile. A saved file, a database row and
a screenshot of a settings page all prove the same nothing.

The fixture is ``tests/fixtures/extensions/mp-test-ext``: Manifest V3, a
popup, a content script scoped to the local test page, and a service worker
that answers messages about its own ``chrome.storage.local``.

Measured prerequisite, verified in ``test_the_browser_can_load_unpacked_at_all``
below rather than assumed: ``--load-extension`` was removed from branded
Google Chrome 137+, so these tests skip rather than fail on a browser that
cannot load an unpacked extension at all.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.network

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "extensions" / "mp-test-ext"


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
    pytest.skip(
        f"this browser cannot load unpacked extensions: {_CAP.reason}",
        allow_module_level=True,
    )


class _Site:
    """A local page the content script is allowed to touch."""

    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>ext</title><body>page"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/e"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _page_target(port: int, timeout: float = 25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"})
            targets = json.loads(urllib.request.urlopen(req, timeout=3).read())
            page = next((t for t in targets if t.get("type") == "page"
                         and "127.0.0.1" in (t.get("url") or "")), None)
            if page:
                return page
        except Exception:  # noqa: BLE001 - the browser may not be up yet
            pass
        time.sleep(0.4)
    return None


def _evaluate(port: int, expression: str, *, await_promise: bool = False,
              timeout: float = 30.0):
    import websocket

    page = _page_target(port, timeout=timeout)
    if page is None:
        return {"error": "no page target"}
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=timeout,
                                     suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": await_promise,
            "returnByValue": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                return json.loads(value) if value else {"error": "no value"}
    finally:
        ws.close()
    return {"error": "timeout"}


#: What the page can see about the extension without any extension API.
_MARKER = """
JSON.stringify({
  marker: !!document.getElementById('mp-test-ext-marker'),
  id: (document.getElementById('mp-test-ext-marker')||{dataset:{}}).dataset.extid || null,
  version: (document.getElementById('mp-test-ext-marker')||{dataset:{}}).dataset.version || null
})
"""


def _read_marker(port: int) -> dict:
    return _evaluate(port, _MARKER)


def _zip_fixture(tmp_path: Path, name: str = "mp-test-ext.zip",
                 version: str | None = None) -> Path:
    """Zip the fixture, optionally rewriting its version for update tests."""
    out = tmp_path / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(FIXTURE.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(FIXTURE)
            data = item.read_bytes()
            if version and rel.name == "manifest.json":
                manifest = json.loads(data)
                manifest["version"] = version
                data = json.dumps(manifest).encode()
            zf.writestr(str(rel), data)
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    from multiprofile.storage import db

    db.close()
    site = _Site()
    launched: list[str] = []

    class Env:
        url = site.url

        def launch(self, profile_id: str):
            import multiprofile.browser.launcher as L
            from multiprofile.browser.runtime_state import get_runtime

            out = L.launch_profile(profile_id, url=site.url, direct=True)
            assert out.get("ok") is True, out
            if profile_id not in launched:
                launched.append(profile_id)
            return out, get_runtime(profile_id).cdp_port

        def stop(self, profile_id: str):
            import multiprofile.browser.launcher as L

            try:
                L.stop_profile(profile_id)
            except Exception:  # noqa: BLE001 - teardown
                pass

    yield Env()
    import multiprofile.browser.launcher as L

    for pid in launched:
        try:
            L.stop_profile(pid)
        except Exception:  # noqa: BLE001 - teardown
            pass
    site.close()
    db.close()


# ---------------------------------------------------------------------------
# the prerequisite, measured
# ---------------------------------------------------------------------------

def test_the_browser_can_load_unpacked_at_all(env, tmp_path):
    """The capability claim, checked against the browser rather than trusted.

    Also the positive control for every test below: if this fails, agreement
    anywhere else would only mean "nothing loaded anywhere".
    """
    from multiprofile.extensions import manager
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    pid = create_profile({"name": "cap", "proxyType": "none",
                          "startUrl": env.url})["id"]
    manager.assign(ext["id"], [pid])
    _out, port = env.launch(pid)
    marker = _read_marker(port)
    assert marker.get("marker") is True, (marker, _CAP.to_dict())
    assert marker.get("id") == ext["extensionId"], marker
    assert marker.get("version") == "1.0.0", marker


# ---------------------------------------------------------------------------
# assignment reaches exactly the chosen profiles
# ---------------------------------------------------------------------------

def test_the_extension_runs_in_exactly_the_selected_profiles(env, tmp_path):
    """Three of four. The fourth is the control that makes the other three
    mean something."""
    from multiprofile.extensions import manager, store
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    pids = [
        create_profile({"name": f"sel{i}", "proxyType": "none",
                        "startUrl": env.url, "tags": ["live"]})["id"]
        for i in range(4)
    ]
    chosen = pids[:3]
    result = manager.assign(ext["id"], chosen)
    assert result.ok, result.to_dict()

    for pid in pids:
        out, port = env.launch(pid)
        marker = _read_marker(port)
        should_run = pid in chosen
        assert marker.get("marker") is should_run, (pid, should_run, marker)
        flags = [a for a in (out.get("cmd") or []) if a.startswith("--load-extension=")]
        assert bool(flags) is should_run, (pid, flags)
        if should_run:
            assert marker.get("id") == ext["extensionId"]
        env.stop(pid)

    # And the recorded state agrees with what the browser did.
    for pid in chosen:
        row = store.get_assignment(ext["id"], pid)
        assert row is not None
        assert row["state"] == store.STATE_INSTALLED_VERIFIED, row
        assert row["observedExtensionId"] == ext["extensionId"]
    assert store.get_assignment(ext["id"], pids[3]) is None


def test_the_unselected_profile_keeps_exclusivity(env, tmp_path):
    """A profile with no extension still gets --disable-extensions, so
    assigning to one profile does not quietly change another's posture."""
    from multiprofile.extensions import manager
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    a = create_profile({"name": "with", "proxyType": "none",
                        "startUrl": env.url})["id"]
    b = create_profile({"name": "without", "proxyType": "none",
                        "startUrl": env.url})["id"]
    manager.assign(ext["id"], [a])
    out_a, _ = env.launch(a)
    env.stop(a)
    out_b, _ = env.launch(b)
    assert "--disable-extensions" not in (out_a.get("cmd") or [])
    assert "--disable-extensions" in (out_b.get("cmd") or [])
    assert any("exclusivity is OFF" in n for n in (out_a.get("notes") or [])), \
        out_a.get("notes")


# ---------------------------------------------------------------------------
# per-profile isolation
# ---------------------------------------------------------------------------






def _extension_storage(port: int, ext_id: str, *, write: dict | None = None,
                       timeout: float = 40.0):
    """Read or write ``chrome.storage.local`` from inside the extension itself.

    Driven through the extension's own popup page over CDP, because that is a
    real extension context: a web page cannot touch ``chrome.storage``, and
    faking it from the page would test nothing.

    The readiness poll is not decoration. A freshly opened extension page
    answers ``Runtime.evaluate`` before Blink has bound the extension APIs
    into it, so the first evaluation raised "cannot read properties of
    undefined (reading 'local')" and the write silently did nothing while the
    read appeared to work -- which looked exactly like storage not persisting.
    """
    import websocket

    popup = f"chrome-extension://{ext_id}/popup.html"
    deadline = time.time() + timeout
    target = None
    while time.time() < deadline and target is None:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{popup}",
                headers={"Host": f"localhost:{port}"}, method="PUT")
            target = json.loads(urllib.request.urlopen(req, timeout=5).read())
        except Exception:  # noqa: BLE001 - retry while the browser settles
            time.sleep(0.4)
    if target is None:
        return {"error": "could not open the extension popup"}

    try:
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True)
    except Exception as e:  # noqa: BLE001 - reported, not raised
        return {"error": f"could not attach to the popup: {e}"}

    next_id = [0]

    def ev(expression: str, await_promise: bool = False):
        next_id[0] += 1
        mid = next_id[0]
        ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": await_promise,
            "returnByValue": True}}))
        stop = time.time() + timeout
        while time.time() < stop:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid:
                return msg
        return {"error": "timeout"}

    try:
        # Wait for the extension APIs to exist in this realm.
        ready = False
        stop = time.time() + timeout
        while time.time() < stop:
            msg = ev("typeof chrome !== 'undefined' && !!(chrome.storage "
                     "&& chrome.storage.local)")
            value = ((msg.get("result") or {}).get("result") or {}).get("value")
            if value is True:
                ready = True
                break
            time.sleep(0.3)
        if not ready:
            return {"error": "chrome.storage never became available in the popup"}

        if write is not None:
            msg = ev(
                "new Promise(r => chrome.storage.local.set(" + json.dumps(write)
                + ", () => chrome.storage.local.get(null,"
                " a => r(JSON.stringify(a)))))", await_promise=True)
        else:
            msg = ev("new Promise(r => chrome.storage.local.get(null,"
                     " a => r(JSON.stringify(a))))", await_promise=True)
        res = (msg.get("result") or {}).get("result") or {}
        if res.get("value"):
            return json.loads(res["value"])
        return {"error": json.dumps(msg)[:300]}
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001,S110 - teardown
            pass


def test_extension_storage_is_isolated_per_profile(env, tmp_path):
    """Same extension files, same extension ID, different storage.

    chrome.storage.local lives inside the browser profile's own user-data
    directory, so reusing the payload across profiles shares code and nothing
    else. This writes a different value in each of two profiles and reads both
    back.
    """
    from multiprofile.extensions import manager
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    pids = [
        create_profile({"name": f"iso{i}", "proxyType": "none",
                        "startUrl": env.url})["id"] for i in range(2)
    ]
    manager.assign(ext["id"], pids)

    written = {}
    for i, pid in enumerate(pids):
        _out, port = env.launch(pid)
        assert _read_marker(port).get("marker") is True
        value = f"profile-{i}-secret"
        got = _extension_storage(port, ext["extensionId"], write={"tag": value})
        assert got.get("tag") == value, got
        written[pid] = value
        env.stop(pid)

    # Read each back in its own profile. A shared store would show the last
    # value written in both.
    for pid in pids:
        _out, port = env.launch(pid)
        got = _extension_storage(port, ext["extensionId"])
        assert got.get("tag") == written[pid], (pid, got, written)
        env.stop(pid)

    assert len(set(written.values())) == 2, "the test wrote the same value twice"


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def test_the_assignment_and_the_extension_identity_survive_a_restart(env, tmp_path):
    """The payload path is stable, so the ID is stable, so the extension's own
    storage is still its own after a restart."""
    from multiprofile.extensions import manager, store
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    pid = create_profile({"name": "persist", "proxyType": "none",
                          "startUrl": env.url})["id"]
    manager.assign(ext["id"], [pid])

    _out, port = env.launch(pid)
    first = _read_marker(port)
    _extension_storage(port, ext["extensionId"], write={"tag": "before-restart"})
    env.stop(pid)
    time.sleep(1.0)

    _out, port = env.launch(pid)
    second = _read_marker(port)
    assert second.get("marker") is True
    assert second.get("id") == first.get("id"), "the extension ID changed"
    got = _extension_storage(port, ext["extensionId"])
    assert got.get("tag") == "before-restart", got

    row = store.get_assignment(ext["id"], pid)
    assert row is not None and row["state"] == store.STATE_INSTALLED_VERIFIED


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def test_an_update_changes_the_version_and_keeps_the_identity(env, tmp_path):
    from multiprofile.extensions import manager
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    pid = create_profile({"name": "upd", "proxyType": "none",
                          "startUrl": env.url})["id"]
    manager.assign(ext["id"], [pid])
    _out, port = env.launch(pid)
    before = _read_marker(port)
    assert before.get("version") == "1.0.0"
    env.stop(pid)

    updated = manager.update_extension(
        ext["id"], zip_path=_zip_fixture(tmp_path, "v2.zip", version="2.5.0"))
    assert updated["version"] == "2.5.0"

    _out, port = env.launch(pid)
    after = _read_marker(port)
    assert after.get("version") == "2.5.0", after
    assert after.get("id") == before.get("id"), "identity changed across update"


# ---------------------------------------------------------------------------
# removal
# ---------------------------------------------------------------------------

def test_removing_from_one_profile_leaves_the_other_working(env, tmp_path):
    from multiprofile.extensions import manager
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    keep = create_profile({"name": "keep", "proxyType": "none",
                           "startUrl": env.url})["id"]
    drop = create_profile({"name": "drop", "proxyType": "none",
                           "startUrl": env.url})["id"]
    manager.assign(ext["id"], [keep, drop])
    manager.unassign(ext["id"], drop)

    _out, port = env.launch(drop)
    assert _read_marker(port).get("marker") is False
    env.stop(drop)

    _out, port = env.launch(keep)
    assert _read_marker(port).get("marker") is True
    assert Path(ext["payloadPath"], "manifest.json").is_file(), \
        "the shared payload was deleted while another profile still used it"


def test_disabling_an_assignment_stops_it_loading_without_removing_it(env, tmp_path):
    from multiprofile.extensions import manager, store
    from multiprofile.storage.profiles import create_profile

    ext = manager.import_zip(_zip_fixture(tmp_path))
    pid = create_profile({"name": "dis", "proxyType": "none",
                          "startUrl": env.url})["id"]
    manager.assign(ext["id"], [pid])
    store.set_enabled(ext["id"], pid, False)
    _out, port = env.launch(pid)
    assert _read_marker(port).get("marker") is False
    assert store.get_assignment(ext["id"], pid) is not None, \
        "disabling must not delete the assignment"
