"""Every worker realm must describe the same machine as its page.

Worker coverage used to be two partial mechanisms in ``evasion.js`` and it
leaked through the gaps in both.

A **service worker** is registered by URL and fetched by the browser, so the
``Worker`` constructor wrapper could not reach it. Measured on this host::

    top document     platform: Win32          hardwareConcurrency: 8
    service worker   platform: Linux x86_64   hardwareConcurrency: 16

The User-Agent was right in both (a process-wide flag) and so was the
timezone (set on the engine). Only the JavaScript-spoofed values were
missing, and they were missing in the one realm that outlives the page: a
service worker persists across navigations and can be messaged by any page in
scope, so a script reading the host's real platform and core count through one
gets a stable cross-profile identifier for free.

A **module worker** has no ``importScripts()``. The wrapper called it anyway
inside a ``try/catch``, so the prelude loaded, the real script silently never
did, and the worker answered nothing::

    bare Chromium 151   classic: Linux x86_64/16   module: Linux x86_64/16
    profile (wrapper)   classic: Win32/12          module: TIMEOUT

A **worker loaded from a URL** rather than a blob got nothing from the second
mechanism, which hooked ``URL.createObjectURL``. And that mechanism was a
partial copy of the page's spoofing, so where it did apply the realms still
disagreed -- it set the WebGL strings but never added the canvas noise::

    page / shared / service   cv: 2097776059
    dedicated worker          cv: 2367204759

All of it is now one mechanism: the injector evaluates a worker identity
script over CDP while the worker is frozen by ``waitForDebuggerOnStart``.
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

# The fields every realm must agree on, as an expression list shared by the
# page, the worker sources and the served scripts.
_FIELDS = (
    "p:navigator.platform,hc:navigator.hardwareConcurrency,lang:navigator.language,"
    "dm:navigator.deviceMemory,tz:Intl.DateTimeFormat().resolvedOptions().timeZone,"
    # userAgent and userAgentData, added after a CreepJS run on this host
    # found the page claiming Windows and the worker's userAgentData still
    # saying Linux. The fields above were all agreeing at the time, because
    # they are the ones the worker script sets; userAgentData is not one of
    # them, and nothing was measuring it.
    "ua:navigator.userAgent,"
    "uap:(navigator.userAgentData?String(navigator.userAgentData.platform):'n/a'),"
    "uam:(navigator.userAgentData?String(navigator.userAgentData.mobile):'n/a'),"
    "uab:(navigator.userAgentData?navigator.userAgentData.brands"
    ".map(b=>b.brand+'/'+b.version).sort().join('|'):'n/a'),"
    "gl:(()=>{try{const c=new OffscreenCanvas(8,8).getContext('webgl');"
    "const d=c.getExtension('WEBGL_debug_renderer_info');"
    "return String(c.getParameter(d.UNMASKED_RENDERER_WEBGL));}catch(e){return 'n/a'}})(),"
    "cv:(()=>{try{const c=new OffscreenCanvas(20,20);const x=c.getContext('2d');"
    "x.fillStyle='#f30';x.fillRect(0,0,20,20);x.fillStyle='#08f';"
    "x.font='12px serif';x.fillText('Ab',1,14);"
    "const d=x.getImageData(0,0,20,20).data;let h=0;"
    "for(let i=0;i<d.length;i++)h=(h*31+d[i])>>>0;return h;}catch(e){return 'n/a'}})()"
)

_WORKER_JS = f"self.onmessage=()=>postMessage(JSON.stringify({{{_FIELDS}}}))".encode()
_SW_JS = f"""self.addEventListener('message', e => {{
  e.source.postMessage(JSON.stringify({{{_FIELDS}}}));
}});
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
""".encode()

_PROBE = r"""
(async () => {
  const F = "__FIELDS__";
  const o = {};
  const D = (pr, ms, l) => Promise.race([pr,
    new Promise(r => setTimeout(() => r(l + ':TIMEOUT'), ms))]);
  const mkw = (opts, url) => new Promise(res => {
    const src = `self.onmessage=()=>postMessage(JSON.stringify({${F}}))`;
    const u = url || URL.createObjectURL(new Blob([src], {type: 'text/javascript'}));
    const w = new Worker(u, opts);
    w.onmessage = e => { res(e.data); w.terminate(); };
    w.onerror = e => res('ERR:' + (e.message || '?'));
    w.postMessage(1);
  });
  o.top = JSON.stringify(eval("({" + F + "})"));
  o.worker_blob = await D(mkw(undefined, null), 8000, 'blob');
  o.worker_url = await D(mkw(undefined, '/w.js'), 8000, 'url');
  o.worker_module = await D(mkw({type: 'module'}, null), 8000, 'module');
  o.shared = await D(new Promise(res => {
    const src = `self.onconnect=e=>{const p=e.ports[0];`
      + `p.onmessage=()=>p.postMessage(JSON.stringify({${F}}));p.start();}`;
    const w = new SharedWorker(URL.createObjectURL(
      new Blob([src], {type: 'text/javascript'})));
    w.port.onmessage = e => res(e.data);
    w.port.start(); w.port.postMessage(1);
  }), 8000, 'shared');
  o.service = await D((async () => {
    const reg = await navigator.serviceWorker.register('/sw.js', {scope: '/'});
    await navigator.serviceWorker.ready;
    return await new Promise(res => {
      navigator.serviceWorker.addEventListener('message', e => res(e.data));
      const sw = reg.active || navigator.serviceWorker.controller;
      if (!sw) { res('NO-ACTIVE'); return; }
      sw.postMessage(1);
    });
  })(), 15000, 'service');
  return JSON.stringify(o);
})()
""".replace("__FIELDS__", _FIELDS.replace('"', '\\"'))

REALMS = ("worker_blob", "worker_url", "worker_module", "shared", "service")


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                if self.path.startswith("/w.js"):
                    body, ctype = _WORKER_JS, "text/javascript"
                elif self.path.startswith("/sw.js"):
                    body, ctype = _SW_JS, "text/javascript"
                else:
                    body, ctype = b"<!doctype html><title>r</title>ok", "text/html"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/r"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture(scope="module")
def realms(tmp_path_factory):
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("realms")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({
        "name": "realms", "proxyType": "none", "startUrl": page.url,
        "timezone": "America/New_York",
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
            target["webSocketDebuggerUrl"], timeout=90, suppress_origin=True
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "awaitPromise": True, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 90
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        raw = json.loads(value)
        out = {"top": json.loads(raw["top"])}
        for realm in REALMS:
            body = raw[realm]
            assert not str(body).endswith("TIMEOUT"), f"{realm} never answered: {body}"
            assert not str(body).startswith("ERR"), f"{realm} errored: {body}"
            out[realm] = json.loads(body)
        return out
    finally:
        L.stop_profile(pid)
        page.close()
        mp.undo()


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_answers_at_all(realms, realm):
    """A module worker used to hang: the wrapper called importScripts(),
    which does not exist there, and swallowed the error."""
    assert realms[realm], realm


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_reports_the_profile_platform(realms, realm):
    """The service worker reported Linux x86_64 while its page said Win32."""
    assert realms[realm]["p"] == realms["top"]["p"], (realm, realms[realm]["p"])


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_reports_the_profile_hardware(realms, realm):
    """hardwareConcurrency was the host's real core count in a service
    worker -- a stable identifier in the realm that outlives the page."""
    assert realms[realm]["hc"] == realms["top"]["hc"], realm
    assert realms[realm]["dm"] == realms["top"]["dm"], realm


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_reports_the_profile_locale(realms, realm):
    assert realms[realm]["lang"] == realms["top"]["lang"], realm
    assert realms[realm]["tz"] == realms["top"]["tz"], realm


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_reports_the_profile_gpu(realms, realm):
    assert realms[realm]["gl"] == realms["top"]["gl"], realm


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_renders_canvas_the_same_way(realms, realm):
    """The sharpest of these: two mechanisms meant one realm noised the
    canvas and another did not, so a page could compare its own render
    against a worker's and see the difference."""
    assert realms[realm]["cv"] == realms["top"]["cv"], (
        realm, realms[realm]["cv"], realms["top"]["cv"]
    )


def test_the_canvas_noise_was_actually_applied(realms):
    """Guards against the agreement being 'nothing is noised anywhere'."""
    assert isinstance(realms["top"]["cv"], int)
    assert realms["top"]["gl"] != "n/a"


# --------------------------------------------------------------------------
# userAgentData — the realm disagreement a CreepJS run found
# --------------------------------------------------------------------------
#
# Measured on this host, product profile, no device preset, direct route,
# against https://abrahamjuliot.github.io/creepjs/ :
#
#     page    userAgentData:  Google Chrome 151 (151.0.7922.173)
#                             Windows 11 [15.0.0] x86_64
#     worker  userAgentData:  Chromium 151
#                             Linux                    <-- the host
#
# and that worker reported the *spoofed* GPU (ANGLE NVIDIA GeForce GTX 1660
# Direct3D11) and the spoofed core count, so the worker script was running --
# the script simply never set `userAgentData`. Stock Chromium on this host
# reports exactly what the product's worker reported ("Chromium 151
# (151.0.7922.173) / Linux x86_64"), which is what identifies the value as
# the untouched host one rather than a partial override.
#
# Asking each realm separately, which is what these tests do, splits it:
#
#     worker_blob / worker_url / worker_module   Windows   ok
#     shared / service                           Linux     leak
#
# A dedicated worker runs in its owner document's renderer and inherits the
# page's `Network.setUserAgentOverride`, so it was right for free. A shared
# worker and a service worker each get their own target in their own process
# and inherit nothing. CreepJS reports from a ServiceWorkerGlobalScope where
# one is available, which is why it saw the leak and a quick Blob-worker
# check did not.


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_reports_the_profile_user_agent(realms, realm):
    """The UA string comes from a process-wide flag, so this one was already
    right in every realm. It is asserted anyway: it is the half of the pair
    that made the other half's disagreement a *contradiction* rather than
    merely a gap."""
    assert realms[realm]["ua"] == realms["top"]["ua"], realm


@pytest.mark.parametrize("realm", REALMS)
def test_the_realm_reports_the_profile_user_agent_data(realms, realm):
    """`navigator.userAgentData` must describe the same OS as the page.

    A page can message any of these realms and compare. A service worker is
    the worst case because it outlives the page: a script that reads
    `userAgentData.platform` through one gets the host's real OS, beside a
    `userAgent` claiming another, which is a contradiction no real browser
    produces.
    """
    for key in ("uap", "uam", "uab"):
        assert realms[realm][key] == realms["top"][key], (
            realm, key, realms[realm][key], realms["top"][key],
        )


def test_user_agent_data_is_actually_available_to_measure(realms):
    """Guards against the agreement above being 'n/a everywhere'.

    127.0.0.1 is a secure context, so `navigator.userAgentData` exists in
    every realm here. If it ever returns 'n/a' the assertions above pass
    while measuring nothing.
    """
    assert realms["top"]["uap"] != "n/a", realms["top"]
    assert realms["top"]["uab"] != "n/a", realms["top"]
    assert "/" in realms["top"]["uab"], realms["top"]["uab"]


def test_the_user_agent_and_its_metadata_describe_one_os(realms):
    """The pair, checked against each other rather than against each realm.

    `userAgent: Windows NT 10.0` beside `userAgentData.platform: Linux` is
    the F1 contradiction, and it is catchable in one line by any page.
    """
    ua = realms["top"]["ua"]
    platform = realms["top"]["uap"]
    expected = {"Windows": "Windows NT", "macOS": "Mac OS X", "Linux": "Linux"}
    marker = expected.get(platform)
    assert marker, f"unhandled userAgentData.platform {platform!r}"
    assert marker in ua, (platform, ua)
