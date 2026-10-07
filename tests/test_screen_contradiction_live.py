"""The screen the page reports and the screen CSS reports must be one screen.

Measured on this host against https://abrahamjuliot.github.io/creepjs/ with a
product profile that had **no device preset** on a direct route:

    Screen            ...screen: 1366 x 768      <- navigator/screen, JS
    CSS Media Queries screen query: 2560 x 1600  <- device-width/height

and stock Chromium on the same host, same page, reported 2560 x 1600 in
*both*. The host display is 2560x1600 (``hyprctl monitors``), so the CSS
figure is the real one and the 1366x768 is the claim.

Why the two disagree: ``evasion.js`` defines ``screen.width``,
``screen.height``, ``availWidth``, ``availHeight`` and
``window.devicePixelRatio`` as JavaScript getters. CSS ``device-width`` and
``device-height`` are resolved by the compositor from the real display and
cannot be reached from script at all. ``Emulation.setDeviceMetricsOverride``
is the only thing that moves both, and the launcher sent it **only when a
device preset was selected** -- so every profile without one carried this
contradiction.

It is a one-line check for a site: compare ``screen.width`` against
``matchMedia('(device-width: ' + screen.width + 'px)')``. No fingerprinting
library required, which is the same shape as F1.
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


# device-width / device-height, found the way CreepJS finds them: ask CSS
# directly rather than asking script what it thinks. The exact-match query is
# tried first and a bounded search is the fallback, so a browser that rounds
# still reports a number instead of nothing.
_PROBE = r"""
(() => {
  const mm = q => matchMedia(q).matches;
  // `(min-device-width: Npx)` holds while N <= device-width, so the largest
  // matching N *is* device-width. Resolved by CSS from the real display;
  // script cannot reach it.
  const find = axis => {
    let lo = 1, hi = 20000, best = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (mm(`(min-device-${axis}: ${mid}px)`)) { best = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    return best;
  };
  return JSON.stringify({
    screenW: screen.width,
    screenH: screen.height,
    availW: screen.availWidth,
    availH: screen.availHeight,
    dpr: window.devicePixelRatio,
    cssDeviceW: find('width'),
    cssDeviceH: find('height'),
    exactW: mm(`(device-width: ${screen.width}px)`),
    exactH: mm(`(device-height: ${screen.height}px)`),
    innerW: window.innerWidth,
    innerH: window.innerHeight,
    outerW: window.outerWidth,
    outerH: window.outerHeight,
  });
})()
"""


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>s</title>ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
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


def _measure(port: int) -> dict:
    import websocket

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
            "params": {"expression": _PROBE, "returnByValue": True},
        }))
        deadline = time.time() + 60
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                result = msg.get("result") or {}
                details = result.get("exceptionDetails")
                if details:
                    raise AssertionError(f"probe raised: {details}")
                value = (result.get("result") or {}).get("value")
                assert value, f"probe returned nothing: {result}"
                return json.loads(value)
        raise AssertionError("probe timed out")
    finally:
        ws.close()


@pytest.fixture(scope="module")
def screens(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("screens")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    # No devicePresetId on purpose: that is the path the capture came from.
    pid = create_profile({"name": "screens", "proxyType": "none",
                          "startUrl": page.url})["id"]
    try:
        assert L.launch_profile(pid, url=page.url, direct=True).get("ok") is True
        time.sleep(2.0)
        out = _measure(get_runtime(pid).cdp_port)
        out["_profile"] = pid
        return out
    finally:
        L.stop_profile(pid)
        page.close()
        mp.undo()


def test_css_reports_the_same_device_width_as_screen(screens):
    """The defect, as the one-line check a site would use."""
    assert screens["cssDeviceW"] == screens["screenW"], (
        f"screen.width={screens['screenW']} but CSS device-width="
        f"{screens['cssDeviceW']}"
    )


def test_css_reports_the_same_device_height_as_screen(screens):
    assert screens["cssDeviceH"] == screens["screenH"], (
        f"screen.height={screens['screenH']} but CSS device-height="
        f"{screens['cssDeviceH']}"
    )


def test_the_exact_media_query_for_the_claimed_screen_matches(screens):
    """`(device-width: Npx)` for the N the profile claims. This is the form a
    checker actually writes, and it is immune to the search above being
    wrong."""
    assert screens["exactW"] is True, screens
    assert screens["exactH"] is True, screens


def test_the_probe_measured_something(screens):
    """Positive control: a search that returned 1 for both axes would make
    the assertions above pass against a broken measurement."""
    assert screens["screenW"] > 100, screens
    assert screens["cssDeviceW"] > 100, screens
    assert screens["screenH"] > 100, screens


def test_the_viewport_was_not_resized_by_the_override(screens):
    """The reason this can apply to every profile and not only preset ones.

    `Emulation.setDeviceMetricsOverride` normally letterboxes and scales the
    page to the overridden viewport, which would make the operator's window
    unusable -- that is why the no-preset path originally skipped it and
    carried the contradiction instead. Sending `width`/`height`/
    `deviceScaleFactor` as 0 means "screen only, viewport untouched".

    Verified on Chromium 151 while building this: `screenHeight` alone moved
    CSS device-height 1600 -> 1440 with innerWidth, innerHeight, outerWidth
    and outerHeight byte-identical before and after.

    A viewport the size of the claimed screen would be the signature of
    getting this wrong, because the claim is usually smaller than the real
    window.
    """
    assert screens["innerW"] > 0 and screens["innerH"] > 0, screens
    # The window is laid out by the compositor, so the viewport must NOT have
    # collapsed onto the claimed screen size.
    assert not (screens["innerW"] == screens["screenW"]
                and screens["innerH"] == screens["screenH"]), (
        "the viewport collapsed onto the claimed screen size, so the override "
        f"resized the page: {screens}"
    )
    # ...and the inner viewport must still fit inside the claimed screen,
    # which is what a real browser window does.
    assert screens["innerW"] <= screens["screenW"], screens
