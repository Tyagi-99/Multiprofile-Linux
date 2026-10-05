"""The F1 residual, measured: a NEW tab's very first document request.

F1's fix put the identity on every renderer, via ``Network.setUserAgentOverride``
applied per attached target on a session held for the browser's lifetime. It
left one request uncovered, and the audit recorded it as the residual: a new
tab's first document request is formed by the **browser process**, before the
renderer that override belongs to exists. No per-target interception can see
that request however early it is armed, because at that moment there is no
target to arm.

Measured on Chromium 151.0.7922.173, a tab opened the way the operator opens
one (``Target.createTarget`` with a URL)::

    intended : sec-ch-ua          "Google Chrome";v="151", "Chromium";v="151", "Not-A.Brand";v="99"
               sec-ch-ua-platform "Windows"
    actual   : sec-ch-ua          "Chromium";v="151", "Not=A?Brand";v="99"
               sec-ch-ua-platform "Linux"

Two headers, not the one the audit recorded. The User-Agent was already right
-- it comes from the ``--user-agent`` process flag -- so what went out was a
**Windows UA beside the host's own Linux brand list**, in Chromium's GREASE
spelling. That pairing is a contradiction no real browser produces, which is
precisely the F1 class of defect, surviving on one request per tab.

The fix arms ``Fetch`` on the browser session rather than per target, narrowed
to Document requests, and rewrites the low-entropy hints from the same
``ua_params`` the override uses.

These tests are the evidence. Skipped when no browser is installed.
"""
from __future__ import annotations

import json
import queue
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


class _Echo:
    """Loopback server that records the headers of every request it gets."""

    def __init__(self) -> None:
        self.seen: queue.Queue = queue.Queue()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                outer.seen.put({k.lower(): v for k, v in self.headers.items()})
                body = b"<!doctype html><title>first</title>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/first"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def wait(self, timeout: float = 25.0):
        try:
            return self.seen.get(timeout=timeout)
        except queue.Empty:
            return None


@pytest.fixture()
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")


def _cdp(port: int, path: str):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"Host": f"localhost:{port}"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _open_new_tab(port: int, url: str) -> None:
    """Open a tab the way the operator does -- browser-process navigation."""
    import websocket

    ver = _cdp(port, "/json/version")
    ws = websocket.create_connection(
        ver["webSocketDebuggerUrl"], timeout=10, suppress_origin=True
    )
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Target.createTarget", "params": {"url": url},
        }))
        time.sleep(0.3)
    finally:
        ws.close()


@pytest.fixture()
def launched(xdg):
    """A no-preset, no-explicit-UA profile: the quick-create shape."""
    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    echo = _Echo()
    # `userAgentId` is explicit. This file measures whether a NEW TAB's first
    # document request carries the *intended* platform -- the residual F1 --
    # and the intended platform used to be Windows only because
    # `_detect_pool("")` defaulted there. The default is now the host's own
    # platform, and on a Linux host `sec-ch-ua-platform: "Linux"` cannot
    # distinguish "we set it" from "the host leaked through", which is the
    # failure this file exists to detect. A Windows claim on this host can
    # only have come from us.
    pid = create_profile({
        "name": "first-request", "proxyType": "none", "startUrl": "about:blank",
        "userAgentId": "win_chrome",
    })["id"]
    res = L.launch_profile(pid, url="about:blank", direct=True)
    assert res.get("ok") is True, res.get("error")
    time.sleep(1.0)
    try:
        yield pid, get_runtime(pid).cdp_port, echo, res
    finally:
        L.stop_profile(pid)
        echo.close()


def test_a_new_tabs_first_request_carries_the_intended_platform(launched):
    """The measurement in the module docstring, as an assertion."""
    _pid, port, echo, _res = launched
    _open_new_tab(port, echo.url)

    headers = echo.wait()
    assert headers is not None, "the new tab never reached the loopback server"
    assert headers.get("sec-ch-ua-platform") == '"Windows"', headers


def test_a_new_tabs_first_request_carries_the_intended_brands(launched):
    """The half the audit did not record: the brand list leaked too."""
    _pid, port, echo, _res = launched
    _open_new_tab(port, echo.url)

    headers = echo.wait()
    assert headers is not None
    brands = headers.get("sec-ch-ua") or ""
    assert "Google Chrome" in brands, brands
    # Chromium's own GREASE spelling is the tell that this is the host's list.
    assert "Not=A?Brand" not in brands, brands


def test_the_first_request_does_not_contradict_its_own_user_agent(launched):
    """A Windows UA beside a Linux platform hint is worse than no spoofing.

    This is the assertion that matters: the headers on that one request have
    to agree with each other, whatever they claim.
    """
    _pid, port, echo, _res = launched
    _open_new_tab(port, echo.url)

    headers = echo.wait()
    assert headers is not None
    ua = headers.get("user-agent") or ""
    platform = headers.get("sec-ch-ua-platform") or ""
    assert ("Windows" in ua) == ("Windows" in platform), (ua, platform)


def test_the_tab_still_loads(launched):
    """Fetch interception that hangs a navigation would be the worse bug.

    Every paused request must be released; if the rewrite path ever throws
    and fails to continue, this is the test that catches it.
    """
    _pid, port, echo, _res = launched
    _open_new_tab(port, echo.url)
    assert echo.wait() is not None, "the navigation never completed"


def test_the_launch_time_assertion_still_passes(launched):
    """Browser-level Fetch must not disturb the identity probe from Gate 1."""
    _pid, _port, _echo, res = launched
    notes = " ".join(res.get("notes") or [])
    assert "identity verified on the wire" in notes, res.get("notes")
