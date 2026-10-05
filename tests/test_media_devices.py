"""Media devices: spoofing must not volunteer what the host withholds.

``enumerateDevices`` used to be rewritten unconditionally. Every device of a
given kind got a stable 64-char id derived from the profile seed, whatever the
browser had actually returned. Two things were wrong with that, and both made
a profile *more* identifiable than leaving the surface alone.

**It handed out an identifier where Chrome hands out none.** Measured on stock
Chromium 151, a loopback page with no media permission::

    BARE:  audioinput / videoinput / audiooutput   deviceId=''  label=''  groupId=''
    OURS:  all three                               deviceId='a2118a3d…'  label=''  groupId=''

Chrome withholds device ids until permission is granted. A profile that
answers with a stable id beside an empty label and an empty groupId is in a
state no real browser produces — and it is a persistent cross-site identifier,
volunteered, for the life of the profile. The host's own behaviour leaked
nothing here; the spoofing is what leaked.

**And the three ids were the same.** All of them came from ``_rng(seed, 20)``,
so a microphone, a camera and a speaker shared one identifier. No browser has
ever done that.

So the rule is: substitute only what the browser is already exposing, and when
it exposes nothing, expose nothing.
"""
from __future__ import annotations

import pytest

from multiprofile.fingerprint import build_fingerprint

# --------------------------------------------------------------------------
# 1. The derivation
# --------------------------------------------------------------------------

def test_the_three_device_ids_differ():
    """One id for a mic, a camera and a speaker is not a fingerprint."""
    ids = build_fingerprint({"id": "p1"})["mediaDeviceIds"]
    assert len(set(ids.values())) == 3, ids


def test_device_ids_are_stable_for_a_profile():
    a = build_fingerprint({"id": "p1"})["mediaDeviceIds"]
    b = build_fingerprint({"id": "p1"})["mediaDeviceIds"]
    assert a == b


def test_device_ids_differ_between_profiles():
    a = build_fingerprint({"id": "p1"})["mediaDeviceIds"]
    b = build_fingerprint({"id": "p2"})["mediaDeviceIds"]
    assert set(a.values()).isdisjoint(set(b.values()))


def test_group_ids_describe_a_plausible_machine():
    """Chrome groups one physical unit together.

    A webcam's camera and microphone share a groupId. Three devices in three
    groups, or all three in one, are both shapes that invite a second look.
    """
    groups = build_fingerprint({"id": "p1"})["mediaDeviceGroupIds"]
    assert groups["audioinput"] == groups["audiooutput"]
    assert groups["videoinput"] != groups["audioinput"]


def test_group_ids_are_not_the_device_ids():
    fp = build_fingerprint({"id": "p1"})
    assert set(fp["mediaDeviceGroupIds"].values()).isdisjoint(
        set(fp["mediaDeviceIds"].values())
    )


def test_ids_look_like_the_real_thing():
    """Chrome's ids are 64 hex characters."""
    fp = build_fingerprint({"id": "p1"})
    for value in {**fp["mediaDeviceIds"], **fp["mediaDeviceGroupIds"]}.values():
        assert len(value) == 64, value
        assert all(c in "0123456789abcdef" for c in value), value


# --------------------------------------------------------------------------
# 2. Live: the page must see what a stock browser would
# --------------------------------------------------------------------------

def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


@pytest.mark.network
@pytest.mark.skipif(not _chrome_available(), reason="Chrome is not installed")
def test_a_profile_volunteers_no_device_id_without_permission(monkeypatch, tmp_path):
    """The measurement in the module docstring, as an assertion.

    Without media permission stock Chromium returns ``deviceId=''``. So must
    we -- an id here is an identifier the host would never have given out.
    """
    import json
    import threading
    import time
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import websocket

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - handler API
            body = b"<!doctype html><title>m</title>ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/m"

    pid = create_profile({"name": "media", "proxyType": "none", "startUrl": url})["id"]
    try:
        assert L.launch_profile(pid, url=url, direct=True).get("ok") is True
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
        expr = (
            "(async()=>{const ds=await navigator.mediaDevices.enumerateDevices();"
            "return JSON.stringify(ds.map(d=>({kind:d.kind,deviceId:String(d.deviceId),"
            "label:String(d.label),groupId:String(d.groupId)})));})()"
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expr, "awaitPromise": True, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 25
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        devices = json.loads(value)
        # A machine with no camera and no microphone enumerates nothing, and
        # there is then no device list for this product to have got wrong.
        # That is an absent input, not a failing assertion -- and reporting
        # it as one would make a headless runner look like a product defect.
        if not devices:
            pytest.skip(
                "this host enumerates no media devices at all; there is "
                "nothing for the spoof to be measured against"
            )

        for dev in devices:
            # An id beside an empty label and an empty group is the giveaway.
            if not dev["label"] and not dev["groupId"]:
                assert dev["deviceId"] == "", (
                    f"volunteered a device id the host would have withheld: {dev}"
                )
    finally:
        L.stop_profile(pid)
        srv.shutdown()
        srv.server_close()
