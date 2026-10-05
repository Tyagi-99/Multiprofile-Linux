"""AudioContext and ClientRects must not link every profile on one machine.

Both surfaces were entirely unhooked. They are computed by the host's audio
stack and the host's rendering stack, so every profile returned the same
answer. Measured before the fix -- two profiles, different seeds, claiming a
GTX 1660 and an RTX 3060 respectively::

    seed 111   audio sum 172.6716509661   rect 10.093750,20.187500,30.296875,40.390625
    seed 222   audio sum 172.6716509661   rect 10.093750,20.187500,30.296875,40.390625

Byte for byte identical, while canvas and WebGL differed per profile. That is
worse than either profile being identified on its own: it **links them to each
other**. Two "different computers" that share an audio fingerprint and
sub-pixel text metrics to twelve decimal places are one computer.

The noise is deterministic in (seed, index, original value), which is the part
that matters. A value that moved between reads would be a louder tell than the
shared one and would break real sites -- drag-and-drop, sticky headers and
hit-testing all read a rect more than once and expect it to hold still. So
these tests assert three things, and the third is the one that would be easy to
get wrong:

1. two profiles differ;
2. one profile is stable across repeated reads;
3. one profile is *reproducible* -- a freshly rendered audio context gives the
   same answer as the first, the way a real machine would.
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
  const out = {};
  const el = document.createElement('div');
  el.style.cssText = 'position:absolute;left:10.1px;top:20.2px;width:30.3px;height:40.4px';
  document.body.appendChild(el);

  const reads = [];
  for (let i = 0; i < 5; i++) {
    const r = el.getBoundingClientRect();
    reads.push([r.x, r.y, r.width, r.height].map(v => v.toFixed(9)).join(','));
  }
  out.rect = reads[0];
  out.rect_stable = new Set(reads).size === 1;

  const render = async () => {
    const ctx = new OfflineAudioContext(1, 44100, 44100);
    const osc = ctx.createOscillator(); osc.type = 'triangle'; osc.frequency.value = 10000;
    const comp = ctx.createDynamicsCompressor();
    osc.connect(comp); comp.connect(ctx.destination); osc.start(0);
    const buf = await ctx.startRendering();
    return buf;
  };

  const buf = await render();
  const sums = [];
  for (let n = 0; n < 3; n++) {
    const d = buf.getChannelData(0);
    let s = 0; for (let i = 4500; i < 5000; i++) s += Math.abs(d[i]);
    sums.push(s.toFixed(12));
  }
  out.audio = sums[0];
  out.audio_stable = new Set(sums).size === 1;

  const buf2 = await render();
  const d2 = buf2.getChannelData(0);
  let s2 = 0; for (let i = 4500; i < 5000; i++) s2 += Math.abs(d2[i]);
  out.audio_reproducible = s2.toFixed(12) === sums[0];

  return JSON.stringify(out);
})()
"""


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>s</title>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/s"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _probe_profile(seed: int, tmp_path, monkeypatch) -> dict:
    """Launch one profile with the given seed and read both surfaces."""
    import websocket

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / f"data{seed}"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / f"cache{seed}"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / f"cfg{seed}"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({
        "name": f"s{seed}", "proxyType": "none", "startUrl": page.url, "seed": seed,
    })["id"]
    try:
        assert L.launch_profile(pid, url=page.url, direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        time.sleep(2.0)

        def _cdp(path):
            r = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", headers={"Host": f"localhost:{port}"}
            )
            return json.loads(urllib.request.urlopen(r, timeout=5).read())

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
        value = None
        failure = None
        deadline = time.time() + 60
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                result = msg.get("result") or {}
                # Surface what the page actually said. "the probe returned
                # nothing" is true of a thrown exception, a rejected promise
                # and a timeout alike, and having to guess between them from
                # a CI log is the same swallowing this suite exists to catch.
                details = result.get("exceptionDetails")
                if details:
                    text = (details.get("exception") or {}).get("description")
                    failure = text or details.get("text") or json.dumps(details)[:400]
                value = (result.get("result") or {}).get("value")
                break
        ws.close()
        if failure:
            raise AssertionError(f"the probe raised in the page: {failure}")
        assert value, "the probe returned nothing (no exception, no value — a timeout)"
        return json.loads(value)
    finally:
        L.stop_profile(pid)
        page.close()


@pytest.fixture(scope="module")
def two_profiles(tmp_path_factory):
    """Two profiles with different seeds, probed once each."""
    from _pytest.monkeypatch import MonkeyPatch

    out = {}
    for seed in (111, 222):
        mp = MonkeyPatch()
        try:
            out[seed] = _probe_profile(seed, tmp_path_factory.mktemp(f"p{seed}"), mp)
        finally:
            mp.undo()
    return out


def test_two_profiles_do_not_share_an_audio_fingerprint(two_profiles):
    """The measurement in the module docstring, as an assertion."""
    a, b = two_profiles[111]["audio"], two_profiles[222]["audio"]
    assert a != b, f"both profiles rendered {a}"


def test_two_profiles_do_not_share_their_sub_pixel_metrics(two_profiles):
    a, b = two_profiles[111]["rect"], two_profiles[222]["rect"]
    assert a != b, f"both profiles measured {a}"


def test_a_rect_holds_still_across_reads(two_profiles):
    """Drag-and-drop, sticky headers and hit-testing all re-read a rect.

    A value that drifted would break real sites and be a louder tell than the
    shared one it replaced.
    """
    for seed, probe in two_profiles.items():
        assert probe["rect_stable"] is True, seed


def test_an_audio_buffer_reads_the_same_twice(two_profiles):
    for seed, probe in two_profiles.items():
        assert probe["audio_stable"] is True, seed


def test_the_audio_fingerprint_is_reproducible(two_profiles):
    """A freshly rendered context must agree with the first.

    Per-render randomness would defeat linkage but is not what a real machine
    does, and a site rendering twice would see it.
    """
    for seed, probe in two_profiles.items():
        assert probe["audio_reproducible"] is True, seed


# What this host's Chromium measures for that div with NO noise applied.
# Chrome quantises layout to 1/64 px, so the CSS 20.2 becomes 20.1875 before
# anything of ours runs -- comparing against the CSS value would be measuring
# Chrome's rounding, not our nudge.
_UNNOISED_RECT = (10.093750, 20.187500, 30.296875, 40.390625)
_UNNOISED_AUDIO = 172.6716509661


def test_the_noise_stays_sub_pixel(two_profiles):
    """Large enough to separate machines, small enough to disturb no layout."""
    for _seed, probe in two_profiles.items():
        measured = [float(v) for v in probe["rect"].split(",")]
        for got, base in zip(measured, _UNNOISED_RECT, strict=True):
            assert abs(got - base) < 2e-3, (probe["rect"], base)


def test_the_audio_shift_is_inaudible(two_profiles):
    """~1e-7 amplitude: it must not be audible or affect real playback."""
    for _seed, probe in two_profiles.items():
        assert abs(float(probe["audio"]) - _UNNOISED_AUDIO) < 1e-3, probe["audio"]
