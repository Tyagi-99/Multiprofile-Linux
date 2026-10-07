"""A page and its own iframes must report one machine, on the *additive* spoofs.

Most spoofs here are substitutions -- a getter that returns a fixed platform,
a `getParameter` that returns a fixed renderer. Applying one of those twice
changes nothing. Three are not substitutions. Audio noise nudges every sample,
rect noise nudges every coordinate, and WebGL `readPixels` noise nudges every
pixel, and each copy of the script keeps its own record of what it has already
nudged. Apply one of those twice and the result moves.

Two code paths reach an iframe. The injector's new-document script runs in the
frame's own realm, and the parent walks `contentWindow` (``evasion.js``
section 8) and applies the spoofs from outside. Section 8 was written when it
was the only path that existed; its own comment recorded the measurement that
justified adding the audio and rect calls to it. Once
``Page.addScriptToEvaluateOnNewDocument`` began reaching frame realms, both
paths ran, and the additive three were applied twice. Measured on this host,
page against every one of its own iframes::

    audio  124.043476408231  vs  124.043477541301
    rect    11.687566983437  vs   11.687633966873

Deterministic, srcdoc and about:blank and URL frames alike. A page that
compares its own audio fingerprint against the one its own iframe reports saw
two different machines -- which is exactly the contradiction these spoofs
exist to prevent, and the noise was *causing* it.

Section 8 is now a fallback: it asks whether the frame already carries the
identity (by the one spoof only the injected script applies -- canvas noise)
and does nothing more if it does. That makes this suite guard the premise as
well as the fix, in both directions. If the parent starts double-applying
again the realms diverge upward; if the injector ever stops reaching frame
realms the parent's fallback still runs, and if *both* were to fail the frame
would report un-noised values and diverge the other way.
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

#: The CSS left/top the probe element is placed at. Blink quantises layout to
#: 1/64 px, so these resolve to exact, known values -- which is what lets the
#: pre-noise coordinate be recovered from a noised read (see RECT_GRID).
CSS_LEFT, CSS_TOP = 11.7, 3.3
CSS_W, CSS_H = 57.9, 13.1

#: Blink's LayoutUnit. The rect nudge is at most 5e-4 px, two orders below
#: half a grid step (0.0078), so rounding a noised coordinate to this grid
#: recovers the original unambiguously.
RECT_GRID = 64

IFRAMES = ("iframe_srcdoc", "iframe_blank", "iframe_url")
REALMS = ("top", *IFRAMES)

#: The probe. `seed` is templated in from Python because the page cannot read
#: it -- F41 removed the marker that used to expose the profile config -- and
#: the one-pass and two-pass predictions are computed in JavaScript rather
#: than Python on purpose: `getNoiseValue` is built on `Math.sin`, and V8's
#: sin is not bit-identical to the platform libm CPython uses.
_PROBE = r"""
async (w, SEED) => {
  const out = {};
  const noise = i => { const x = Math.sin(i * 9999.1234) * 10000; return x - Math.floor(x); };
  const nudge = v => {
    if (typeof v !== 'number' || !isFinite(v) || v === 0) return v;
    const k = Math.abs(Math.round(v * 1000));
    return v + (noise(SEED + k) - 0.5) * 1e-3;
  };

  // --- audio: the ordinary OfflineAudioContext fingerprint -------------
  try {
    const AC = w.OfflineAudioContext || w.webkitOfflineAudioContext;
    const ctx = new AC(1, 44100, 44100);
    const osc = ctx.createOscillator();
    osc.type = 'triangle';
    osc.frequency.value = 10000;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -50; comp.knee.value = 40;
    comp.ratio.value = 12; comp.attack.value = 0; comp.release.value = 0.25;
    osc.connect(comp); comp.connect(ctx.destination);
    osc.start(0);
    const d = (await ctx.startRendering()).getChannelData(0);
    let sum = 0;
    for (let i = 4500; i < 5000; i++) sum += Math.abs(d[i]);
    out.audio = sum.toFixed(12);
  } catch (e) { out.audio = 'E:' + e; }

  // --- client rects, with the prediction alongside ---------------------
  try {
    const el = w.document.createElement('div');
    el.style.cssText = 'position:absolute;left:__L__px;top:__T__px;'
      + 'width:__W__px;height:__H__px';
    w.document.body.appendChild(el);
    const r = el.getBoundingClientRect();
    el.remove();
    const G = __GRID__;
    const seen = [r.x, r.y, r.width, r.height];
    out.rect = seen.map(v => v.toFixed(12)).join('|');
    // recover the pre-noise value off Blink's layout grid, then say which
    // number of applications the read actually matches
    out.rectRaw = seen.map(v => (Math.round(v * G) / G).toFixed(12)).join('|');
    out.rectOnce = seen.map(v => nudge(Math.round(v * G) / G).toFixed(12)).join('|');
    out.rectTwice = seen.map(v => nudge(nudge(Math.round(v * G) / G)).toFixed(12)).join('|');
  } catch (e) { out.rect = 'E:' + e; }

  // --- WebGL readPixels, the third additive surface --------------------
  try {
    const c = w.document.createElement('canvas');
    c.width = c.height = 16;
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) { out.gl = 'no-context'; }
    else {
      gl.clearColor(0.3, 0.6, 0.9, 1.0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      const px = new Uint8Array(16 * 16 * 4);
      gl.readPixels(0, 0, 16, 16, gl.RGBA, gl.UNSIGNED_BYTE, px);
      let h = 0;
      for (let i = 0; i < px.length; i++) h = (h * 31 + px[i]) >>> 0;
      out.gl = h;
    }
  } catch (e) { out.gl = 'E:' + e; }

  return out;
}
""".replace("__L__", str(CSS_LEFT)).replace("__T__", str(CSS_TOP)) \
   .replace("__W__", str(CSS_W)).replace("__H__", str(CSS_H)) \
   .replace("__GRID__", str(RECT_GRID))

_PAGE = r"""
(async () => {
  const probe = __PROBE__;
  const SEED = __SEED__;
  const o = {};
  o.top = await probe(window, SEED);
  const mk = how => new Promise(res => {
    const f = document.createElement('iframe');
    if (how === 'srcdoc') f.srcdoc = '<!doctype html><title>f</title><body>';
    else f.src = how;
    f.onload = async () => {
      try { res(await probe(f.contentWindow, SEED)); }
      catch (e) { res({error: String(e)}); }
    };
    document.body.appendChild(f);
  });
  o.iframe_srcdoc = await mk('srcdoc');
  o.iframe_blank = await mk('about:blank');
  o.iframe_url = await mk('/f.html');
  return JSON.stringify(o);
})()
""".replace("__PROBE__", _PROBE)


class _Site:
    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>a</title><body>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/a"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.fingerprint._core import normalize_seed
    from multiprofile.storage.profiles import create_profile, get_profile

    tmp_path = tmp_path_factory.mktemp("frames")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    site = _Site()
    pid = create_profile({
        "name": "frames", "proxyType": "none", "startUrl": site.url,
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
            "params": {"expression": _PAGE.replace("__SEED__", str(seed)),
                       "awaitPromise": True, "returnByValue": True},
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
        got = json.loads(value)
        for realm in REALMS:
            assert "error" not in got[realm], (realm, got[realm])
        got["seed"] = seed
        return got
    finally:
        L.stop_profile(pid)
        site.close()
        mp.undo()


# ---------------------------------------------------------------------------
# agreement: the page and its own frames describe one machine
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("realm", IFRAMES)
def test_the_frame_reports_the_same_audio_as_its_page(frames, realm):
    assert frames[realm]["audio"] == frames["top"]["audio"], (
        realm, frames[realm]["audio"], frames["top"]["audio"])


@pytest.mark.parametrize("realm", IFRAMES)
def test_the_frame_reports_the_same_rects_as_its_page(frames, realm):
    assert frames[realm]["rect"] == frames["top"]["rect"], (
        realm, frames[realm]["rect"], frames["top"]["rect"])


@pytest.mark.parametrize("realm", IFRAMES)
def test_the_frame_reports_the_same_webgl_pixels_as_its_page(frames, realm):
    assert frames[realm]["gl"] == frames["top"]["gl"], (
        realm, frames[realm]["gl"], frames["top"]["gl"])


# ---------------------------------------------------------------------------
# and the absolute check: exactly one application, not merely a matching one
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("realm", REALMS)
def test_the_rect_noise_is_applied_exactly_once(frames, realm):
    """Agreement alone cannot catch both realms being wrong together.

    The nudge is deterministic in the original coordinate, and Blink has
    already quantised that coordinate to 1/64 px -- two orders of magnitude
    coarser than the nudge -- so the pre-noise value can be recovered from the
    read and the prediction computed from it.
    """
    got = frames[realm]
    if got["rect"] == got["rectTwice"]:
        pytest.fail(f"{realm}: rect noise APPLIED TWICE ({got['rect']})")
    if got["rect"] == got["rectRaw"]:
        pytest.fail(f"{realm}: rect noise NOT APPLIED ({got['rect']})")
    assert got["rect"] == got["rectOnce"], (
        realm, "observed", got["rect"], "expected one application", got["rectOnce"])


def test_the_measurement_is_not_vacuous(frames):
    """Positive control. Every assertion above compares strings, and three of
    them would pass if the predictions all collapsed to the same value."""
    top = frames["top"]
    assert frames["seed"] > 1, frames["seed"]
    # the three predictions are genuinely different numbers
    assert len({top["rectRaw"], top["rectOnce"], top["rectTwice"]}) == 3, top
    # the recovered pre-noise coordinates really are on Blink's layout grid
    for v in top["rectRaw"].split("|"):
        scaled = float(v) * RECT_GRID
        assert abs(scaled - round(scaled)) < 1e-6, (v, scaled)
    # audio and WebGL answered with values, not error strings
    assert not str(top["audio"]).startswith("E:"), top["audio"]
    assert isinstance(top["gl"], int), top["gl"]
    assert float(top["audio"]) > 0, top["audio"]
