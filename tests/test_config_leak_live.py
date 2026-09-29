"""F41, measured: the page must not be able to read the spoofing config.

``build_identity_script`` emitted its configuration at top level::

    var __MP_INLINE_CFG__ = {...};
    window.__MP_PROFILE__ = __MP_INLINE_CFG__;
    (function(){ /* evasion.js */ })();

Both are properties of the global object, so every page could read the whole
thing. Measured on a live profile before the fix::

    window.__MP_PROFILE__  ->  25 keys, 1977 bytes
    keys: architecture, bitness, ch_brands, ch_full_version_list, ch_platform,
          clientHints, cores, engineVersion, gpu, isMobile, language,
          languages, mediaDeviceGroupIds, mediaDeviceIds, memory, model,
          platform, platformVersion, screen, seed, timezone, touch,
          userAgent, webrtcMode, webrtcPolicy
    seed: 1479877959

The presence of the property names the product in one read. The **seed** is
what makes this worse than doing no spoofing at all: it derives every noise
value the script produces, so a site that reads it can undo the canvas, audio
and ClientRects noise *and* keep the seed itself as a stable cross-site
identifier — more reliable than the fingerprint it was meant to hide, and
unique to the profile.

`evasion.js` is already an IIFE, so the fix is one more wrapper: the config
becomes a closure variable, reachable by the code that needs it and by nothing
else.

This test is the evidence, and the regression guard. The unit tests in
``test_cdp_inject.py`` pin the shape of the generated string; this one asserts
what a real page can actually see.
"""
from __future__ import annotations

import json
import threading
import time
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


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>leak</title>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/leak"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


#: Everything a page could use to discover that it is inside this product, or
#: to recover the values the spoofing is derived from.
_PROBE = """(() => {
  const globals = Object.getOwnPropertyNames(window)
    .filter(k => /^__MP|multiprofile|evasion/i.test(k));
  return JSON.stringify({
    profile: typeof window.__MP_PROFILE__,
    inline: typeof window.__MP_INLINE_CFG__,
    globals: globals,
    // The identity still has to work, or the fix traded one failure for
    // another: a page that reads nothing but is also not spoofed.
    platform: navigator.platform,
    ua: navigator.userAgent,
  });
})()"""


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    import urllib.request

    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as launcher
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("leak")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({
        "name": "leak", "proxyType": "none", "startUrl": page.url,
        "userAgentId": "win_chrome", "timezone": "America/New_York",
    })["id"]
    try:
        assert launcher.launch_profile(pid, url=page.url, direct=True).get("ok") is True
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
            target["webSocketDebuggerUrl"], timeout=60, suppress_origin=True
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 60
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        yield json.loads(value)
    finally:
        try:
            launcher.stop_profile(pid)
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass
        page.close()
        mp.undo()


def test_the_page_cannot_read_the_profile_config(measured):
    """The finding, stated as the behaviour it broke."""
    assert measured["profile"] == "undefined", (
        "window.__MP_PROFILE__ hands the page the whole spoofing config, "
        "including the seed every noise value is derived from"
    )


def test_the_inline_config_is_not_a_global_either(measured):
    """A top-level `var` is a property of the global object, so moving the
    assignment off `window` is not enough on its own."""
    assert measured["inline"] == "undefined"


def test_nothing_on_window_names_this_product(measured):
    """One property read must not identify the browser. This is F7's finding
    -- the evasion script announcing itself -- one layer down."""
    assert measured["globals"] == [], measured["globals"]


def test_the_identity_still_works(measured):
    """Otherwise the fix traded one failure for a worse one: a page that can
    read nothing because the spoofing never loaded."""
    assert measured["platform"] == "Win32"
    assert "Windows NT 10.0" in measured["ua"]
