"""1.6.0 integration tests: devices + fingerprint + diagnostics + consistency
end-to-end through the real Flask app, plus launcher-hook exception safety.

Storage is isolated per-test via XDG_* env vars (the xdg module resolves
them lazily). Browser-dependent assertions pytest.skip when no working
Chromium binary is on the host; nothing is faked.
"""
from __future__ import annotations

import ipaddress
import os
import time

import pytest

from multiprofile.api.server import create_app
from multiprofile.devices.validator import find_chromium_binary

CHROMIUM_BIN, _ = find_chromium_binary()
HAS_CHROMIUM = CHROMIUM_BIN is not None
HAS_DISPLAY = bool(os.environ.get("DISPLAY"))


@pytest.fixture()
def xdg_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))


@pytest.fixture()
def client(xdg_isolated):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def device_profile(client):
    """A real profile carrying deviceId=pixel-9, deleted after the test."""
    cr = client.post("/api/profiles", json={
        "name": "w5-int-pixel9",
        "deviceId": "pixel-9",
        "proxyType": "none",
        "startUrl": "about:blank",
    })
    assert cr.status_code == 200, cr.data
    pid = cr.get_json()["profile"]["id"]
    yield pid
    client.delete(f"/api/profiles/{pid}?purgeStorage=1")


# ---------------------------------------------------------------------------
# End-to-end API: profile with deviceId
# ---------------------------------------------------------------------------

def test_device_profile_fingerprint_preset_coherent(client, device_profile):
    """The fingerprint report's inspector components honour the device preset."""
    r = client.get(f"/api/profiles/{device_profile}/fingerprint")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    comps = {c["name"]: c for c in body["components"]}

    ua = comps["Navigator"]["expected"]["userAgent"]
    assert "Android" in ua and "Pixel 9" in ua, ua

    scr = comps["Screen"]["expected"]
    assert (scr["width"], scr["height"]) == (411, 923)
    assert scr["devicePixelRatio"] == pytest.approx(2.626)

    ch = comps["Client Hints"]["expected"]
    assert ch["platform"] == "Android"
    assert ch["mobile"] is True
    assert ch["model"] == "Pixel 9"

    assert comps["Touch"]["expected"]["maxTouchPoints"]


def test_consistency_returns_per_domain_checks(client, device_profile):
    r = client.get(f"/api/profiles/{device_profile}/consistency")
    assert r.status_code == 200
    body = r.get_json()
    assert body["profile_id"] == device_profile
    assert body["status"] in ("PASS", "WARNING", "ERROR")
    domains = {c["domain"] for c in body["checks"]}
    assert {"geo", "device", "platform", "mobile", "browser", "gpu_webgl"} <= domains
    for c in body["checks"]:
        assert c["status"] in ("PASS", "WARNING", "ERROR"), c
        assert c["domain"]
        assert isinstance(c.get("message"), str) and c["message"]


@pytest.mark.network
def test_network_test_honest_diagnostics_shape(client, device_profile):
    """Diagnostics are real measurements or honest DOWN+error — never placeholders."""
    r = client.post(f"/api/profiles/{device_profile}/network/test?timeout=6")
    assert r.status_code == 200
    body = r.get_json()
    assert {"profileId", "mode", "health", "egressIp", "error"} <= set(body)
    assert body["health"] in ("HEALTHY", "DEGRADED", "DOWN")
    if body["health"] != "HEALTHY":
        assert body["error"], "non-HEALTHY diagnostics must carry the real error"
    else:
        assert body["egressIp"], "HEALTHY diagnostics must carry a measured egress IP"
    ip = body.get("egressIp")
    if ip is not None:
        # raises on placeholder-shaped garbage like "0.0.0.0"/"N/A"
        parsed = ipaddress.ip_address(ip)
        assert not parsed.is_unspecified, f"placeholder IP leaked: {ip}"


def test_unknown_ids_404_json(client):
    assert client.get("/api/profiles/no-such/fingerprint").status_code == 404
    assert client.get("/api/profiles/no-such/fingerprint").get_json()["error"]
    assert client.get("/api/profiles/no-such/consistency").status_code == 404
    assert client.post("/api/profiles/no-such/network/test").status_code == 404
    assert client.get("/api/devices/no-such-device").status_code == 404
    assert client.get("/api/devices/no-such-device").get_json()["error"]


# ---------------------------------------------------------------------------
# Launcher hook: exception safety
# ---------------------------------------------------------------------------

def test_launch_without_chrome_fails_gracefully_not_hang(client, monkeypatch):
    """No Chromium binary -> immediate graceful error, never a hang."""
    import multiprofile.browser.launcher as L

    def _no_chrome():
        raise FileNotFoundError("no chromium on PATH (test)")

    monkeypatch.setattr(L, "find_chrome", _no_chrome)
    cr = client.post("/api/profiles", json={"name": "w5-nochrome", "proxyType": "none"})
    pid = cr.get_json()["profile"]["id"]
    try:
        t0 = time.monotonic()
        res = L.launch_profile(pid, url="about:blank", direct=True)
        dt = time.monotonic() - t0
        assert res.get("ok") is False
        assert "dependency_missing" in res.get("errors", [])
        assert dt < 15, f"launch without Chrome must fail fast, not hang ({dt:.1f}s)"
    finally:
        client.delete(f"/api/profiles/{pid}?purgeStorage=1")


def test_launch_unknown_device_id_records_note_not_traceback(client, monkeypatch, tmp_path):
    """Unknown deviceId -> 'device preset not found' note; no traceback, no crash.

    The browser spawn is faked (no real Chromium needed): the point under
    test is the device-emulation block's exception safety once CDP is up.
    """
    import multiprofile.browser.cdp_identity as C
    import multiprofile.browser.launcher as L

    monkeypatch.setattr(L, "find_chrome", lambda: "/nonexistent/chrome")
    monkeypatch.setattr(L, "_prepare_extension", lambda profile, fp: tmp_path / "ext")

    class _FakeStderr:
        def read(self):
            return b""

        def close(self):
            pass

    class _FakeProc:
        def __init__(self, *a, **k):
            self.pid = 987654321
            self.stderr = _FakeStderr()

        def poll(self):
            return None

    monkeypatch.setattr(L.subprocess, "Popen", _FakeProc)
    monkeypatch.setattr(L, "wait_for_cdp", lambda port, timeout_s=8.0: True)
    monkeypatch.setattr(C, "apply_ua_override", lambda port, fp, ua: None)

    cr = client.post("/api/profiles", json={
        "name": "w5-unknown-dev", "deviceId": "no-such-device", "proxyType": "none",
    })
    pid = cr.get_json()["profile"]["id"]
    try:
        res = L.launch_profile(pid, url="about:blank", direct=True)
        notes = res.get("notes") or []
        assert any("device preset not found: no-such-device" in n for n in notes), notes
        assert not any("Traceback" in n for n in notes), notes
    finally:
        L.stop_profile(pid)
        client.delete(f"/api/profiles/{pid}?purgeStorage=1")


@pytest.mark.skipif(not (HAS_CHROMIUM and HAS_DISPLAY),
                    reason="needs a working Chromium binary AND an X display")
def test_launch_device_emulation_persists_after_launch_returns(client):
    """Regression: one-shot CDP sessions lose UA/touch overrides.

    Chromium scopes Network.setUserAgentOverride and
    Emulation.setTouchEmulationEnabled to the DevTools session that set
    them — once that session closes, page readback reverts to host values
    (only Emulation.setDeviceMetricsOverride survives a disconnect). The
    launcher must therefore hold the emulation session open for the
    browser's lifetime. This test reads back on a FRESH CDP connection
    made after launch_profile returned: with the old one-shot code the
    touch/UA assertions fail; with the held session they pass. Nothing is
    faked: real headed launch under Xvfb when available, skipped otherwise.
    """
    import json
    import urllib.parse
    import websocket

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.devices.validator import _http_json

    cr = client.post("/api/profiles", json={
        "name": "w5-live-persist", "deviceId": "pixel-9", "proxyType": "none",
    })
    pid = cr.get_json()["profile"]["id"]
    html = ("<html><head>"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "</head><body>x</body></html>")
    try:
        res = L.launch_profile(
            pid, url="data:text/html," + urllib.parse.quote(html), direct=True)
        assert res.get("ok") is True, res.get("error")
        port = get_runtime(pid).cdp_port
        assert port, "launcher must record the CDP port in runtime state"
        time.sleep(1)
        targets = _http_json("GET", f"http://127.0.0.1:{port}/json/list")
        page = next(
            t for t in targets
            if t.get("type") == "page" and (t.get("url") or "").startswith("data:"))
        ws = websocket.WebSocket()
        try:
            ws.connect(page["webSocketDebuggerUrl"], timeout=10)
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate", "params": {
                    "expression": ("[window.innerWidth, window.innerHeight, "
                                   "navigator.maxTouchPoints, navigator.userAgent]"),
                    "returnByValue": True}}))
            val = None
            deadline = time.time() + 10
            while time.time() < deadline and val is None:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    val = msg["result"]["result"]["value"]
        finally:
            ws.close()
        assert val is not None, "no CDP readback from the launched page"
        w, h, touch, ua = val
        assert abs(w - 411) <= 2 and abs(h - 923) <= 2, f"viewport not emulated: {val}"
        assert touch and touch > 0, f"touch emulation lost after launch: {val}"
        assert "Android" in ua and "Pixel 9" in ua, f"UA override lost after launch: {ua!r}"
    finally:
        L.stop_profile(pid)
        client.delete(f"/api/profiles/{pid}?purgeStorage=1")


@pytest.mark.skipif(not (HAS_CHROMIUM and HAS_DISPLAY),
                    reason="needs a working Chromium binary AND an X display")
def test_launch_device_emulation_note_present_with_chrome(client):
    """With a real binary, a deviceId launch records the emulation note.

    Runs a real headed launch under Xvfb when available; skipped otherwise.
    Kept minimal: launch, check the note, stop, delete.
    """
    import multiprofile.browser.launcher as L

    cr = client.post("/api/profiles", json={
        "name": "w5-live-dev", "deviceId": "pixel-9", "proxyType": "none",
        "startUrl": "about:blank",
    })
    pid = cr.get_json()["profile"]["id"]
    try:
        res = L.launch_profile(pid, url="about:blank", direct=True)
        assert res.get("ok") is True, res.get("error")
        notes = res.get("notes") or []
        assert any("device emulation" in n for n in notes), notes
    finally:
        L.stop_profile(pid)
        client.delete(f"/api/profiles/{pid}?purgeStorage=1")
