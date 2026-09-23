"""1.6.0 release tests: version coherence, docs presence, security (no secret
leakage in API responses or audit entries), and gateway scaffold status.

Storage is isolated per-test via XDG_* env vars.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from multiprofile import __version__
from multiprofile.api.server import create_app
from multiprofile.audit import read_audit

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_VERSION = "1.6.0"

SECRET_KEY_RE = re.compile(r"password|passwd|passphrase|secret|token|pat", re.I)


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


def _iter_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + "/" + str(k), k
            yield from _iter_keys(v, path + "/" + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_keys(v, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# Version coherence
# ---------------------------------------------------------------------------

def test_version_is_1_6_0():
    assert __version__ == EXPECTED_VERSION


def test_pyproject_version_matches():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["version"] == EXPECTED_VERSION


def test_build_deb_version_matches():
    text = (ROOT / "build-deb.sh").read_text()
    assert f"VERSION={EXPECTED_VERSION}" in text


def test_debian_changelog_top_entry():
    first = (ROOT / "packaging" / "debian" / "changelog").read_text().splitlines()[0]
    assert first.startswith(f"multiprofile-native ({EXPECTED_VERSION})"), first


def test_health_endpoint_reports_version(client):
    body = client.get("/api/health").get_json()
    assert body["version"] == EXPECTED_VERSION


def test_changelog_has_1_6_0_section():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert EXPECTED_VERSION in text


def test_no_stale_version_markers():
    """No leftover 1.5.0/1.4.6 version markers in packaging or source.

    The Debian changelog deliberately keeps its 1.4.6 history entry below the
    new 1.6.0 stanza (upstream history is preserved), so it is excluded here;
    test_debian_changelog_top_entry covers it.
    """
    for rel in ("pyproject.toml", "build-deb.sh", "src/multiprofile/__init__.py"):
        text = (ROOT / rel).read_text()
        assert "1.5.0" not in text, rel
        assert "1.4.6" not in text, rel


# ---------------------------------------------------------------------------
# Docs presence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc", [
    "docs/ARCHITECTURE.md",
    "docs/DEVICE_PRESETS.md",
    "docs/FINGERPRINT_MODEL.md",
    "docs/NETWORK_ISOLATION.md",
    "docs/PROFILE_CONSISTENCY.md",
    "docs/REMOTE_GATEWAYS.md",
])
def test_release_docs_exist(doc):
    assert (ROOT / doc).exists(), doc


def test_docs_reference_1_6_0():
    for doc in ("docs/ARCHITECTURE.md", "docs/DEVICE_PRESETS.md",
                "docs/FINGERPRINT_MODEL.md", "docs/NETWORK_ISOLATION.md",
                "docs/PROFILE_CONSISTENCY.md"):
        assert EXPECTED_VERSION in (ROOT / doc).read_text(), doc


# ---------------------------------------------------------------------------
# Security: no secrets in API responses or audit entries
# ---------------------------------------------------------------------------

@pytest.fixture()
def secret_profile(client):
    """Profile carrying real proxy credentials, so the scan is meaningful."""
    cr = client.post("/api/profiles", json={
        "name": "w5-sec", "deviceId": "pixel-9",
        "proxyType": "custom", "proxyHost": "10.9.9.9", "proxyPort": 18080,
        "proxyUser": "w5user", "proxyPass": "w5-s3cret-pw",
        "startUrl": "about:blank",
    })
    assert cr.status_code == 200
    pid = cr.get_json()["profile"]["id"]
    yield pid, "w5-s3cret-pw"
    client.delete(f"/api/profiles/{pid}?purgeStorage=1")


def _assert_no_secrets(obj, password, where):
    for path, key in _iter_keys(obj):
        assert not SECRET_KEY_RE.search(str(key)), f"{where}: secret-like key {path}"
    assert password not in json.dumps(obj), f"{where}: password value leaked"


def test_api_responses_contain_no_secrets(client, secret_profile):
    pid, password = secret_profile
    payloads = {
        "GET /api/devices": client.get("/api/devices").get_json(),
        "GET /api/devices/pixel-9": client.get("/api/devices/pixel-9").get_json(),
        "GET /api/devices/pixel-9/validate":
            client.get("/api/devices/pixel-9/validate").get_json(),
        f"GET /api/profiles/{pid}/fingerprint":
            client.get(f"/api/profiles/{pid}/fingerprint").get_json(),
        f"GET /api/profiles/{pid}/consistency":
            client.get(f"/api/profiles/{pid}/consistency").get_json(),
        f"POST /api/profiles/{pid}/network/test":
            client.post(f"/api/profiles/{pid}/network/test?timeout=5").get_json(),
    }
    for where, body in payloads.items():
        assert isinstance(body, dict), where
        _assert_no_secrets(body, password, where)


def test_audit_log_contains_no_secrets(client, secret_profile):
    pid, password = secret_profile
    # Exercise the audited routes, then scan every entry written.
    client.get(f"/api/profiles/{pid}/fingerprint")
    client.get(f"/api/profiles/{pid}/consistency")
    client.post(f"/api/profiles/{pid}/network/test?timeout=5")
    entries = read_audit(limit=50)
    assert entries, "expected audit entries from the exercised routes"
    for e in entries:
        detail = e.get("detail")
        for path, key in _iter_keys(detail if isinstance(detail, (dict, list)) else {}):
            assert not SECRET_KEY_RE.search(str(key)), (
                f"audit {e.get('action')}: secret-like key {path}"
            )
    blob = json.dumps(entries)
    assert password not in blob, "audit log leaks the proxy password value"


# ---------------------------------------------------------------------------
# Gateway scaffold status
# ---------------------------------------------------------------------------

def test_azure_gateway_provision_still_not_implemented():
    from multiprofile.gateway.azure import AzureGatewayProvider
    res = AzureGatewayProvider("gw-test", {}).provision()
    assert res["status"] == "not_implemented"
    assert res["ok"] is False


def test_wireguard_gateway_provision_still_not_implemented():
    from multiprofile.gateway.wireguard import WireGuardGatewayProvider
    res = WireGuardGatewayProvider("gw-test", {}).provision()
    assert res["status"] == "not_implemented"
    assert res["ok"] is False
