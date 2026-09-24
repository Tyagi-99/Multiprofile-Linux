"""The localhost API must not be drivable by a web page the operator opens.

register_auth lets anything on this machine through without a token, on the
reasoning that code running here already owns the machine. A *web page* is
also "on this machine" as far as the socket is concerned, and it is not
trusted at all.

Three holes were verified open before this guard existed:

    POST text/plain + hostile Origin -> 200, profile created
    GET with foreign Host header     -> 200, no Host validation
    DELETE with hostile Origin       -> reached the handler

Every write route parses its body with get_json(force=True), so Content-Type
was never checked. A text/plain POST is a CORS *simple request*: no preflight,
sent by any page without the browser asking permission. The response is
unreadable cross-origin, but the side effect lands. Add DNS rebinding -- which
nothing prevented, because remote_addr really is 127.0.0.1 -- and reads become
possible too.
"""
from __future__ import annotations

import pytest

from multiprofile.api.server import create_app

EVIL = "https://evil.example"


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


def _count(client) -> int:
    return len(client.get("/api/profiles?limit=100").get_json()["items"])


# ---------------------------------------------------------------------------
# the three verified holes
# ---------------------------------------------------------------------------


def test_a_text_plain_post_no_longer_creates_a_profile(client):
    """The exact request a hostile page can send with no preflight."""
    before = _count(client)

    r = client.post("/api/profiles", data='{"name":"CSRF-created"}',
                    content_type="text/plain", headers={"Origin": EVIL})

    assert r.status_code == 403
    assert r.get_json()["error"] in {"cross_origin", "bad_content_type"}
    assert _count(client) == before, "a cross-origin POST still created a profile"


def test_a_foreign_host_header_is_refused(client):
    """DNS rebinding: remote_addr is genuinely 127.0.0.1, only Host differs."""
    r = client.get("/api/profiles", headers={"Host": "attacker.example"})

    assert r.status_code == 403
    assert r.get_json()["error"] == "bad_host"


def test_a_cross_origin_delete_is_refused(client):
    r = client.delete("/api/profiles/anything", headers={"Origin": EVIL})

    assert r.status_code == 403
    assert r.get_json()["error"] == "cross_origin"


# ---------------------------------------------------------------------------
# Host
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "127.0.0.1", "[::1]:8765"])
def test_loopback_hosts_are_accepted(client, host):
    assert client.get("/api/profiles", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "multiprofile.example.com:8765", "10.0.0.5:8765"])
def test_non_loopback_hosts_are_refused(client, host):
    assert client.get("/api/profiles", headers={"Host": host}).status_code == 403


def test_the_guard_only_covers_the_api(client):
    """The static console must still be reachable."""
    r = client.get("/", headers={"Host": "attacker.example"})
    assert r.status_code != 403


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------


def test_a_matching_origin_is_accepted(client):
    """This is what the console itself sends."""
    r = client.post("/api/profiles", json={"name": "Same origin"},
                    headers={"Origin": "http://localhost", "Host": "localhost"})
    assert r.status_code == 200


def test_a_null_origin_is_refused(client):
    """Sandboxed iframe or file:// — never the console."""
    r = client.post("/api/profiles", json={"name": "x"}, headers={"Origin": "null"})
    assert r.status_code == 403


def test_an_absent_origin_is_allowed(client):
    """curl, the SDKs and this suite never send one; a browser always does."""
    assert client.post("/api/profiles", json={"name": "No origin"}).status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_sec_fetch_site_is_honoured(client, site):
    r = client.post("/api/profiles", json={"name": "x"}, headers={"Sec-Fetch-Site": site})
    assert r.status_code == 403


def test_sec_fetch_site_same_origin_is_fine(client):
    r = client.post("/api/profiles", json={"name": "ok"}, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Content-Type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ctype", [
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
])
def test_a_body_in_a_simple_request_content_type_is_refused(client, ctype):
    r = client.post("/api/profiles", data='{"name":"x"}', content_type=ctype)
    assert r.status_code == 403
    assert r.get_json()["error"] == "bad_content_type"


def test_json_bodies_are_accepted(client):
    assert client.post("/api/profiles", json={"name": "json ok"}).status_code == 200


def test_a_json_suffix_content_type_is_accepted(client):
    r = client.post("/api/profiles", data='{"name":"suffix"}',
                    content_type="application/merge-patch+json")
    assert r.status_code == 200


def test_a_bodyless_write_is_still_allowed(client):
    """/launch and /stop send no body at all — Host and Origin cover them."""
    r = client.post("/api/profiles/does-not-exist/stop")
    assert r.status_code != 403


def test_a_bodyless_write_is_refused_cross_origin(client):
    """...and a hostile page cannot fire one."""
    r = client.post("/api/profiles/does-not-exist/stop", headers={"Origin": EVIL})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# reads still work
# ---------------------------------------------------------------------------


def test_safe_methods_are_not_origin_checked(client):
    """A cross-origin GET cannot be read by the page anyway, and blocking it
    would break nothing useful while breaking legitimate tooling."""
    assert client.get("/api/profiles", headers={"Origin": EVIL}).status_code == 200


def test_health_still_answers(client):
    assert client.get("/api/health").status_code == 200
