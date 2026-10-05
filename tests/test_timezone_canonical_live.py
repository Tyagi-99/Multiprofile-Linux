"""A profile must report a timezone this engine can actually report.

Found by `scripts/identity_parity.py` on its first run, in a surface no
existing test covered. A profile stored as `Asia/Kolkata`:

    page            Intl...timeZone -> 'Asia/Kolkata'
    every worker    Intl...timeZone -> 'Asia/Calcutta'
    stock Chromium  Intl...timeZone -> 'Asia/Calcutta'   (every realm)

Two defects, and the second is worse than the first.

**The page contradicted its own workers.** Workers get the zone from
`Emulation.setTimezoneOverride` with no hook over it, so they report whatever
the engine resolves. The page had `resolvedOptions` hooked to return the
configured string verbatim.

**And the page's value was impossible.** Measured on Chromium 151::

    Intl.DateTimeFormat('en-US',{timeZone:'Asia/Kolkata'})
      .resolvedOptions().timeZone            -> 'Asia/Calcutta'
    Intl.supportedValuesOf('timeZone').includes('Asia/Kolkata')
                                             -> false

`Asia/Kolkata` is not in this engine's supported set at all, so no real
browser on this build can return it. That makes it **F44's shape** -- a value
outside the set of values that can exist -- which a site reads in one line
with nothing to compare against. Internal agreement would not have caught it;
only asking the engine what it supports does.

The fix asks the engine to spell the zone before the hooks use it, so the hook
can only ever agree with the engine. The tests below are therefore written
against aliased zones specifically, because a zone that is already canonical
(`America/New_York`) cannot fail either check -- which is exactly why the
existing worker-realm suite, which uses `America/New_York`, passed throughout.
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

#: Zones whose stored spelling is an alias the engine renames. Each one is a
#: real tzdata rename, and each is a plausible profile setting.
ALIASED_ZONES = (
    "Asia/Kolkata",        # -> Asia/Calcutta on this ICU
    "Asia/Ho_Chi_Minh",    # -> Asia/Saigon
    "Europe/Kyiv",         # -> Europe/Kiev
)

_FIELDS = (
    "tz:Intl.DateTimeFormat().resolvedOptions().timeZone,"
    "off:new Date().getTimezoneOffset(),"
    "supported:(typeof Intl.supportedValuesOf==='function'"
    "?Intl.supportedValuesOf('timeZone').includes("
    "Intl.DateTimeFormat().resolvedOptions().timeZone):null)"
)

_SW_JS = f"""self.addEventListener('message', e => {{
  e.source.postMessage(JSON.stringify({{{_FIELDS}}}));
}});
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
""".encode()

_PROBE = r"""
(async () => {
  const F = "__FIELDS__";
  const D = (pr, ms, l) => Promise.race([pr,
    new Promise(r => setTimeout(() => r('{"tz":"TIMEOUT:' + l + '"}'), ms))]);
  const o = {};
  o.page = JSON.stringify(eval("({" + F + "})"));
  o.worker = await D(new Promise(res => {
    const src = `self.onmessage=()=>postMessage(JSON.stringify({${F}}))`;
    const w = new Worker(URL.createObjectURL(
      new Blob([src], {type: 'text/javascript'})));
    w.onmessage = e => { res(e.data); w.terminate(); };
    w.onerror = e => res('{"tz":"ERR"}');
    w.postMessage(1);
  }), 8000, 'worker');
  o.service = await D((async () => {
    const reg = await navigator.serviceWorker.register('/sw.js', {scope: '/'});
    await navigator.serviceWorker.ready;
    return await new Promise(res => {
      navigator.serviceWorker.addEventListener('message', e => res(e.data));
      const sw = reg.active || navigator.serviceWorker.controller;
      if (!sw) { res('{"tz":"NO-ACTIVE"}'); return; }
      sw.postMessage(1);
    });
  })(), 20000, 'service');
  return JSON.stringify(o);
})()
""".replace("__FIELDS__", _FIELDS.replace('"', '\\"'))


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                if self.path.startswith("/sw.js"):
                    body, ctype = _SW_JS, "text/javascript"
                else:
                    body, ctype = b"<!doctype html><title>t</title>ok", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/t"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _measure(zone: str, tmp_path_factory) -> dict:
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("tz")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({"name": f"tz-{zone}", "proxyType": "none",
                          "startUrl": page.url, "timezone": zone,
                          "geoAuto": False})["id"]
    try:
        assert L.launch_profile(pid, url=page.url, direct=True).get("ok") is True
        time.sleep(2.0)
        port = get_runtime(pid).cdp_port
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        target = next(
            t for t in json.loads(urllib.request.urlopen(req, timeout=5).read())
            if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
        )
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=60, suppress_origin=True
        )
        try:
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": _PROBE, "awaitPromise": True,
                           "returnByValue": True},
            }))
            deadline = time.time() + 60
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get("id") != 1:
                    continue
                result = msg.get("result") or {}
                assert not result.get("exceptionDetails"), result["exceptionDetails"]
                raw = (result.get("result") or {}).get("value")
                assert raw, f"probe returned nothing: {result}"
                outer = json.loads(raw)
                return {k: json.loads(v) for k, v in outer.items()}
            raise AssertionError("probe timed out")
        finally:
            ws.close()
    finally:
        L.stop_profile(pid)
        page.close()
        mp.undo()


@pytest.fixture(scope="module")
def measurements(tmp_path_factory):
    return {zone: _measure(zone, tmp_path_factory) for zone in ALIASED_ZONES}


@pytest.mark.parametrize("zone", ALIASED_ZONES)
def test_the_reported_zone_is_one_the_engine_supports(measurements, zone):
    """The check that needs no comparison, and the one that would have caught
    this on its own: a zone outside `Intl.supportedValuesOf('timeZone')`
    cannot be produced by any real browser on this build."""
    page = measurements[zone]["page"]
    assert page["supported"] is not False, (
        f"profile stored {zone!r}; the page reports {page['tz']!r}, which is "
        f"not in Intl.supportedValuesOf('timeZone') on this engine"
    )


@pytest.mark.parametrize("zone", ALIASED_ZONES)
@pytest.mark.parametrize("realm", ("worker", "service"))
def test_every_realm_reports_the_same_zone_string(measurements, zone, realm):
    """Not just the same offset -- the same string. Both spellings of an
    aliased zone carry the same offset, which is why this looked like
    agreement in the original capture."""
    page = measurements[zone]["page"]
    got = measurements[zone][realm]
    assert not str(got["tz"]).startswith(("TIMEOUT", "ERR", "NO-ACTIVE")), got
    assert got["tz"] == page["tz"], (
        f"profile stored {zone!r}: page says {page['tz']!r}, "
        f"{realm} says {got['tz']!r}"
    )


@pytest.mark.parametrize("zone", ALIASED_ZONES)
def test_the_offset_follows_the_zone(measurements, zone):
    """Guards against agreement being reached by reporting the host zone
    everywhere: the offset must be the target's, not this machine's."""
    page = measurements[zone]["page"]
    expected = {
        "Asia/Kolkata": -330,
        "Asia/Ho_Chi_Minh": -420,
        "Europe/Kyiv": (-120, -180),
    }[zone]
    allowed = expected if isinstance(expected, tuple) else (expected,)
    assert page["off"] in allowed, (zone, page["off"], allowed)


def test_the_supported_values_check_is_actually_running(measurements):
    """Positive control. `Intl.supportedValuesOf` is only in newer engines;
    if it is missing the field is null and the first test above passes
    without measuring anything."""
    page = measurements[ALIASED_ZONES[0]]["page"]
    assert page["supported"] is True, (
        "Intl.supportedValuesOf is unavailable, so the impossible-value "
        f"check above is inert on this engine: {page}"
    )
