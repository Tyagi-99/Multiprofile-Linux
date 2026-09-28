"""A profile's timezone must be the engine's, not a set of patched methods.

Before this, the profile's timezone existed only as JavaScript.
``evasion.js`` hooks ``Intl.DateTimeFormat``, ``resolvedOptions``,
``Date.prototype.getTimezoneOffset``, ``toString`` and ``toTimeString`` -- and
nothing else. Every surface it did not think of reported the host's zone.

Measured on this machine (host ``Asia/Kolkata``, profile ``America/New_York``),
inside a single document::

    Date.prototype.toString        04:58:55 GMT-0400 (Eastern Daylight Time)
    Date.prototype.toLocaleString  9/28/2026, 2:28:55 PM

Nine and a half hours apart -- exactly Kolkata (+5:30) against New York
(-4:00). A page needed one unhooked method to catch the profile contradicting
itself, and ``toLocaleString`` is not an obscure one.

Patching methods one at a time cannot win that game; the list of surfaces that
expose local time is open-ended. So the timezone is now set on the engine:
``Emulation.setTimezoneOverride`` per attached target, plus ``TZ`` in the
browser process's environment for the process itself. Every surface then agrees
by construction, including the ones nobody has thought of yet, and the JS hooks
stay as a fallback for when the override cannot be applied.

These tests assert agreement between surfaces rather than exact strings,
because the point is not what any one of them says -- it is that they cannot
disagree.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.network

TARGET_TZ = "America/New_York"


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


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>tz</title>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/tz"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


_PROBE = r"""
(async () => {
  const out = {};
  out.intl       = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.offset     = new Date().getTimezoneOffset();
  out.toString   = new Date().toString();
  out.toLocale   = new Date().toLocaleString('en-US');
  out.toLocaleT  = new Date().toLocaleTimeString('en-US', {hour12: false});
  out.toTimeStr  = new Date().toTimeString();
  try {
    const src = `self.postMessage({
      intl: Intl.DateTimeFormat().resolvedOptions().timeZone,
      offset: new Date().getTimezoneOffset(),
      toLocale: new Date().toLocaleString('en-US')
    });`;
    const w = new Worker(URL.createObjectURL(new Blob([src], {type:'text/javascript'})));
    out.worker = await new Promise(r => { w.onmessage = e => r(e.data); setTimeout(()=>r(null), 4000); });
  } catch (e) { out.worker = null; }
  return JSON.stringify(out);
})()
"""


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    """Launch a profile pinned to TARGET_TZ and evaluate the probe in it."""
    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    import websocket

    page = _Page()
    pid = create_profile({
        "name": "tz", "proxyType": "none", "startUrl": page.url, "timezone": TARGET_TZ,
    })["id"]
    res = L.launch_profile(pid, url=page.url, direct=True)
    assert res.get("ok") is True, res.get("error")
    port = get_runtime(pid).cdp_port
    time.sleep(2.0)

    def _cdp(path):
        r = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", headers={"Host": f"localhost:{port}"}
        )
        return json.loads(urllib.request.urlopen(r, timeout=5).read())

    try:
        target = next(
            t for t in _cdp("/json")
            if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
        )
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "awaitPromise": True, "returnByValue": True},
        }))
        deadline = time.time() + 25
        value = None
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        yield json.loads(value)
    finally:
        L.stop_profile(pid)
        page.close()


def _hour_from_locale(s: str) -> int:
    """Hour out of '9/28/2026, 5:00:34 AM' or a 24h time string."""
    time_part = s.split(",")[-1].strip()
    hour = int(time_part.split(":")[0])
    if "PM" in time_part.upper() and hour != 12:
        hour += 12
    if "AM" in time_part.upper() and hour == 12:
        hour = 0
    return hour


def test_the_reported_zone_is_the_profiles(probe):
    assert probe["intl"] == TARGET_TZ


def test_to_locale_string_agrees_with_to_string(probe):
    """The measurement in the module docstring, as an assertion.

    These two were 9.5 hours apart -- one patched, one not.
    """
    # "... 05:00:34 GMT-0400 (Eastern Daylight Time)"
    to_string_hour = int(probe["toString"].split(" ")[4].split(":")[0])
    assert _hour_from_locale(probe["toLocale"]) == to_string_hour, probe


def test_every_local_time_surface_agrees(probe):
    """toLocaleTimeString and toTimeString are two more unhooked routes."""
    to_string_hour = int(probe["toString"].split(" ")[4].split(":")[0])
    assert int(probe["toLocaleT"].split(":")[0]) == to_string_hour, probe
    assert int(probe["toTimeStr"].split(":")[0]) == to_string_hour, probe


def test_the_offset_matches_the_zone(probe):
    """New York is UTC-4 or UTC-5; Kolkata would be -330."""
    assert probe["offset"] in (240, 300), probe["offset"]


def test_a_worker_reports_the_same_zone(probe):
    """A worker is its own realm; a per-realm patch is easy to miss there."""
    worker = probe.get("worker")
    assert worker, "the worker never answered"
    assert worker["intl"] == TARGET_TZ
    assert worker["offset"] == probe["offset"]


def test_the_worker_and_the_page_agree_on_the_wall_clock(probe):
    worker = probe.get("worker")
    assert worker
    assert _hour_from_locale(worker["toLocale"]) == _hour_from_locale(probe["toLocale"])


def test_the_launch_reports_the_timezone_as_applied(monkeypatch, tmp_path):
    """The console must be able to say whether the engine took it."""
    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    pid = create_profile({
        "name": "tzflag", "proxyType": "none", "startUrl": "about:blank",
        "timezone": TARGET_TZ,
    })["id"]
    try:
        assert L.launch_profile(pid, url="about:blank", direct=True).get("ok") is True
        time.sleep(1.0)
        detail = (get_runtime(pid).inject_detail or {})
        assert detail.get("timezoneApplied") is True, detail
    finally:
        L.stop_profile(pid)
