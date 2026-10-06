"""The canvas noise must be applied exactly once, at the right coordinates,
on every surface, in every realm.

The realm suite compared each worker's canvas hash against its page's. That
catches a disagreement but cannot say which side is wrong, and it is blind to
anything the two get wrong together. Three defects lived under it.

**The page applied the noise twice.** ``evasion.js`` guarded against stacking
by reading ``hasOwnProperty.call(toDataURL, 'toString')`` -- true of a hooked
function, false of a native one. The F3 native-masking fix then moved the
claimed source into a WeakMap *so that nothing is written onto the hooked
function*, which left the guard reading a property that no longer existed,
constant false, and dead. Meanwhile the injector sent
``addScriptToEvaluateOnNewDocument(runImmediately=True)`` **and**
``Page.reload`` to a page that was already open, and on some launches both
copies landed in one document::

    page     pixels nudged by +-2      <- two applications
    workers  pixels nudged by +-1

about one launch in six, which is why it read as flakiness.

**A worker noised the wrong pixels.** The page keys noise to canvas position;
the worker script always keyed it to (0,0). Reading from (0,0) -- which every
probe did -- hid it, and ``getImageData(5,7,20,20)`` showed it on 12 of 12
runs in all five worker types.

**A worker's PNG export carried no noise at all.** The page hooks
``OffscreenCanvas.convertToBlob``; the worker script did not. Three profiles
with different seeds::

    page     378539371 / 116989659 / 2291625482    (moves with the seed)
    worker   3394833805 / 3394833805 / 3394833805

One value for every profile on the machine -- the host's raw render, which is
a cross-profile identifier, contradicting the worker's own page.

So this suite does not compare realms against each other. It predicts the mask
in Python from the profile's seed and compares every realm against *that*,
which distinguishes "applied twice" from "wrong seed" from "not applied".
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

#: The probe canvas, and the uniform opaque grey it is filled with. Grey
#: rather than a text render on purpose: nothing in the mask then comes from
#: the engine's font stack, so a difference can only be the noise.
SIDE = 20
BASE = 128

#: Where the offset read is taken from, to prove the noise follows canvas
#: position rather than the read's own origin.
OFF_X, OFF_Y = 5, 7


# ---------------------------------------------------------------------------
# the prediction
# ---------------------------------------------------------------------------
def _imul(a: int, b: int) -> int:
    """``Math.imul``: a 32-bit multiply, result as a signed 32-bit int."""
    r = (a * b) & 0xFFFFFFFF
    return r - 0x100000000 if r >= 0x80000000 else r


def pixel_hash(seed: int, x: int, y: int) -> int:
    """``pixelHash`` from ``evasion.js`` and the worker script, bit for bit.

    Cross-checked against the JavaScript in a Node run: for seed 1 at (0,0)
    both give 654898832, and both select 34 of the 400 pixels for seed 12345.
    """
    h = (_imul(seed ^ 0x9E3779B9, 0x85EBCA6B)
         ^ _imul(x + 1, 0xC2B2AE35)
         ^ _imul(y + 1, 0x27D4EB2F)) & 0xFFFFFFFF
    h = _imul(h ^ (h >> 15), 0x2C1B3C6D) & 0xFFFFFFFF
    h = _imul(h ^ (h >> 12), 0x297A2D39) & 0xFFFFFFFF
    return (h ^ (h >> 15)) & 0xFFFFFFFF


def expected_mask(seed: int, ox: int = 0, oy: int = 0, passes: int = 1) -> str:
    """The mask ``passes`` applications leave on a uniform opaque canvas.

    ``pixelHash`` is a pure function of canvas position, so a second pass
    picks the same pixels, the same channel and the same direction. Against a
    base far from the 0/255 clamps that makes every nudge +-``passes``, which
    is what separates "applied twice" from "applied once with another seed".
    """
    out = []
    for y in range(SIDE):
        for x in range(SIDE):
            r = pixel_hash(seed, ox + x, oy + y)
            if (r & 15) != 0:
                continue
            d = passes if ((r >> 8) & 1) == 1 else -passes
            out.append(f"{y * SIDE + x}:{(r >> 4) % 3}:{'+' if d > 0 else ''}{d}")
    return ",".join(out)


# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------
_PROBE_FN = r"""
async () => {
  const S = __SIDE__, B = __BASE__, OX = __OFFX__, OY = __OFFY__;
  const hash = u8 => { let h = 0; for (let i = 0; i < u8.length; i++) h = (h * 31 + u8[i]) >>> 0; return h; };
  const maskOf = data => {
    const m = [];
    for (let i = 0; i < data.length; i += 4) {
      const p = i / 4;
      for (let k = 0; k < 3; k++) {
        const v = data[i + k] - B;
        if (v !== 0) m.push(p + ':' + k + ':' + (v > 0 ? '+' + v : v));
      }
      const a = data[i + 3] - 255;
      if (a !== 0) m.push(p + ':a:' + a);
    }
    return m.join(',');
  };
  const grey = (w, h) => {
    const c = new OffscreenCanvas(w, h), x = c.getContext('2d');
    x.fillStyle = 'rgb(' + B + ',' + B + ',' + B + ')';
    x.fillRect(0, 0, w, h);
    return x;
  };
  const out = {};
  try { out.mask = maskOf(grey(S, S).getImageData(0, 0, S, S).data); }
  catch (e) { out.mask = 'E:' + e; }
  try { out.maskOff = maskOf(grey(S + OX * 4, S + OY * 4).getImageData(OX, OY, S, S).data); }
  catch (e) { out.maskOff = 'E:' + e; }
  try {
    const c = new OffscreenCanvas(S, S), x = c.getContext('2d');
    x.fillStyle = '#f30'; x.fillRect(0, 0, S, S);
    x.fillStyle = '#08f'; x.font = '12px serif'; x.fillText('Ab', 1, 14);
    const b = await c.convertToBlob();
    out.blob = hash(new Uint8Array(await b.arrayBuffer()));
  } catch (e) { out.blob = 'E:' + e; }
  return out;
}
""".replace("__SIDE__", str(SIDE)).replace("__BASE__", str(BASE)) \
   .replace("__OFFX__", str(OFF_X)).replace("__OFFY__", str(OFF_Y))

_WORKER_JS = (
    "const probe = " + _PROBE_FN + ";"
    "self.onmessage = async () => postMessage(JSON.stringify(await probe()));"
).encode()

_SW_JS = (
    "const probe = " + _PROBE_FN + ";"
    "self.addEventListener('message', async e => "
    "e.source.postMessage(JSON.stringify(await probe())));"
    "self.addEventListener('install', () => self.skipWaiting());"
    "self.addEventListener('activate', e => e.waitUntil(clients.claim()));"
).encode()

_PAGE_PROBE = r"""
(async () => {
  const probe = __PROBE__;
  const D = (pr, ms, l) => Promise.race([pr,
    new Promise(r => setTimeout(() => r(l + ':TIMEOUT'), ms))]);
  const body = 'const probe = ' + probe.toString()
    + ';self.onmessage = async () => postMessage(JSON.stringify(await probe()));';
  const mkw = (opts, url) => new Promise(res => {
    const u = url || URL.createObjectURL(new Blob([body], {type: 'text/javascript'}));
    const w = new Worker(u, opts);
    w.onmessage = e => { res(e.data); w.terminate(); };
    w.onerror = e => res('ERR:' + (e.message || '?'));
    w.postMessage(1);
  });
  const o = {};
  o.top = JSON.stringify(await probe());
  o.worker_blob = await D(mkw(undefined, null), 10000, 'blob');
  o.worker_url = await D(mkw(undefined, '/w.js'), 10000, 'url');
  o.worker_module = await D(mkw({type: 'module'}, null), 10000, 'module');
  o.shared = await D(new Promise(res => {
    const src = 'const probe = ' + probe.toString()
      + ';self.onconnect = e => { const p = e.ports[0];'
      + 'p.onmessage = async () => p.postMessage(JSON.stringify(await probe()));'
      + 'p.start(); };';
    const w = new SharedWorker(URL.createObjectURL(
      new Blob([src], {type: 'text/javascript'})));
    w.port.onmessage = e => res(e.data);
    w.port.start(); w.port.postMessage(1);
  }), 10000, 'shared');
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
""".replace("__PROBE__", _PROBE_FN)

#: Every realm the noise has to reach. ``top`` is tested separately because it
#: is the one the others are compared against in the older suite.
WORKER_REALMS = ("worker_blob", "worker_url", "worker_module", "shared", "service")
ALL_REALMS = ("top", *WORKER_REALMS)


class _Site:
    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                if self.path.startswith("/w.js"):
                    body, ctype = _WORKER_JS, "text/javascript"
                elif self.path.startswith("/sw.js"):
                    body, ctype = _SW_JS, "text/javascript"
                else:
                    body, ctype = b"<!doctype html><title>n</title>ok", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/n"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture(scope="module")
def noise(tmp_path_factory):
    """Every realm's mask, plus the mask the profile's seed predicts."""
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.fingerprint._core import normalize_seed
    from multiprofile.storage.profiles import create_profile, get_profile

    tmp_path = tmp_path_factory.mktemp("noise")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    site = _Site()
    pid = create_profile({
        "name": "noise", "proxyType": "none", "startUrl": site.url,
    })["id"]
    seed = normalize_seed(get_profile(pid))
    try:
        assert L.launch_profile(pid, url=site.url, direct=True).get("ok") is True
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
            target["webSocketDebuggerUrl"], timeout=120, suppress_origin=True
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PAGE_PROBE, "awaitPromise": True,
                       "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 120
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        raw = json.loads(value)
        out = {"seed": seed}
        for realm in ALL_REALMS:
            body = raw[realm]
            assert not str(body).endswith("TIMEOUT"), f"{realm} never answered: {body}"
            assert not str(body).startswith("ERR"), f"{realm} errored: {body}"
            out[realm] = json.loads(body)
        return out
    finally:
        L.stop_profile(pid)
        site.close()
        mp.undo()


def _describe(seed: int, got: str, ox: int = 0, oy: int = 0) -> str:
    """Name what a mask actually is, so a failure says more than 'differs'."""
    if got == expected_mask(seed, ox, oy):
        return "one application"
    if not got:
        return "NOT APPLIED"
    for n in (2, 3, 4):
        if got == expected_mask(seed, ox, oy, passes=n):
            return f"APPLIED {n} TIMES"
    if got == expected_mask(seed, 0, 0) and (ox or oy):
        return "applied at (0,0) instead of the read's canvas position"
    if len(got.split(",")) == len(expected_mask(seed, ox, oy).split(",")):
        return "same number of nudges, different pixels (wrong seed?)"
    return f"{len(got.split(','))} nudges, expected " \
           f"{len(expected_mask(seed, ox, oy).split(','))}"


# ---------------------------------------------------------------------------
# exactly one application, everywhere
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("realm", ALL_REALMS)
def test_the_realm_applies_the_noise_exactly_once(noise, realm):
    seed = noise["seed"]
    got = noise[realm]["mask"]
    assert got == expected_mask(seed, 0, 0), (realm, _describe(seed, got))


@pytest.mark.parametrize("realm", ALL_REALMS)
def test_the_noise_follows_canvas_position_not_the_read_origin(noise, realm):
    """A read whose origin is not (0,0) has to be noised at that origin, or a
    page and a worker disagree about the same drawing."""
    seed = noise["seed"]
    got = noise[realm]["maskOff"]
    assert got == expected_mask(seed, OFF_X, OFF_Y), (
        realm, _describe(seed, got, OFF_X, OFF_Y))


@pytest.mark.parametrize("realm", WORKER_REALMS)
def test_the_realm_exports_the_same_png_as_its_page(noise, realm):
    """The export path, which the worker script did not hook at all."""
    assert isinstance(noise[realm]["blob"], int), (realm, noise[realm]["blob"])
    assert noise[realm]["blob"] == noise["top"]["blob"], (
        realm, noise[realm]["blob"], noise["top"]["blob"])


def test_the_measurement_is_not_vacuous(noise):
    """Positive control. An empty mask compared against an empty prediction
    would pass every test above."""
    seed = noise["seed"]
    assert seed > 1, seed
    assert expected_mask(seed, 0, 0), "the seed predicts no noise at all"
    assert noise["top"]["mask"], "nothing was noised in the page"
    assert isinstance(noise["top"]["blob"], int), noise["top"]["blob"]
    # and the offset prediction is genuinely a different mask, or the
    # position test is comparing a value against itself
    assert expected_mask(seed, OFF_X, OFF_Y) != expected_mask(seed, 0, 0)
