"""Routes for pools, workspaces and notes — including what must never be returned."""
from __future__ import annotations

import pytest

from multiprofile.api.server import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _make_pool(client, **kw):
    body = {
        "label": "US",
        "members": [
            {"label": "a", "scheme": "socks5", "host": "198.51.100.1", "port": 1080},
            {"label": "b", "scheme": "socks5", "host": "198.51.100.2", "port": 1080},
        ],
        **kw,
    }
    r = client.post("/api/pools", json=body)
    assert r.status_code == 200, r.data
    return r.get_json()["pool"]


# ---------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------


def test_pool_crud_round_trip(client):
    pool = _make_pool(client)
    pid = pool["id"]

    assert client.get("/api/pools").get_json()[0]["id"] == pid
    assert client.get(f"/api/pools/{pid}").get_json()["label"] == "US"

    r = client.put(f"/api/pools/{pid}", json={"label": "EU"})
    assert r.get_json()["pool"]["label"] == "EU"

    assert client.delete(f"/api/pools/{pid}").status_code == 200
    assert client.get(f"/api/pools/{pid}").status_code == 404
    assert client.delete(f"/api/pools/{pid}").status_code == 404


def test_a_member_password_is_never_returned(client):
    pool = _make_pool(client, members=[{
        "label": "a", "scheme": "socks5", "host": "198.51.100.1",
        "port": 1080, "username": "u", "password": "s3cret",
    }])

    listed = client.get("/api/pools").data.decode()
    detail = client.get(f"/api/pools/{pool['id']}").data.decode()

    assert "s3cret" not in listed
    assert "s3cret" not in detail
    assert "s3cret" not in client.post(f"/api/pools/{pool['id']}", json={}).data.decode()
    member = client.get(f"/api/pools/{pool['id']}").get_json()["members"][0]
    assert member["hasPassword"] is True
    assert "password" not in member


def test_assign_records_a_member_and_is_sticky(client):
    pool = _make_pool(client)
    first = client.post(f"/api/pools/{pool['id']}/assign", json={"profileId": "p1"}).get_json()
    again = client.post(f"/api/pools/{pool['id']}/assign", json={"profileId": "p1"}).get_json()

    assert first["ok"] is True
    assert again["member"]["id"] == first["member"]["id"]
    assert "password" not in again["member"]


def test_assign_reports_an_unassignable_pool_as_409(client):
    pool = _make_pool(client, members=[])
    r = client.post(f"/api/pools/{pool['id']}/assign", json={"profileId": "p1"})

    assert r.status_code == 409
    assert "no enabled member" in r.get_json()["message"]


def test_assign_requires_a_profile_id(client):
    pool = _make_pool(client)
    assert client.post(f"/api/pools/{pool['id']}/assign", json={}).status_code == 400


def test_release_drops_the_assignment(client):
    pool = _make_pool(client)
    client.post(f"/api/pools/{pool['id']}/assign", json={"profileId": "p1"})

    r = client.post(f"/api/pools/{pool['id']}/release", json={"profileId": "p1"})

    assert r.get_json()["released"] is True
    assert client.get(f"/api/pools/{pool['id']}").get_json()["assignments"] == {}


def test_usage_counts_come_back_with_the_list(client):
    pool = _make_pool(client)
    for i in range(4):
        client.post(f"/api/pools/{pool['id']}/assign", json={"profileId": f"p{i}"})

    [listed] = client.get("/api/pools").get_json()

    assert listed["profileCount"] == 4
    assert sorted(listed["usage"].values()) == [2, 2]


# ---------------------------------------------------------------------------
# workspaces
# ---------------------------------------------------------------------------


def test_a_profile_inherits_its_workspaces_pool(client):
    pool = _make_pool(client)
    ws = client.post("/api/clients", json={"name": "Acme", "defaultPoolId": pool["id"]}).get_json()
    ws_id = (ws.get("client") or ws)["id"]

    created = client.post("/api/profiles", json={"name": "Inheritor", "clientId": ws_id})

    assert created.get_json()["profile"]["poolId"] == pool["id"]


def test_an_explicit_pool_beats_the_workspace_default(client):
    a = _make_pool(client, label="A")
    b = _make_pool(client, label="B")
    ws = client.post("/api/clients", json={"name": "Acme", "defaultPoolId": a["id"]}).get_json()
    ws_id = (ws.get("client") or ws)["id"]

    created = client.post("/api/profiles", json={"name": "P", "clientId": ws_id, "poolId": b["id"]})

    assert created.get_json()["profile"]["poolId"] == b["id"]


def test_a_profile_outside_a_workspace_gets_no_pool(client):
    created = client.post("/api/profiles", json={"name": "Loner"})
    assert created.get_json()["profile"].get("poolId") in (None, "")


def test_workspaces_route_lists_the_new_fields(client):
    pool = _make_pool(client)
    client.post("/api/clients", json={"name": "Acme", "defaultPoolId": pool["id"], "tags": ["x"]})

    [ws] = client.get("/api/workspaces").get_json()

    assert ws["defaultPoolId"] == pool["id"]
    assert ws["tags"] == ["x"]


def test_an_invalid_pool_id_is_rejected(client):
    r = client.post("/api/profiles", json={"name": "P", "poolId": 12345})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


def _profile(client):
    return client.post("/api/profiles", json={"name": "Noted"}).get_json()["profile"]["id"]


def test_notes_round_trip(client):
    pid = _profile(client)

    created = client.post(f"/api/profiles/{pid}/notes", json={"text": "first"})
    assert created.status_code == 201

    client.post(f"/api/profiles/{pid}/notes", json={"text": "second", "kind": "network"})
    body = client.get(f"/api/profiles/{pid}/notes").get_json()

    assert [e["text"] for e in body["entries"]] == ["second", "first"]
    assert body["entries"][0]["kind"] == "network"


def test_the_summary_comes_back_with_the_log(client):
    pid = _profile(client)
    client.put(f"/api/profiles/{pid}", json={"notes": "pinned line"})

    body = client.get(f"/api/profiles/{pid}/notes").get_json()

    assert body["summary"] == "pinned line"
    assert body["entries"] == []


def test_an_empty_note_is_a_400(client):
    pid = _profile(client)
    r = client.post(f"/api/profiles/{pid}/notes", json={"text": "   "})
    assert r.status_code == 400
    assert "required" in r.get_json()["message"]


def test_an_overlong_note_is_a_400(client):
    pid = _profile(client)
    r = client.post(f"/api/profiles/{pid}/notes", json={"text": "x" * 5000})
    assert r.status_code == 400


def test_notes_on_a_missing_profile_are_404(client):
    assert client.get("/api/profiles/nope/notes").status_code == 404
    assert client.post("/api/profiles/nope/notes", json={"text": "hi"}).status_code == 404


def test_a_note_can_be_deleted(client):
    pid = _profile(client)
    entry = client.post(f"/api/profiles/{pid}/notes", json={"text": "bye"}).get_json()["entry"]

    assert client.delete(f"/api/profiles/{pid}/notes/{entry['id']}").status_code == 200
    assert client.delete(f"/api/profiles/{pid}/notes/{entry['id']}").status_code == 404
    assert client.get(f"/api/profiles/{pid}/notes").get_json()["entries"] == []


# ---------------------------------------------------------------------------
# network-health must not call a blocked profile "direct"
# ---------------------------------------------------------------------------


def test_network_health_reports_a_stranded_pool_profile_as_blocked(client):
    """A blocked config reads as DIRECT on the dataclass. The route must not
    repeat that: saying "fine, direct" about a profile whose next launch will
    refuse to start is the same swallowed-truth bug as a silent launch error."""
    pool = _make_pool(client, members=[])
    pid = client.post(
        "/api/profiles", json={"name": "Stranded", "poolId": pool["id"]}
    ).get_json()["profile"]["id"]

    body = client.get(f"/api/profiles/{pid}/network-health").get_json()

    assert body["ok"] is False
    assert body["status"] == "BLOCKED"
    assert "no enabled member" in body["message"]


def test_network_health_still_reports_a_real_direct_profile_as_direct(client):
    pid = client.post("/api/profiles", json={"name": "Plain"}).get_json()["profile"]["id"]
    body = client.get(f"/api/profiles/{pid}/network-health").get_json()

    assert body["ok"] is True
    assert body["status"] == "DIRECT"
