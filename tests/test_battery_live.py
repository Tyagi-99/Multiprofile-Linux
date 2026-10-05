"""The Battery API must not report the host's battery.

``navigator.getBattery()`` was untouched, so it answered with this machine's
real state. Measured before the fix, every profile returned::

    charging=false  level=0.28  chargingTime=Infinity  dischargingTime=5545

Three things are wrong with that at once:

* It is the host's state, so it is identical across profiles -- and unlike a
  static fingerprint it **moves**, in lockstep. Two "different computers"
  whose batteries drain together are one computer, and a drifting shared
  value links them more strongly than a shared constant would.
* It contradicts the claimed machine. These profiles present as desktops
  with a discrete GPU, and no desktop discharges.
* ``dischargingTime`` is a live counter, so the value is close to unique at
  any instant.

A desktop without a battery reports exactly what the spec describes --
charging true, level 1, chargingTime 0, dischargingTime Infinity -- so for a
desktop profile the honest answer is also the constant one, and the moving
link disappears rather than being replaced by a different lie.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

_PROBE = r"""
(async () => {
  const o = {};
  const b = await navigator.getBattery();
  o.charging = b.charging;
  o.level = b.level;
  o.chargingTime = b.chargingTime;
  o.dischargingTime = String(b.dischargingTime);
  // The object must still be a real BatteryManager: a stand-in would fail
  // instanceof and lose the change events.
  o.is_manager = b instanceof BatteryManager;
  o.has_events = typeof b.addEventListener === 'function';
  try {
    const w = document.getElementById('f').contentWindow;
    const b2 = await w.navigator.getBattery();
    o.iframe = [b2.charging, b2.level, String(b2.dischargingTime)].join('|');
  } catch (e) { o.iframe = 'ERR:' + e.name; }
  return JSON.stringify(o);
})()
"""


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>b</title><iframe id=f srcdoc='x'></iframe>ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/b"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _probe(seed: int, tmp_path) -> dict:
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({
        "name": f"bat{seed}", "proxyType": "none", "startUrl": page.url, "seed": seed,
    })["id"]
    try:
        assert L.launch_profile(pid, url=page.url, direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        time.sleep(2.5)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        target = next(
            t for t in json.loads(urllib.request.urlopen(req, timeout=5).read())
            if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
        )
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=15, suppress_origin=True
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "awaitPromise": True, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 30
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        return json.loads(value)
    finally:
        L.stop_profile(pid)
        page.close()
        mp.undo()


@pytest.fixture(scope="module")
def two_profiles(tmp_path_factory):
    return {s: _probe(s, tmp_path_factory.mktemp(f"bat{s}")) for s in (111, 222)}


def test_a_desktop_profile_does_not_discharge(two_profiles):
    """The measurement in the module docstring, as an assertion."""
    for seed, p in two_profiles.items():
        assert p["charging"] is True, (seed, p)
        assert p["level"] == 1, (seed, p)


def test_no_live_counter_is_exposed(two_profiles):
    """dischargingTime was a running countdown -- close to unique at any
    instant, and moving in lockstep across every profile on the host."""
    for seed, p in two_profiles.items():
        assert p["dischargingTime"] == "Infinity", (seed, p)
        assert p["chargingTime"] == 0, (seed, p)


def test_the_host_battery_level_does_not_leak(two_profiles):
    """Whatever this machine's charge is, a desktop profile must not echo
    it. A shared fractional level is a link between profiles."""
    for seed, p in two_profiles.items():
        assert p["level"] not in (0.28, 0.27), (seed, p)


def test_it_is_still_a_real_battery_manager(two_profiles):
    """A stand-in object would fail instanceof and lose the change events."""
    for seed, p in two_profiles.items():
        assert p["is_manager"] is True, seed
        assert p["has_events"] is True, seed


def test_an_iframe_reports_the_same_battery(two_profiles):
    """A frame disagreeing with its own top document is the tell these
    spoofs exist to prevent."""
    for seed, p in two_profiles.items():
        assert p["iframe"] == "true|1|Infinity", (seed, p["iframe"])
