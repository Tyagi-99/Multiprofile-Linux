"""What a launched profile actually enumerates.

The policy tests check the plan. This one checks the engine, because the
whole approach rests on Chromium honouring ``FONTCONFIG_FILE`` -- measured
against stock Chromium 151 on this host::

    DEFAULT     [C059, P052, Z003, D050000L, Standard Symbols PS, Nimbus Roman,
                 Nimbus Sans, Liberation Sans, Noto Sans, Arial, Times New Roman]
    RESTRICTED  [Liberation Sans, Noto Sans]

It also asserts the property a ``measureText`` hook would have destroyed:
canvas and DOM font detection must return the same answer for every family.
They agree on a stock browser, so a disagreement would be a tell in itself.
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
(() => {
  const BASES = ['monospace','sans-serif','serif'];
  const S = 'mmmmmmmmmmlli-WWWW@#';
  const c = document.createElement('canvas').getContext('2d');
  const cb = {}; for (const b of BASES) { c.font = '72px ' + b; cb[b] = c.measureText(S).width; }
  const canvasHas = f => BASES.some(b => {
    c.font = `72px "${f}", ${b}`; return c.measureText(S).width !== cb[b];
  });
  const mk = fam => { const s = document.createElement('span');
    s.style.cssText = 'position:absolute;left:-9999px;font-size:72px;white-space:nowrap';
    s.style.fontFamily = fam; s.textContent = S; document.body.appendChild(s); return s; };
  const db = {}; for (const b of BASES) {
    const s = mk(b); db[b] = [s.offsetWidth, s.getBoundingClientRect().width]; s.remove();
  }
  const domHas = f => BASES.some(b => { const s = mk(`"${f}", ${b}`);
    const r = [s.offsetWidth, s.getBoundingClientRect().width]; s.remove();
    return r[0] !== db[b][0] || r[1] !== db[b][1]; });
  const WIN = ['Arial','Times New Roman','Courier New'];
  const GHOSTSCRIPT = ['C059','P052','Z003','D050000L','Standard Symbols PS'];
  const DISTRO = ['Nimbus Roman','Nimbus Sans','URW Bookman','DejaVu Sans',
                  'DejaVu Serif','Cantarell','Ubuntu','FreeSans','Adwaita Sans'];
  const out = {};
  out.win = WIN.filter(canvasHas);
  out.ghostscript = GHOSTSCRIPT.filter(canvasHas);
  out.distro = DISTRO.filter(canvasHas);
  out.disagree = [...WIN, ...GHOSTSCRIPT, ...DISTRO]
      .filter(f => canvasHas(f) !== domHas(f));
  out.generics = {sans: cb['sans-serif'], serif: cb['serif'], mono: cb['monospace']};
  out.platform = navigator.platform;
  return JSON.stringify(out);
})()
"""


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>f</title>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/f"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture(scope="module")
def windows_profile(tmp_path_factory):
    """One profile claiming Windows, probed once."""
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("fonts")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    # `userAgentId` is explicit now. This fixture used to pass only
    # `os: "windows"`, which reaches the font bucket but not the fingerprint
    # pool -- the Windows *platform* came from `_detect_pool("")`'s default.
    # That default is now the host's own platform, so a test about a Windows
    # claim has to make the claim rather than inherit it. See
    # `fingerprint/identity_matrix.py`.
    pid = create_profile({
        "name": "fontwin", "proxyType": "none", "startUrl": page.url,
        "os": "windows", "userAgentId": "win_chrome",
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
            "params": {"expression": _PROBE, "returnByValue": True},
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


def test_the_profile_really_claims_windows(windows_profile):
    """Otherwise the rest of this file proves nothing."""
    assert windows_profile["platform"] == "Win32"


def test_no_ghostscript_font_survives_a_windows_claim(windows_profile):
    """C059, P052, Z003, D050000L and Standard Symbols PS are on no Windows
    machine. Unlike a missing Calibri they are not weak evidence -- they are
    proof that a self-declared Windows box is a Linux one."""
    assert windows_profile["ghostscript"] == [], windows_profile["ghostscript"]


def test_no_distribution_font_survives_a_windows_claim(windows_profile):
    assert windows_profile["distro"] == [], windows_profile["distro"]


def test_the_core_windows_families_are_present(windows_profile):
    """A machine with no Arial at all would be its own anomaly."""
    assert "Arial" in windows_profile["win"], windows_profile["win"]
    assert "Times New Roman" in windows_profile["win"], windows_profile["win"]


def test_canvas_and_dom_agree_on_every_family(windows_profile):
    """The property a measureText hook would have destroyed.

    Canvas and DOM detection agree on a stock browser. Suppressing a font in
    measureText while a span measuring the same family still laid out at its
    real width would produce a disagreement no real browser shows -- a new
    tell in place of the old one.
    """
    assert windows_profile["disagree"] == [], windows_profile["disagree"]


def test_the_generic_families_still_differ(windows_profile):
    """Sanity: if sans-serif, serif and monospace all collapsed to one face,
    the profile would render every page in that face. Measured here, an
    early version of the config resolved sans-serif to Courier New."""
    g = windows_profile["generics"]
    assert g["sans"] != g["mono"], g
    assert g["serif"] != g["mono"], g
