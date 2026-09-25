"""Profile A must not be able to read profile B's browser storage.

The existing isolation tests check that the *paths* differ. That is not the
same claim. This launches two real Chrome profiles through the real
launcher, writes to every storage surface a site can persist to in profile
A, and then reads each one back from profile B on the same origin.

Surfaces covered: cookies, localStorage, sessionStorage, IndexedDB and the
Cache Storage API. Same origin in both profiles, so a leak would show up as
profile B reading profile A's value -- there is nothing else that could
produce it.

Skipped when Chrome is not installed, so the suite still runs on a machine
without a browser.
"""
from __future__ import annotations

import json
import socketserver
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest

pytestmark = pytest.mark.network


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


class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body>isolation probe</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _TS(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture(scope="module")
def origin():
    srv = _TS(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    return tmp_path


class _Cdp:
    """Minimal evaluator against one profile's first page target."""

    def __init__(self, port: int, origin: str):
        import websocket

        targets = json.loads(
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5).read()
        )
        page = next(t for t in targets if t.get("type") == "page" and origin[:21] in (t.get("url") or ""))
        # suppress_origin: the DevTools origin check is on by design.
        self.ws = websocket.create_connection(
            page["webSocketDebuggerUrl"], timeout=15, suppress_origin=True
        )
        self._id = 0

    def eval(self, expression: str, timeout: float = 15.0):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({
            "id": mid, "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
        }))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise AssertionError(msg["error"])
                return (msg.get("result", {}).get("result", {}) or {}).get("value")
        raise AssertionError(f"no CDP reply for: {expression[:60]}")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# Writes one marker into every storage surface a site can persist to.
_WRITE = """
(async () => {
  const v = "%s";
  document.cookie = "iso=" + v + "; path=/";
  localStorage.setItem("iso", v);
  sessionStorage.setItem("iso", v);
  await new Promise((res, rej) => {
    const r = indexedDB.open("isodb", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("s");
    r.onsuccess = () => {
      const tx = r.result.transaction("s", "readwrite");
      tx.objectStore("s").put(v, "iso");
      tx.oncomplete = () => res();
      tx.onerror = () => rej(tx.error);
    };
    r.onerror = () => rej(r.error);
  });
  const c = await caches.open("isocache");
  await c.put("/iso", new Response(v));
  return "written";
})()
"""

_READ = """
(async () => {
  const out = {};
  out.cookie = document.cookie || "";
  out.local = localStorage.getItem("iso");
  out.session = sessionStorage.getItem("iso");
  out.idb = await new Promise((res) => {
    const r = indexedDB.open("isodb", 1);
    r.onupgradeneeded = () => { try { r.result.createObjectStore("s"); } catch (e) {} };
    r.onsuccess = () => {
      let g;
      try { g = r.result.transaction("s", "readonly").objectStore("s").get("iso"); }
      catch (e) { return res(null); }
      g.onsuccess = () => res(g.result ?? null);
      g.onerror = () => res(null);
    };
    r.onerror = () => res(null);
  });
  const c = await caches.open("isocache");
  const hit = await c.match("/iso");
  out.cache = hit ? await hit.text() : null;
  return JSON.stringify(out);
})()
"""


def test_profile_b_cannot_read_profile_a_storage(xdg, origin):
    """The whole product rests on this. Measured, not assumed."""
    from multiprofile.browser.launcher import launch_profile, stop_profile
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    marker = "SECRET-FROM-PROFILE-A"
    a = create_profile({"name": "Iso A", "startUrl": origin, "proxyType": "none"})
    b = create_profile({"name": "Iso B", "startUrl": origin, "proxyType": "none"})

    try:
        assert launch_profile(a["id"], url=origin, cdp=True).get("ok") is True
        time.sleep(4)
        ca = _Cdp(get_runtime(a["id"]).cdp_port, origin)
        assert ca.eval(_WRITE % marker) == "written"
        # Read it back in A, so a null in B means isolation and not a failed write.
        back = json.loads(ca.eval(_READ))
        ca.close()
        assert back["local"] == marker, f"profile A could not even read its own data: {back}"
        assert back["idb"] == marker
        assert back["cache"] == marker
        assert marker in back["cookie"]

        # Stop A before starting B. The claim under test is about data that
        # PERSISTED to A's profile directory, so A need not still be running
        # -- and two headless Chromes at once is what made this flaky on a
        # machine with a couple of GB free.
        stop_profile(a["id"])
        time.sleep(2)

        assert launch_profile(b["id"], url=origin, cdp=True).get("ok") is True
        time.sleep(4)
        cb = _Cdp(get_runtime(b["id"]).cdp_port, origin)
        seen = json.loads(cb.eval(_READ))
        cb.close()
    finally:
        stop_profile(a["id"])
        stop_profile(b["id"])

    leaked = {k: v for k, v in seen.items() if v and marker in str(v)}
    assert not leaked, f"profile B read profile A's storage: {leaked}"

    assert seen["local"] is None, "localStorage leaked"
    assert seen["session"] is None, "sessionStorage leaked"
    assert seen["idb"] is None, "IndexedDB leaked"
    assert seen["cache"] is None, "Cache Storage leaked"
    assert marker not in seen["cookie"], "cookies leaked"


def test_each_profile_gets_its_own_user_data_dir(xdg):
    """The mechanism behind the isolation above."""
    from multiprofile.storage.profiles import create_profile
    from multiprofile.storage.xdg import chrome_profile_dir

    a = create_profile({"name": "A"})
    b = create_profile({"name": "B"})

    da, db = chrome_profile_dir(a["id"]), chrome_profile_dir(b["id"])
    assert da != db
    assert not str(da).startswith(str(db))
    assert not str(db).startswith(str(da))
