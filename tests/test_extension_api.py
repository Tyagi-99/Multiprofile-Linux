"""The HTTP surface: a web page cannot install an extension, so every install
path goes through these routes.

The cases that matter here are the ones where a careless API would lie: a bulk
operation answering 200 when a profile failed, a CRX upload accepted as an
install, a Web Store route that will open any URL it is handed.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from multiprofile.api.server import create_app

HEADERS = {"Host": "localhost", "Origin": "http://localhost",
           "Content-Type": "application/json"}

#: Multipart is allowed on the upload route only with this header, which an
#: HTML form cannot set. See multiprofile/api/auth.py.
UPLOAD_HEADERS = {"Host": "localhost", "Origin": "http://localhost",
                  "X-Multiprofile-Upload": "1"}

MANIFEST = {
    "manifest_version": 3,
    "name": "API Fixture",
    "version": "0.9.0",
    "permissions": ["storage"],
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    from multiprofile.storage import db

    db.close()
    app = create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    db.close()


def _zip(tmp_path, name="api.zip", **overrides):
    data = dict(MANIFEST)
    data.update(overrides)
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", json.dumps(data))
    return str(p)


def _import(client, tmp_path, **overrides):
    res = client.post("/api/extensions/import",
                      json={"zipPath": _zip(tmp_path, **overrides)},
                      headers=HEADERS)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["extension"]


def _profile(client, name="p", **extra):
    res = client.post("/api/profiles",
                      json={"name": name, "proxyType": "none", **extra},
                      headers=HEADERS)
    assert res.status_code in (200, 201), res.get_json()
    body = res.get_json()
    return (body.get("profile") or body)["id"]


# ---------------------------------------------------------------------------
# capability
# ---------------------------------------------------------------------------

def test_capability_is_reported_with_a_reason(client):
    body = client.get("/api/extensions/capability", headers=HEADERS).get_json()
    assert body["ok"] is True
    cap = body["capability"]
    assert "unpackedLoading" in cap
    assert cap["reason"], "a capability with no reason is not auditable"
    if not cap["unpackedLoading"]:
        assert cap["remediation"], "an unsupported browser must say what would work"


# ---------------------------------------------------------------------------
# listing and import
# ---------------------------------------------------------------------------

def test_the_list_is_empty_before_anything_is_imported(client):
    body = client.get("/api/extensions", headers=HEADERS).get_json()
    assert body["ok"] is True
    assert body["extensions"] == []
    assert "capability" in body


def test_preview_does_not_create_anything(client, tmp_path):
    res = client.post("/api/extensions/preview",
                      json={"zipPath": _zip(tmp_path)}, headers=HEADERS)
    assert res.status_code == 200
    body = res.get_json()
    assert body["manifest"]["name"] == "API Fixture"
    assert body["manifest"]["permissions"] == ["storage"]
    after = client.get("/api/extensions", headers=HEADERS).get_json()
    assert after["extensions"] == [], "preview must not import"


def test_import_reports_the_package_state_not_installed(client, tmp_path):
    ext = _import(client, tmp_path)
    assert ext["packageState"] == "imported"
    body = client.get("/api/extensions", headers=HEADERS).get_json()
    row = body["extensions"][0]
    assert row["assignedProfileCount"] == 0
    assert row["verifiedProfileCount"] == 0


def test_a_bad_manifest_is_a_400_with_a_code(client, tmp_path):
    res = client.post("/api/extensions/import",
                      json={"zipPath": _zip(tmp_path, manifest_version=1)},
                      headers=HEADERS)
    assert res.status_code == 400
    body = res.get_json()
    assert body["ok"] is False
    assert body["code"] == "manifest_version_unsupported"


def test_import_requires_a_source(client):
    res = client.post("/api/extensions/import", json={}, headers=HEADERS)
    assert res.status_code == 400
    assert res.get_json()["code"] == "bad_request"


def test_an_unknown_extension_is_a_404(client):
    res = client.get("/api/extensions/nope", headers=HEADERS)
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------

def test_an_uploaded_zip_is_imported(client, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST))
    buf.seek(0)
    res = client.post(
        "/api/extensions/upload",
        data={"file": (buf, "uploaded.zip")},
        content_type="multipart/form-data",
        headers=UPLOAD_HEADERS,
    )
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["extension"]["name"] == "API Fixture"


def test_an_uploaded_crx_is_refused_with_the_reason(client):
    """Unzipping a signed container and loading it unpacked would discard the
    signature and change the ID. Calling that an install would be a false
    provenance claim."""
    res = client.post(
        "/api/extensions/upload",
        data={"file": (io.BytesIO(b"Cr24\x00\x00\x00\x03"), "thing.crx")},
        content_type="multipart/form-data",
        headers=UPLOAD_HEADERS,
    )
    assert res.status_code == 400
    body = res.get_json()
    assert body["code"] == "crx_unsupported"
    assert "Web Store" in body["error"]


def test_an_upload_with_no_file_is_refused(client):
    res = client.post("/api/extensions/upload", data={},
                      content_type="multipart/form-data",
                      headers=UPLOAD_HEADERS)
    assert res.status_code == 400


def test_a_multipart_upload_without_the_fetch_only_header_is_refused():
    """The JSON content-type requirement is a CSRF defence: a cross-origin
    HTML form can POST multipart and cannot POST JSON. The upload route is
    allowed multipart only with a header no form can set, which forces a CORS
    preflight and restores exactly that protection."""
    import io as _io
    import zipfile as _zip

    import pytest as _pytest

    from multiprofile.api.server import create_app as _create

    monkey = _pytest.MonkeyPatch()
    try:
        import tempfile

        tmp = tempfile.mkdtemp()
        monkey.setenv("XDG_DATA_HOME", tmp + "/d")
        monkey.setenv("XDG_CACHE_HOME", tmp + "/c")
        monkey.setenv("XDG_CONFIG_HOME", tmp + "/g")
        monkey.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
        monkey.delenv("MULTIPROFILE_API_TOKEN", raising=False)
        from multiprofile.storage import db

        db.close()
        app = _create()
        app.config["TESTING"] = True
        c = app.test_client()
        buf = _io.BytesIO()
        with _zip.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(MANIFEST))
        buf.seek(0)
        res = c.post("/api/extensions/upload",
                     data={"file": (buf, "x.zip")},
                     content_type="multipart/form-data",
                     headers={"Host": "localhost", "Origin": "http://localhost"})
        assert res.status_code == 403
        assert res.get_json()["error"] == "bad_content_type"
        db.close()
    finally:
        monkey.undo()


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

def test_resolve_profiles_returns_the_exact_target_list(client):
    a = _profile(client, "alpha", tags=["x"])
    _profile(client, "beta", tags=["y"])
    res = client.post("/api/extensions/resolve-profiles",
                      json={"search": "alph", "selectAllMatching": True},
                      headers=HEADERS)
    body = res.get_json()
    assert body["count"] == 1
    assert [p["id"] for p in body["profiles"]] == [a]


def test_resolve_profiles_reports_missing_ids(client):
    a = _profile(client, "only")
    res = client.post("/api/extensions/resolve-profiles",
                      json={"profileIds": [a, "profile_missing"]}, headers=HEADERS)
    body = res.get_json()
    assert body["missingProfileIds"] == ["profile_missing"]
    assert body["count"] == 1


# ---------------------------------------------------------------------------
# assignment
# ---------------------------------------------------------------------------

def test_assignment_reports_one_result_per_profile(client, tmp_path):
    ext = _import(client, tmp_path)
    pids = [_profile(client, f"a{i}") for i in range(3)]
    res = client.post(f"/api/extensions/{ext['id']}/assign",
                      json={"profileIds": pids}, headers=HEADERS)
    body = res.get_json()
    if not body["ok"]:
        # On a browser that cannot load unpacked extensions the honest answer
        # is a failure with a reason, not a silent success.
        assert res.status_code in (207, 400)
        assert all(not r["ok"] for r in body["results"])
        return
    assert res.status_code == 200
    assert body["total"] == 3
    assert body["succeeded"] == 3
    assert len(body["results"]) == 3
    assert body["failedProfileIds"] == []


def test_a_partial_failure_is_207_and_not_ok(client, tmp_path):
    """The specific bug this status exists to prevent: a console calling the
    whole batch a success because the HTTP status was 200."""
    ext = _import(client, tmp_path)
    good = _profile(client, "good")
    res = client.post(f"/api/extensions/{ext['id']}/assign",
                      json={"profileIds": [good, "profile_does_not_exist"]},
                      headers=HEADERS)
    body = res.get_json()
    assert body["ok"] is False, body
    if body.get("succeeded"):
        assert res.status_code == 207, body
        # The unknown profile is reported as missing rather than failed -- it
        # is not a failed install, it does not exist -- but the response is
        # still not a success, because the caller asked for two and got one.
        assert body["missingProfileIds"] == ["profile_does_not_exist"]
        assert "no longer exist" in body["note"]
    else:
        # unsupported browser: everything failed, which is also not a success
        assert res.status_code == 400


def test_unassign_requires_a_profile(client, tmp_path):
    ext = _import(client, tmp_path)
    res = client.post(f"/api/extensions/{ext['id']}/unassign", json={},
                      headers=HEADERS)
    assert res.status_code == 400


def test_enabling_an_unassigned_profile_is_a_404(client, tmp_path):
    ext = _import(client, tmp_path)
    pid = _profile(client, "nope")
    res = client.post(f"/api/extensions/{ext['id']}/enabled",
                      json={"profileId": pid, "enabled": False}, headers=HEADERS)
    assert res.status_code == 404


def test_retry_with_nothing_failed_says_so(client, tmp_path):
    ext = _import(client, tmp_path)
    res = client.post(f"/api/extensions/{ext['id']}/retry", json={},
                      headers=HEADERS)
    body = res.get_json()
    assert body["ok"] is True
    assert body["total"] == 0
    assert "No failed profiles" in body["note"]


# ---------------------------------------------------------------------------
# default for new profiles
# ---------------------------------------------------------------------------

def test_default_for_new_says_it_does_not_reach_existing_profiles(client, tmp_path):
    ext = _import(client, tmp_path)
    existing = _profile(client, "already-here")
    res = client.post(f"/api/extensions/{ext['id']}/default-for-new",
                      json={"enabled": True}, headers=HEADERS)
    body = res.get_json()
    assert body["extension"]["defaultForNewProfiles"] is True
    assert "Existing profiles are unchanged" in body["note"]
    detail = client.get(f"/api/extensions/{ext['id']}",
                        headers=HEADERS).get_json()["extension"]
    assert all(a["profileId"] != existing for a in detail["assignments"])


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def test_deleting_an_assigned_extension_is_a_409(client, tmp_path):
    ext = _import(client, tmp_path)
    pid = _profile(client, "holder")
    client.post(f"/api/extensions/{ext['id']}/assign",
                json={"profileIds": [pid]}, headers=HEADERS)
    detail = client.get(f"/api/extensions/{ext['id']}",
                        headers=HEADERS).get_json()["extension"]
    if not detail["assignments"]:
        pytest.skip("this browser cannot load unpacked extensions, so nothing "
                    "was assigned to refuse the delete over")
    res = client.delete(f"/api/extensions/{ext['id']}", headers=HEADERS)
    assert res.status_code == 409
    assert res.get_json()["code"] == "still_assigned"


def test_forced_delete_succeeds(client, tmp_path):
    ext = _import(client, tmp_path)
    pid = _profile(client, "holder")
    client.post(f"/api/extensions/{ext['id']}/assign",
                json={"profileIds": [pid]}, headers=HEADERS)
    res = client.delete(f"/api/extensions/{ext['id']}?force=1", headers=HEADERS)
    assert res.status_code == 200, res.get_json()
    assert client.get("/api/extensions",
                      headers=HEADERS).get_json()["extensions"] == []


# ---------------------------------------------------------------------------
# the Web Store route
# ---------------------------------------------------------------------------

def test_the_webstore_route_refuses_a_third_party_mirror(client):
    """The supported flow is the browser's own. A route that opens any URL it
    is given is how a 'CRX downloader' ends up in the install path."""
    pid = _profile(client, "ws")
    res = client.post("/api/extensions/webstore",
                      json={"profileId": pid,
                            "url": "https://crx-downloader.example/get?id=abc"},
                      headers=HEADERS)
    assert res.status_code == 400
    assert "chromewebstore.google.com" in res.get_json()["error"]


def test_the_webstore_route_refuses_a_malformed_extension_id(client):
    pid = _profile(client, "ws2")
    res = client.post("/api/extensions/webstore",
                      json={"profileId": pid, "webstoreId": "not-an-id"},
                      headers=HEADERS)
    assert res.status_code == 400


def test_the_webstore_route_needs_a_running_profile(client):
    """A Web Store install needs the operator in front of the window, so this
    reports rather than launching a browser behind their back."""
    pid = _profile(client, "ws3")
    res = client.post(
        "/api/extensions/webstore",
        json={"profileId": pid, "webstoreId": "a" * 32},
        headers=HEADERS)
    assert res.status_code == 409
    body = res.get_json()
    assert body["ok"] is False
    assert "not running" in body["error"]


def test_the_extensions_page_route_needs_a_running_profile(client):
    pid = _profile(client, "ep")
    res = client.post(f"/api/profiles/{pid}/extensions-page", json={},
                      headers=HEADERS)
    assert res.status_code == 409
    assert "not running" in res.get_json()["error"]


# ---------------------------------------------------------------------------
# the controlled restart
# ---------------------------------------------------------------------------

def test_restart_requires_explicit_profile_ids(client, tmp_path):
    """A restart closes whatever the operator had open, so this route never
    decides for itself which profiles are "pending enough" to restart."""
    ext = _import(client, tmp_path)
    res = client.post(f"/api/extensions/{ext['id']}/restart-profiles",
                      json={}, headers=HEADERS)
    assert res.status_code == 400
    body = res.get_json()
    assert body["code"] == "bad_request"
    assert "does not restart every pending profile" in body["error"]


def test_restarting_an_unknown_profile_fails_only_that_profile(client, tmp_path):
    ext = _import(client, tmp_path)
    res = client.post(f"/api/extensions/{ext['id']}/restart-profiles",
                      json={"profileIds": ["profile_nope"]}, headers=HEADERS)
    assert res.status_code == 400
    body = res.get_json()
    assert body["ok"] is False
    assert body["total"] == 1
    assert body["results"][0]["detail"] == "No such profile."
    assert body["failedProfileIds"] == ["profile_nope"]
