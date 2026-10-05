"""Route tests for Worker 3 — network_routes.register_network_routes.

Throwaway Flask app; storage and diagnostics are stubbed so these tests are
hermetic (no network, no disk).
"""
from __future__ import annotations

from flask import Flask

import multiprofile.network_routes as routes
from multiprofile.network import NetworkDiagnostics

ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)

PROFILE = {
    "id": "p1",
    "name": "Test",
    "userAgent": ANDROID_UA,
    "isMobile": True,
    "timezone": "America/New_York",
    "language": "en-US",
    "networkConfig": {"mode": "DIRECT"},
    "fingerprint": {
        "ch_platform": "Android",
        "ch_brands": [
            {"brand": "Google Chrome", "version": "131"},
            {"brand": "Chromium", "version": "131"},
        ],
        "screen": {"w": 412, "h": 915, "dpr": 2.625},
        "touch": 5,
        "isMobile": True,
        "gpu": {"vendor": "Google Inc. (Qualcomm)", "renderer": "ANGLE (Qualcomm, Adreno (TM) 740, OpenGL ES 3.2)"},
    },
}


def _make_app(monkeypatch, profile=PROFILE, diag=None, fail=False):
    monkeypatch.setattr(routes, "get_profile",
                        lambda pid: profile if profile and pid == profile["id"] else None)

    def fake_run_diagnostics(net, *, profile_id="", timeout=10.0):
        if fail:
            raise RuntimeError("boom")
        return diag or NetworkDiagnostics(
            profile_id=profile_id, mode="DIRECT", health="HEALTHY",
            egress_ip="203.0.113.7", country="United States",
            country_code="US", timezone="America/New_York",
            latency_ms=120.5,
            dns={"status": "PASS", "detail": "ok"},
            ipv6={"status": "UNAVAILABLE", "detail": "no route"},
        )

    monkeypatch.setattr(routes, "run_diagnostics", fake_run_diagnostics)
    # Avoid touching the real audit log on disk.
    monkeypatch.setattr(routes, "audit_log", lambda *a, **k: None)

    app = Flask(__name__)
    routes.register_network_routes(app)
    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------- 404 cases
def test_network_test_404_unknown_profile(monkeypatch):
    client = _make_app(monkeypatch, profile=None)
    resp = client.post("/api/profiles/nope/network/test")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "profile not found"}


def test_consistency_404_unknown_profile(monkeypatch):
    client = _make_app(monkeypatch, profile=None)
    resp = client.get("/api/profiles/nope/consistency")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "profile not found"}


# ---------------------------------------------------------------- contracts
def test_network_test_200_shape(monkeypatch):
    client = _make_app(monkeypatch)
    resp = client.post("/api/profiles/p1/network/test")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profileId"] == "p1"
    assert body["health"] == "HEALTHY"
    assert body["egressIp"] == "203.0.113.7"
    assert body["dns"]["status"] == "PASS"


def test_network_test_500_shape(monkeypatch):
    client = _make_app(monkeypatch, fail=True)
    resp = client.post("/api/profiles/p1/network/test")
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "boom"}


def test_consistency_200_shape(monkeypatch):
    client = _make_app(monkeypatch)
    resp = client.get("/api/profiles/p1/consistency")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile_id"] == "p1"
    assert body["status"] in ("PASS", "WARNING", "ERROR")
    domains = [c["domain"] for c in body["checks"]]
    assert domains == ["geo", "device", "platform", "mobile", "browser",
                       "gpu_webgl", "webgpu"]
    # Profile has no deviceId -> graceful WARNING, not a crash.
    device = next(c for c in body["checks"] if c["domain"] == "device")
    assert device["status"] == "WARNING"
    assert "device preset unavailable" in device["message"]


def test_consistency_with_real_device_preset(monkeypatch):
    profile = dict(PROFILE)
    profile["deviceId"] = "pixel-9"
    client = _make_app(monkeypatch, profile=profile)
    resp = client.get("/api/profiles/p1/consistency")
    assert resp.status_code == 200
    body = resp.get_json()
    device = next(c for c in body["checks"] if c["domain"] == "device")
    assert "device preset unavailable" not in device["message"]
    mobile = next(c for c in body["checks"] if c["domain"] == "mobile")
    assert mobile["status"] == "PASS"


def test_responses_contain_no_password_keys(monkeypatch):
    client = _make_app(monkeypatch)
    for resp in (
        client.post("/api/profiles/p1/network/test"),
        client.get("/api/profiles/p1/consistency"),
        client.post("/api/profiles/nope/network/test"),
    ):
        raw = resp.get_data(as_text=True).lower()
        assert "password" not in raw
        assert "passphrase" not in raw
        assert "proxypass" not in raw


def test_consistency_error_shape_has_profile_id(monkeypatch):
    bad = dict(PROFILE)
    bad["fingerprint"] = dict(PROFILE["fingerprint"])
    bad["fingerprint"]["ch_platform"] = "iOS"  # Android UA + iOS hints -> ERROR
    client = _make_app(monkeypatch, profile=bad)
    resp = client.get("/api/profiles/p1/consistency")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ERROR"
