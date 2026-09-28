"""F8: a field name the API does not recognise must not look like a success.

``create_profile`` does ``dict(payload)`` and ``update_profile`` merges
``{**stored, **payload}``, so every key a client sent used to be written to
profiles.json and returned on every later read. Values were validated,
names were not. Measured before the fix::

    POST /api/profiles {"name":"junk-field-test","totallyBogusKey":"pwned"}
    -> HTTP 200 | stored keys include bogus: True | warnings: []

The audit lost a test to this: it sent ``devicePresetId`` where the field is
``deviceId``, the API answered 200, and the preset silently never applied. A
typo in a field name was indistinguishable from a working request.

Three tiers, because "unrecognised" is three different situations -- see the
registry in ``api/validation.py``:

1. honoured        -> validated as before
2. server-owned    -> accepted and dropped, so read-modify-write still works
3. unimplemented   -> accepted, reported in ``warnings[]``, dropped
"""
from __future__ import annotations

import pytest

from multiprofile.api import validation as V
from multiprofile.api.server import create_app

HEADERS = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _create(client, **body):
    return client.post("/api/profiles", json=body, headers=HEADERS)


# --------------------------------------------------------------------------
# 1. An unknown key is refused
# --------------------------------------------------------------------------

def test_the_audits_own_repro_is_now_refused(client):
    r = _create(client, name="junk-field-test", totallyBogusKey="pwned")
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert err["field"] == "totallyBogusKey"
    assert err["code"] == "invalid_input"


def test_a_typo_is_named_and_corrected(client):
    """The failure that cost the audit a test, with the fix it needed."""
    r = _create(client, name="typo", devicePresetId="iphone-16")
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert err["field"] == "devicePresetId"
    assert "deviceId" in err["message"], err["message"]


def test_an_unknown_key_is_refused_on_update_too(client):
    pid = _create(client, name="u").get_json()["profile"]["id"]
    r = client.put(f"/api/profiles/{pid}", json={"bogus": 1}, headers=HEADERS)
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "bogus"


def test_nothing_unknown_reaches_storage(client):
    """The refusal has to happen before the write, not after it."""
    from multiprofile.storage.profiles import load_profiles

    assert _create(client, name="rejected", totallyBogusKey="pwned").status_code == 400
    assert not any(p.get("name") == "rejected" for p in load_profiles())


# --------------------------------------------------------------------------
# 2. Server-owned keys are accepted, so read-modify-write survives
# --------------------------------------------------------------------------

def test_a_row_from_the_list_endpoint_can_be_put_straight_back(client):
    """There is no GET /api/profiles/<id>, so this IS the edit flow.

    A client reads a row from ``GET /api/profiles`` and PUTs it back. That row
    carries runtime truth the server injected -- launch state, cdp port, proxy
    verdict -- and refusing those keys would break the only way the API offers
    to edit a profile you have just read.
    """
    pid = _create(client, name="rmw", deviceId="iphone-16").get_json()["profile"]["id"]
    row = next(
        p for p in client.get("/api/profiles", headers=HEADERS).get_json()["items"]
        if p["id"] == pid
    )
    r = client.put(f"/api/profiles/{pid}", json=row, headers=HEADERS)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["profile"]["name"] == "rmw"


def test_every_key_the_list_endpoint_emits_is_accepted_on_update(client):
    """The registry must stay in step with the runtime enrichment.

    ``load_profiles`` and ``_with_launch_state`` both write keys onto a row
    that no client ever sent. Each one they add and the registry does not know
    turns the read-modify-write flow above into a 400, and the failure only
    shows up once that key is populated -- after a launch, say. This is the
    test that fails instead.
    """
    pid = _create(client, name="keys").get_json()["profile"]["id"]
    row = next(
        p for p in client.get("/api/profiles", headers=HEADERS).get_json()["items"]
        if p["id"] == pid
    )
    known = V.PROFILE_FIELDS | V.PROFILE_FIELDS_SERVER_OWNED | set(V.PROFILE_FIELDS_UNIMPLEMENTED)
    unregistered = sorted(set(row) - known)
    assert not unregistered, (
        f"the list endpoint emits {unregistered}, which PUT would refuse; "
        "add them to PROFILE_FIELDS_SERVER_OWNED"
    )


def test_a_client_cannot_dictate_a_server_owned_field(client):
    """Accepted is not the same as honoured."""
    pid = _create(client, name="owned").get_json()["profile"]["id"]
    r = client.put(
        f"/api/profiles/{pid}",
        json={"id": "profile_hijacked", "createdAt": "1999-01-01", "pid": 4242},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["profile"]["id"] == pid
    assert r.get_json()["profile"].get("createdAt") != "1999-01-01"


# --------------------------------------------------------------------------
# 3. Recognised but unimplemented: reported, never silently kept
# --------------------------------------------------------------------------

def test_the_wizards_dead_keys_are_reported_not_refused(client):
    """Our own wizard sends three keys nothing reads.

    Refusing them would 400 every creation the wizard makes, so they are
    accepted and named in ``warnings[]``. The gap itself is an open finding.
    """
    r = _create(
        client,
        name="wizard",
        deviceId="iphone-16",
        fingerprintStrategy="stable",
        networkMode="gateway",
        gatewayId="gw_1",
    )
    assert r.status_code == 200, r.get_json()
    warnings = " ".join(r.get_json()["warnings"])
    for key in ("fingerprintStrategy", "networkMode", "gatewayId"):
        assert key in warnings, f"{key} was accepted without a word: {warnings}"


def test_a_setting_that_cannot_take_effect_is_not_persisted(client):
    """Persisting it is what let the gap survive: it reads back as applied."""
    from multiprofile.storage.profiles import get_profile

    pid = _create(client, name="dead", fingerprintStrategy="stable").get_json()["profile"]["id"]
    stored = get_profile(pid) or {}
    assert "fingerprintStrategy" not in stored


@pytest.mark.parametrize("key", sorted(V.PROFILE_FIELDS_UNIMPLEMENTED))
def test_each_unimplemented_key_says_where_the_setting_really_lives(key):
    """A warning that only says "ignored" sends the operator back to the docs."""
    assert V.PROFILE_FIELDS_UNIMPLEMENTED[key].strip()


def test_the_three_tiers_do_not_overlap():
    """A key in two tiers would make its handling depend on check order."""
    assert not (V.PROFILE_FIELDS & V.PROFILE_FIELDS_SERVER_OWNED)
    unimpl = set(V.PROFILE_FIELDS_UNIMPLEMENTED)
    assert not (V.PROFILE_FIELDS & unimpl)
    assert not (V.PROFILE_FIELDS_SERVER_OWNED & unimpl)


# --------------------------------------------------------------------------
# 4. status was in use and unvalidated
# --------------------------------------------------------------------------

def test_the_frontends_park_flow_still_works(client):
    """``lib/mutations.ts`` PUTs {status} and nothing validated it before."""
    pid = _create(client, name="park").get_json()["profile"]["id"]
    r = client.put(f"/api/profiles/{pid}", json={"status": "parked"}, headers=HEADERS)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["profile"]["status"] == "parked"


def test_a_status_outside_the_enum_is_refused(client):
    pid = _create(client, name="badstatus").get_json()["profile"]["id"]
    r = client.put(f"/api/profiles/{pid}", json={"status": "nonsense"}, headers=HEADERS)
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "status"


# --------------------------------------------------------------------------
# 5. The ordinary paths are unchanged
# --------------------------------------------------------------------------

def test_a_well_formed_create_is_untouched(client):
    r = _create(
        client,
        name="fine",
        startUrl="https://example.com",
        tags=["a", "b"],
        notes="hello",
        timezone="Europe/Berlin",
        deviceId="iphone-16",
    )
    assert r.status_code == 200, r.get_json()
    p = r.get_json()["profile"]
    assert p["name"] == "fine"
    assert p["tags"] == ["a", "b"]
    assert p["deviceId"] == "iphone-16"


def test_an_unknown_value_is_still_refused(client):
    """The check that already worked must keep working."""
    r = _create(client, name="badpreset", deviceId="no-such-device")
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "deviceId"
