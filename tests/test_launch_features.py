"""The features a launch turns on, and the one flag that carries them.

Chrome honours the **last** ``--enable-features`` on the command line rather
than the union of them. Two such flags means the first is silently dropped,
which is the kind of failure that looks like it works -- so the launch
composes exactly one, and these tests hold that line.

``WebBluetooth`` is on because this host's Chromium build ships it disabled
while upstream Chrome on Windows and macOS does not. Measured on stock
Chromium 151::

    default                          navigator.bluetooth  undefined
                                     Bluetooth            undefined
                                     BluetoothDevice      undefined
    --enable-features=WebBluetooth   all three present

A profile whose User-Agent says Windows and whose navigator has no Bluetooth
at all is describing a browser that does not exist. Turning the genuine
interface family back on is better than synthesising one: a fake
``navigator.bluetooth`` without the matching constructors would trade one
contradiction for a more detailed one.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import multiprofile.browser.launcher as L

# --------------------------------------------------------------------------
# 1. One flag, composed
# --------------------------------------------------------------------------

def test_web_bluetooth_is_requested(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_WEBGPU", raising=False)
    assert "WebBluetooth" in L.chrome_enabled_features()


def test_vulkan_rides_along_in_the_same_list(monkeypatch):
    """Two --enable-features flags would drop one of them."""
    monkeypatch.delenv("MULTIPROFILE_WEBGPU", raising=False)
    features = L.chrome_enabled_features()
    assert "Vulkan" in features
    assert len(features) == len(set(features))


def test_the_gpu_flags_no_longer_carry_their_own_feature_flag():
    """If they did, the launch would emit two --enable-features and Chrome
    would honour only the last."""
    for mode in ("auto", "angle", "software", "disabled"):
        assert not any(a.startswith("--enable-features") for a in L.chrome_gpu_args(mode)), mode


def test_turning_webgpu_off_leaves_bluetooth_alone(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_WEBGPU", "0")
    features = L.chrome_enabled_features()
    assert "Vulkan" not in features
    assert "WebBluetooth" in features


# --------------------------------------------------------------------------
# 2. Live: exactly one flag reaches Chrome, and it works
# --------------------------------------------------------------------------

def _chrome_available() -> bool:
    try:
        L.find_chrome()
        return True
    except Exception:
        return False


@pytest.mark.network
@pytest.mark.skipif(not _chrome_available(), reason="Chrome is not installed")
def test_the_launch_emits_exactly_one_enable_features(monkeypatch, tmp_path):
    """Guards the composition at the place it actually matters."""
    from multiprofile.storage.profiles import create_profile

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    seen: list[list[str]] = []
    real_popen = L.subprocess.Popen

    def spy(cmd, **kw):
        if "chromium" in str(cmd[0]) or "chrome" in str(cmd[0]):
            if "--version" not in cmd:
                seen.append(list(cmd))
                raise RuntimeError("stop before spawning")
        return real_popen(cmd, **kw)

    monkeypatch.setattr(L.subprocess, "Popen", spy)
    pid = create_profile({"name": "f", "proxyType": "none", "startUrl": "about:blank"})["id"]
    try:
        L.launch_profile(pid, url="about:blank", direct=True)
    except Exception:
        pass

    assert seen, "the launch never reached the spawn"
    flags = [a for a in seen[0] if a.startswith("--enable-features")]
    assert len(flags) == 1, flags
    assert "WebBluetooth" in flags[0], flags


@pytest.mark.network
@pytest.mark.skipif(not _chrome_available(), reason="Chrome is not installed")
def test_a_profile_has_the_bluetooth_interface_family(monkeypatch, tmp_path):
    """The measurement in the module docstring, as an assertion.

    All three must be present together. navigator.bluetooth without the
    Bluetooth constructor is a shape no browser ships.
    """
    import websocket

    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - handler API
            body = b"<!doctype html><title>bt</title>ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/bt"

    pid = create_profile({"name": "bt", "proxyType": "none", "startUrl": url})["id"]
    try:
        assert L.launch_profile(pid, url=url, direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        time.sleep(2.0)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        target = next(
            t for t in json.loads(urllib.request.urlopen(req, timeout=5).read())
            if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
        )
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True
        )
        expr = ("JSON.stringify({nav: typeof navigator.bluetooth,"
                " ctor: typeof Bluetooth, device: typeof BluetoothDevice})")
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 20
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        got = json.loads(value)
        assert got == {"nav": "object", "ctor": "function", "device": "function"}, got
    finally:
        L.stop_profile(pid)
        srv.shutdown()
        srv.server_close()
