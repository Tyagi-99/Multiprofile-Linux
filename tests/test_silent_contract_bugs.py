"""F21: four ways the console and the API disagreed without saying so.

All four are the F8/F16 family -- a control that looks wired and is not -- and
all four were found by sweeping for that shape rather than by a bug report,
because none of them produces an error. They produce a blank, or a field that
reads back empty, or a 400 nobody expected.

1. ``description`` was dropped. ``create_client`` built a fixed dict and
   ``create_template`` copied only ``TEMPLATE_FIELDS``; neither included it,
   while the settings dialog sent it on every save and then rendered
   ``it.description || "—"`` in the table. Saving appeared to work forever.
2. ``update_client`` merged the payload wholesale while ``create_client``
   dropped unknowns -- two different answers to the same question, so junk
   could enter a record on update but not on create.
3. Clearing a start URL or a note was a hard 400. The edit dialog sends
   ``null`` for an emptied field, and ``v_str`` refused it, so the console had
   no way to clear either field at all.
4. Egress IP and latency were permanently "—" on several screens. The API has
   no key-casing convention: ``ProxyHealthResult``/``NetworkDiagnostics``
   hand-write camelCase while ``/api/profiles/<id>/health`` serialises its
   dataclass with ``asdict()`` and emits snake_case for the same fields. The
   console read snake_case.
"""
from __future__ import annotations

import pytest

HEADERS = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from multiprofile.api.server import create_app

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _unwrap(body, key):
    """The collection routes return a bare list; the item routes wrap."""
    if isinstance(body, list):
        return body
    return body.get(key) or body


# --------------------------------------------------------------------------
# 1. description survives the round trip
# --------------------------------------------------------------------------

def test_a_clients_description_is_stored(client):
    r = client.post("/api/clients", json={"name": "C", "description": "who they are"}, headers=HEADERS)
    assert r.status_code == 201, r.get_json()
    assert _unwrap(r.get_json(), "client")["description"] == "who they are"


def test_a_clients_description_survives_an_edit(client):
    cid = _unwrap(client.post("/api/clients", json={"name": "C", "description": "v1"},
                              headers=HEADERS).get_json(), "client")["id"]
    r = client.put(f"/api/clients/{cid}", json={"name": "C", "description": "v2"}, headers=HEADERS)
    assert _unwrap(r.get_json(), "client")["description"] == "v2"


def test_a_templates_description_is_stored(client):
    r = client.post("/api/templates", json={"name": "T", "description": "what it is for"},
                    headers=HEADERS)
    assert r.status_code == 201, r.get_json()
    assert _unwrap(r.get_json(), "template")["description"] == "what it is for"


def test_a_templates_description_survives_an_edit(client):
    tid = _unwrap(client.post("/api/templates", json={"name": "T", "description": "v1"},
                              headers=HEADERS).get_json(), "template")["id"]
    r = client.put(f"/api/templates/{tid}", json={"name": "T", "description": "v2"}, headers=HEADERS)
    assert _unwrap(r.get_json(), "template")["description"] == "v2"


def test_the_list_view_can_read_the_description_back(client):
    """The table renders `it.description || "—"`; it showed "—" forever."""
    client.post("/api/clients", json={"name": "C", "description": "visible"}, headers=HEADERS)
    rows = _unwrap(client.get("/api/clients", headers=HEADERS).get_json(), "clients")
    assert any(r.get("description") == "visible" for r in rows), rows


# --------------------------------------------------------------------------
# 2. create and update agree about unknown keys
# --------------------------------------------------------------------------

def test_update_no_longer_accepts_arbitrary_keys(client):
    """create dropped unknowns and update kept them -- pick one."""
    cid = _unwrap(client.post("/api/clients", json={"name": "C"}, headers=HEADERS).get_json(),
                  "client")["id"]
    row = _unwrap(client.put(f"/api/clients/{cid}", json={"name": "C", "junkKey": "pwned"},
                             headers=HEADERS).get_json(), "client")
    assert "junkKey" not in row, row


def test_the_known_fields_still_update(client):
    cid = _unwrap(client.post("/api/clients", json={"name": "C"}, headers=HEADERS).get_json(),
                  "client")["id"]
    row = _unwrap(client.put(f"/api/clients/{cid}", json={"name": "renamed", "color": "#123456"},
                             headers=HEADERS).get_json(), "client")
    assert row["name"] == "renamed"
    assert row["color"] == "#123456"


# --------------------------------------------------------------------------
# 3. null means "clear", not 400
# --------------------------------------------------------------------------

def _profile(client, **body):
    return client.post("/api/profiles", json={"name": "p", **body}, headers=HEADERS).get_json()["profile"]


def test_clearing_a_start_url_and_a_note_is_allowed(client):
    """Exactly what the edit dialog sends when both fields are emptied."""
    pid = _profile(client, startUrl="https://a.example", notes="hi")["id"]
    r = client.put(f"/api/profiles/{pid}",
                   json={"name": "p", "startUrl": None, "notes": None, "tags": []},
                   headers=HEADERS)
    assert r.status_code == 200, r.get_json()


def test_a_cleared_field_falls_back_to_its_default(client):
    """None must not reach the launcher as a start URL."""
    pid = _profile(client, startUrl="https://a.example", notes="hi")["id"]
    p = client.put(f"/api/profiles/{pid}", json={"startUrl": None, "notes": None},
                   headers=HEADERS).get_json()["profile"]
    assert p["startUrl"] == "https://example.com"
    assert p["notes"] == ""
    assert p["startUrl"] is not None and p["notes"] is not None


def test_a_non_null_start_url_is_still_validated(client):
    """Allowing null must not have opened the field to anything."""
    pid = _profile(client)["id"]
    r = client.put(f"/api/profiles/{pid}", json={"startUrl": 17}, headers=HEADERS)
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "startUrl"


def test_an_empty_string_clears_the_same_way(client):
    pid = _profile(client, startUrl="https://a.example")["id"]
    r = client.put(f"/api/profiles/{pid}", json={"startUrl": ""}, headers=HEADERS)
    # "" fails min_len, which is fine -- but it must not be stored as "".
    assert r.status_code == 400 or r.get_json()["profile"]["startUrl"] != ""


# --------------------------------------------------------------------------
# 4. the console can read egress IP and latency
# --------------------------------------------------------------------------

def test_proxy_health_carries_both_key_spellings():
    from multiprofile.network import ProxyHealthResult

    d = ProxyHealthResult(ok=True, latency_ms=42, egress_ip="1.2.3.4").to_dict()
    assert d["egressIp"] == d["egress_ip"] == "1.2.3.4"
    assert d["latencyMs"] == d["latency_ms"] == 42


def test_diagnostics_carries_both_key_spellings():
    from multiprofile.network import NetworkDiagnostics

    d = NetworkDiagnostics(profile_id="p", egress_ip="5.6.7.8", latency_ms=7).to_dict()
    assert d["egressIp"] == d["egress_ip"] == "5.6.7.8"
    assert d["latencyMs"] == d["latency_ms"] == 7
    assert d["profileId"] == d["profile_id"] == "p"


def test_none_stays_none_in_both_spellings():
    """A missing measurement must not read as present under one spelling."""
    from multiprofile.network import ProxyHealthResult

    d = ProxyHealthResult(ok=False).to_dict()
    assert d["egressIp"] is None and d["egress_ip"] is None
    assert d["latencyMs"] is None and d["latency_ms"] is None
