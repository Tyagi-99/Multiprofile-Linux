"""Workstream 3 (API hardening) tests: cursor pagination, input validation,
auth enforcement, and the live SSE event stream.

Storage is isolated per-test via XDG_* env vars. The event-bus test is
in-process: WS1's ``multiprofile.api.events`` bus is used directly.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from multiprofile.api import events as bus
from multiprofile.api.event_bus import normalize_message
from multiprofile.api.pagination import decode_cursor, encode_cursor, paginate
from multiprofile.api.server import create_app
from multiprofile.audit import audit_log


@pytest.fixture()
def xdg_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    # Keep create_app() daemon threads (job workers, supervisor) out of pytest.
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")


@pytest.fixture()
def client(xdg_isolated):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def fake_jobs():
    """Seed the legacy in-memory job store with 30 fake jobs (no workers)."""
    from multiprofile.jobs import legacy

    saved_jobs = dict(legacy._jobs)
    saved_recent = list(legacy._recent)
    try:
        for i in range(30):
            jid = f"job_test_{i:03d}"
            legacy._jobs[jid] = {
                "id": jid,
                "type": "launch_profile",
                "profileIds": ["p1"],
                "status": "queued",
                "createdAt": 1_700_000_000.0 + i,  # distinct, sortable
            }
            legacy._recent.append(jid)
        yield
    finally:
        legacy._jobs.clear()
        legacy._jobs.update(saved_jobs)
        legacy._recent.clear()
        legacy._recent.extend(saved_recent)


def _seed_profiles(n=30):
    from multiprofile.storage.profiles import create_profile

    return [create_profile({"name": f"P{i:02d}"}) for i in range(n)]


def _page_through(client, path, limit=10):
    seen = []
    cursor = None
    pages = 0
    while True:
        qs = {"limit": limit}
        if cursor:
            qs["cursor"] = cursor
        resp = client.get(path, query_string=qs)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["limit"] == limit
        seen.extend(body["items"])
        cursor = body["next_cursor"]
        pages += 1
        assert pages < 20  # guard against infinite loops
        if cursor is None:
            break
    return seen


# --------------------------------------------------------------------------- #
# Pagination                                                                  #
# --------------------------------------------------------------------------- #
def test_profiles_pagination_all_30_exactly_once(client):
    seeded = {p["id"] for p in _seed_profiles(30)}
    items = _page_through(client, "/api/profiles", limit=10)
    ids = [p["id"] for p in items]
    assert len(ids) == len(set(ids))  # no duplicates, none skipped
    assert seeded <= set(ids)  # all 30 seeded profiles returned


def test_profiles_limit_clamped_to_100(client):
    seeded = {p["id"] for p in _seed_profiles(3)}
    resp = client.get("/api/profiles", query_string={"limit": 1000})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["limit"] == 100
    assert seeded <= {p["id"] for p in body["items"]}
    assert body["next_cursor"] is None  # everything fit in one page


def test_profiles_default_limit_is_25(client):
    _seed_profiles(30)
    body = client.get("/api/profiles").get_json()
    assert body["limit"] == 25
    assert len(body["items"]) == 25
    assert body["next_cursor"] is not None


def test_malformed_cursor_rejected(client):
    resp = client.get("/api/profiles", query_string={"cursor": "!!!"})
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "invalid_input"
    assert err["field"] == "cursor"


def test_jobs_pagination_all_30_exactly_once(client, fake_jobs):
    items = _page_through(client, "/api/jobs", limit=10)
    ids = [j["id"] for j in items]
    assert len(ids) == 30
    assert len(set(ids)) == 30


def test_jobs_limit_clamped(client, fake_jobs):
    body = client.get("/api/jobs", query_string={"limit": 5000}).get_json()
    assert body["limit"] == 100
    assert len(body["items"]) == 30
    assert body["next_cursor"] is None


def test_audit_pagination_all_entries_exactly_once(client):
    for i in range(30):
        audit_log("test_event", profile_id=f"p{i}", ok=True)
    seen = _page_through(client, "/api/audit", limit=10)
    assert len(seen) == 30
    # entries carry the seeded profile ids exactly once each
    pids = sorted(e.get("profileId") for e in seen)
    assert pids == sorted(f"p{i}" for i in range(30))


def test_devices_pagination_all_33_exactly_once(client):
    seen = []
    cursor = None
    while True:
        qs = {"limit": 10}
        if cursor:
            qs["cursor"] = cursor
        body = client.get("/api/devices", query_string=qs).get_json()
        seen.extend(body["devices"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    ids = [d["id"] for d in seen]
    assert len(ids) == 33
    assert len(set(ids)) == 33


def test_devices_legacy_full_list_without_params(client):
    body = client.get("/api/devices").get_json()
    assert len(body["devices"]) == 33
    assert "next_cursor" not in body  # legacy shape preserved


def test_cursor_roundtrip_tie_break():
    items = [
        {"id": "b", "ts": 5.0},
        {"id": "a", "ts": 5.0},  # tie on ts: id must break it
        {"id": "c", "ts": 6.0},
    ]
    key = lambda it: (it["ts"], it["id"])
    p1 = paginate(items, cursor=None, limit=2, key_fn=key)
    assert [i["id"] for i in p1["items"]] == ["a", "b"]
    assert p1["next_cursor"]
    k, i = decode_cursor(p1["next_cursor"])
    assert (k, i) == (5.0, "b")
    p2 = paginate(items, cursor=p1["next_cursor"], limit=2, key_fn=key)
    assert [i["id"] for i in p2["items"]] == ["c"]
    assert p2["next_cursor"] is None


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #
def test_profile_create_wrong_type_rejected(client):
    resp = client.post("/api/profiles", json={"name": 123})
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert err["code"] == "invalid_input"
    assert err["field"] == "name"


def test_profile_create_bad_enum_rejected(client):
    resp = client.post("/api/profiles", json={"name": "x", "proxyType": "ftp"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "proxyType"


def test_profile_create_negative_port_rejected(client):
    resp = client.post("/api/profiles", json={"name": "x", "proxyPort": -1})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "proxyPort"


def test_profile_create_port_too_large_rejected(client):
    resp = client.post("/api/profiles", json={"name": "x", "proxyPort": 99999})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "proxyPort"


def test_profile_create_unknown_device_preset_rejected(client):
    resp = client.post(
        "/api/profiles", json={"name": "x", "deviceId": "no-such-device"}
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "deviceId"


def test_profile_create_known_device_preset_accepted(client):
    resp = client.post(
        "/api/profiles", json={"name": "x", "deviceId": "pixel-9"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["profile"]["deviceId"] == "pixel-9"


def test_profile_create_bad_network_mode_rejected(client):
    resp = client.post(
        "/api/profiles",
        json={"name": "x", "networkConfig": {"mode": "WORMHOLE"}},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "networkConfig.mode"


def test_profile_update_wrong_type_rejected(client):
    pid = _seed_profiles(1)[0]["id"]
    resp = client.put(f"/api/profiles/{pid}", json={"tags": "not-a-list"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "tags"


def test_profile_create_non_json_body_rejected(client):
    """Refused at the security boundary now, not by the body validator.

    A text/plain POST is a CORS simple request: any web page can send one
    with no preflight, and every write route used to parse it anyway via
    get_json(force=True). The request guard rejects the Content-Type before
    the body is ever looked at, so this is a 403 rather than a 400 — the
    request never reaches validation. Same outcome, refused earlier.
    """
    resp = client.post(
        "/api/profiles", data="this is not json", content_type="text/plain"
    )
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "bad_content_type"


def test_malformed_json_still_fails_body_validation(client):
    """With the right Content-Type, the validator is still the one to answer."""
    resp = client.post("/api/profiles", data="this is not json",
                       content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "_body"


def test_job_enqueue_bad_type_rejected(client):
    resp = client.post(
        "/api/jobs", json={"type": "nuke_everything", "profileIds": ["p1"]}
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "type"


def test_job_enqueue_missing_profile_ids_rejected(client):
    resp = client.post("/api/jobs", json={"type": "launch_profile"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "profileIds"


def test_job_enqueue_bad_flag_rejected(client):
    resp = client.post(
        "/api/jobs",
        json={"type": "stop_profile", "profileIds": ["p1"], "direct": "yes"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "direct"


def test_job_enqueue_valid_accepted(client):
    resp = client.post(
        "/api/jobs", json={"type": "stop_profile", "profileIds": ["p1"]}
    )
    assert resp.status_code == 201
    assert resp.get_json()["job"]["type"] == "stop_profile"


def test_proxy_test_bad_scheme_rejected(client):
    resp = client.post(
        "/api/proxy/test", json={"host": "h", "port": 8080, "protocol": "ftp"}
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "protocol"


def test_proxy_test_bad_port_rejected(client):
    resp = client.post(
        "/api/proxy/test", json={"host": "h", "port": 70000}
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "port"


def test_clone_bad_mode_rejected(client):
    pid = _seed_profiles(1)[0]["id"]
    resp = client.post(f"/api/profiles/{pid}/clone", json={"mode": "everything"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "mode"


# --------------------------------------------------------------------------- #
# Auth                                                                        #
# --------------------------------------------------------------------------- #
def test_remote_without_token_rejected(client):
    resp = client.get("/api/profiles", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
    assert resp.status_code == 401
    assert resp.get_json() == {"error": {"code": "unauthorized"}}


def test_remote_health_without_token_rejected(client):
    # v1.6.0 exempted GET /api/health; v1.7.0 does not (deliberate hardening).
    resp = client.get("/api/health", environ_overrides={"REMOTE_ADDR": "192.168.1.5"})
    assert resp.status_code == 401


def test_localhost_without_token_allowed(client):
    resp = client.get("/api/profiles", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 200
    resp = client.get("/api/profiles", environ_overrides={"REMOTE_ADDR": "::1"})
    assert resp.status_code == 200


def test_remote_with_valid_token_allowed(client, monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_API_TOKEN", "tok123")
    resp = client.get(
        "/api/profiles",
        environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        headers={"Authorization": "Bearer tok123"},
    )
    assert resp.status_code == 200


def test_remote_wrong_and_missing_token_indistinguishable(client, monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_API_TOKEN", "tok123")
    env = {"REMOTE_ADDR": "192.168.1.5"}
    missing = client.get("/api/profiles", environ_overrides=dict(env))
    wrong = client.get(
        "/api/profiles",
        environ_overrides=dict(env),
        headers={"Authorization": "Bearer wrong"},
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.get_json() == wrong.get_json() == {"error": {"code": "unauthorized"}}


def test_token_from_config_file(client, monkeypatch, tmp_path):
    cfg = tmp_path / "cfg" / "multiprofile"
    cfg.mkdir(parents=True)
    (cfg / "api_token").write_text("file-token-abc\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    resp = client.get(
        "/api/profiles",
        environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        headers={"Authorization": "Bearer file-token-abc"},
    )
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Live events (SSE, in-process)                                               #
# --------------------------------------------------------------------------- #
def test_normalize_message_tuple_and_dict():
    m = normalize_message(("job.progress", {"job_id": "j1"}))
    assert m["type"] == "job.progress"
    assert m["payload"]["job_id"] == "j1"
    assert m["at"]
    m2 = normalize_message({"type": "t", "payload": {"a": 1}, "at": "x"})
    assert (m2["type"], m2["at"]) == ("t", "x")


def test_sse_receives_published_event(client):
    bus.clear_subscribers()
    received = {}

    def _get():
        resp = client.get("/api/events/stream?max_events=1")
        received["data"] = resp.get_data(as_text=True)

    t = threading.Thread(target=_get, daemon=True)
    t.start()
    for _ in range(200):
        if bus.subscriber_count() >= 1:
            break
        time.sleep(0.05)
    assert bus.subscriber_count() >= 1, "SSE subscriber never attached"
    bus.publish(
        "job.progress",
        {"job_id": "job_1", "job_type": "launch_profile",
         "profile_id": "p1", "status": "running", "detail": "starting"},
    )
    t.join(timeout=30)
    assert not t.is_alive(), "SSE stream did not close after max_events"
    data = received["data"]
    assert '"type": "job.progress"' in data
    assert '"job_id": "job_1"' in data
    assert '"status": "running"' in data
    bus.clear_subscribers()


def test_emit_helpers_publish_to_bus():
    from multiprofile.api.event_bus import (
        emit_job_progress,
        emit_profile_status,
        get_bus,
    )

    bus.clear_subscribers()
    q = bus.subscribe()
    emit_profile_status(get_bus(), "p1", "idle", "running")
    emit_job_progress(get_bus(), "j1", "launch_profile", "p1", "queued")
    got = [q.get(timeout=5), q.get(timeout=5)]
    types = {m[0] for m in got}
    assert types == {"profile.status", "job.progress"}
    by_type = {m[0]: m[1] for m in got}
    assert by_type["profile.status"]["profile_id"] == "p1"
    assert by_type["profile.status"]["new_state"] == "running"
    assert by_type["job.progress"]["job_id"] == "j1"
    bus.clear_subscribers()


def test_encode_cursor_is_opaque_and_roundtrips():
    c = encode_cursor(5.0, "b")
    assert c == encode_cursor(5.0, "b")  # deterministic
    assert decode_cursor(c) == (5.0, "b")
    assert decode_cursor(None) is None
    assert decode_cursor("") is None
