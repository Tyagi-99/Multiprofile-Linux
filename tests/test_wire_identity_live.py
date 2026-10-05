"""F1, measured: a real browser, real headers, off the wire.

The unit tests in ``test_wire_identity.py`` pin the contract. This one is the
evidence. It launches a profile through the real launcher with NO device
preset and NO explicit User-Agent -- the profile the quick-create button
makes, and the exact shape that browsed as the host -- points it at a
loopback server, and reads the headers that arrive.

Measured on Chromium 141 before the fix::

    User-Agent:         Mozilla/5.0 (X11; Linux x86_64) ... Chrome/141.0.0.0
    sec-ch-ua-platform: "Linux"
    navigator.platform: "Win32"

and after::

    User-Agent:         Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131.0.0.0
    sec-ch-ua-platform: "Windows"
    navigator.platform: "Win32"

Skipped when no browser is installed, so the suite still runs without one.
"""
from __future__ import annotations

import json
import queue
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


class _Echo:
    """Loopback server that records the headers of every request it gets."""

    def __init__(self) -> None:
        self.seen: queue.Queue = queue.Queue()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                outer.seen.put({k.lower(): v for k, v in self.headers.items()})
                body = b"<!doctype html><title>wire</title>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/wire"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def wait(self, timeout: float = 20.0):
        try:
            return self.seen.get(timeout=timeout)
        except queue.Empty:
            return None


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")


def _cdp_json(port: int, path: str):
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"Host": f"localhost:{port}"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _evaluate(ws, msg_id: int, expression: str):
    ws.send(json.dumps({
        "id": msg_id,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True},
    }))
    deadline = time.time() + 15
    while time.time() < deadline:
        msg = json.loads(ws.recv())
        if msg.get("id") == msg_id:
            return msg["result"]["result"]["value"]
    return None


def test_a_no_preset_profile_does_not_leak_the_host_identity(xdg):
    """THE live proof for F1.

    Nothing is faked: a real launch, a real navigation, and the headers as
    the server received them compared against what the page says about
    itself.
    """
    import websocket

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    echo = _Echo()
    # `userAgentId` is explicit. A no-preset profile now claims the host's own
    # platform, and on a Linux host a Linux claim cannot distinguish "the
    # launcher set this" from "the host leaked through" -- which is the F1
    # failure this test exists to prove absent. A Windows claim on this host
    # can only have come from the launcher, so it is the direction that
    # actually proves the property. The claim is experimental per
    # `fingerprint/identity_matrix.py`; it is not refused, and it must still
    # reach the wire.
    profile = create_profile({
        "name": "f1-wire-live",
        "proxyType": "none",
        "os": "windows",
        "userAgentId": "win_chrome",
        "startUrl": "about:blank",
    })
    pid = profile["id"]
    assert not profile.get("deviceId"), "this test is about the NO-preset path"

    try:
        res = L.launch_profile(pid, url="about:blank", direct=True)
        assert res.get("ok") is True, res.get("error")
        assertion = res.get("identityAssertion") or {}

        # Independently of the launcher's own probe: our connection, our server.
        port = get_runtime(pid).cdp_port
        assert port
        page = next(
            t for t in _cdp_json(port, "/json") if t.get("type") == "page"
        )
        ws = websocket.WebSocket()
        ws.connect(page["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
        try:
            ws.send(json.dumps({
                "id": 1, "method": "Page.navigate", "params": {"url": echo.url},
            }))
            wire = echo.wait()
            assert wire is not None, "the browser never reached the loopback server"
            time.sleep(1.0)
            claimed = _evaluate(ws, 2, "navigator.platform")
            ua_js = _evaluate(ws, 3, "navigator.userAgent")
        finally:
            ws.close()
    finally:
        L.stop_profile(pid)
        echo.close()

    ua = wire.get("user-agent") or ""
    assert "Windows NT" in ua, f"host User-Agent leaked on the wire: {ua!r}"
    assert "X11; Linux" not in ua
    assert wire.get("sec-ch-ua-platform") == '"Windows"', (
        f"host platform leaked in Client Hints: {wire.get('sec-ch-ua-platform')!r}"
    )
    # The contradiction F1 produced: headers and JS must now agree.
    assert claimed == "Win32", claimed
    assert ua_js == ua, f"JS UA {ua_js!r} != wire UA {ua!r}"

    # And the launcher must have PROVED all of the above at launch time
    # rather than reporting it on trust, which is how F1 stayed hidden.
    assert assertion.get("conclusive") is True, (
        f"launch-time identity probe did not run: {assertion}"
    )
    assert assertion.get("ok") is True, assertion.get("mismatches")


def test_accept_language_on_the_wire_is_well_formed(xdg):
    """The override used to be handed a finished header, and Chromium then
    sent "en-US,en;q=0.9;q=0.9" — a string no real browser emits."""
    import websocket

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    echo = _Echo()
    pid = create_profile({
        "name": "f1-accept-language",
        "proxyType": "none",
        "language": "en-US",
        "startUrl": "about:blank",
    })["id"]
    try:
        assert L.launch_profile(pid, url="about:blank", direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        page = next(t for t in _cdp_json(port, "/json") if t.get("type") == "page")
        ws = websocket.WebSocket()
        ws.connect(page["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
        try:
            ws.send(json.dumps({
                "id": 1, "method": "Page.navigate", "params": {"url": echo.url},
            }))
            wire = echo.wait()
        finally:
            ws.close()
    finally:
        L.stop_profile(pid)
        echo.close()

    assert wire is not None
    accept_language = wire.get("accept-language") or ""
    assert accept_language == "en-US,en;q=0.9", accept_language
