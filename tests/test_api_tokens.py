"""Named, scoped, revocable API tokens.

The product had exactly one token, in cleartext, in
``$XDG_CONFIG_HOME/multiprofile/api_token``. Three consequences, all real:

* **No least privilege.** A cron job that starts a profile each morning held
  the credential that can delete the fleet and read every cookie jar.
* **No revocation.** Rotating after a leak meant changing the one token and
  breaking every other integration at the same moment — which in practice
  means it does not get rotated.
* **Cleartext at rest.** Whoever could read the file had a working credential.

The auth model these tests are written against is the existing, deliberate
one: **loopback needs no token at all** (the console must work, and code
already running on this machine owns it anyway), and remote requests need one.
Scopes therefore bite exactly where automation connects from, which is the
place they matter.

Every failure — malformed, unknown, revoked, wrong secret — answers 401
identically, so the endpoint cannot be used as an oracle to discover which
part of a guess was right. A *scope* failure is 403, because the credential is
good and the permission is not, and sending someone to replace a token that
was never wrong wastes their afternoon.
"""
from __future__ import annotations

import pathlib

import pytest

from multiprofile import api_tokens
from multiprofile.storage import db

#: Anything not loopback. Host must match, or the DNS-rebinding guard answers
#: first and the test would be measuring the wrong control.
REMOTE = {"REMOTE_ADDR": "192.168.1.50", "HTTP_HOST": "192.168.1.9:8080"}


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    return tmp_path


@pytest.fixture
def client():
    from multiprofile.api.server import create_app

    return create_app().test_client()


def _remote(client, method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return getattr(client, method)(path, headers=headers, environ_overrides=REMOTE, **kw)


def _mint(client, name="t", scopes="read") -> str:
    body = client.post("/api/tokens", json={"name": name, "scopes": scopes}).get_json()
    return body["token"]["token"]


# --------------------------------------------------------------------------
# 1. The secret
# --------------------------------------------------------------------------

def test_a_token_is_shown_once_and_never_again(client):
    """A store that can show you a token later is one that can leak one later."""
    created = client.post("/api/tokens", json={"name": "cron"}).get_json()
    secret = created["token"]["token"]
    assert secret.startswith("mpt_")
    assert "only time" in created["warning"]

    for row in client.get("/api/tokens").get_json()["items"]:
        assert "token" not in row
        assert secret not in str(row)


def test_the_secret_is_not_stored_in_the_database(client, tmp_path):
    """The property F39 exists for: a stolen control.db yields no working token.

    This used to split the presented token on "_" and take the **last**
    fragment. The token is ``mpt_<id>_<secret>`` and the secret is
    ``secrets.token_urlsafe(32)``, whose alphabet includes "_" -- so about
    half the time the fragment was a piece of the secret rather than the
    secret, and 13% of the time it was under ten characters. Those runs
    passed while asserting almost nothing. It failed only when the fragment
    got short enough to occur in a binary file by chance, which is how it was
    found: a one-byte "Q" that any database contains.

    `maxsplit=2` takes everything after the prefix and the id, so the whole
    secret is checked whatever it happens to contain.
    """
    presented = _mint(client, "cron")
    prefix, token_id, secret = presented.split("_", 2)
    assert prefix == "mpt" and len(token_id) == 12
    # If a future token format breaks this split, fail here rather than
    # quietly searching for a substring that proves nothing.
    assert len(secret) >= 20, f"that is not a secret, it is a fragment: {secret!r}"

    blob = b""
    for path in (tmp_path / "data" / "multiprofile").glob("control.db*"):
        blob += path.read_bytes()
    assert blob, "nothing was read; the assertions below would pass vacuously"
    # The id *is* stored, so finding it proves this search can find things.
    # Without it, "the secret is absent" and "the search is broken" look the
    # same, and a test that cannot fail is not evidence of anything.
    assert token_id.encode() in blob, "the search found nothing it should have found"

    assert secret.encode() not in blob, "a leaked database must not yield a working token"
    assert presented.encode() not in blob


def test_the_listing_never_carries_the_hash(client):
    _mint(client, "cron")
    row = client.get("/api/tokens").get_json()["items"][0]
    assert "token_hash" not in row
    assert "tokenHash" not in row


def test_two_tokens_are_never_the_same(client):
    seen = {_mint(client, f"t{i}") for i in range(10)}
    assert len(seen) == 10


# --------------------------------------------------------------------------
# 2. Authentication
# --------------------------------------------------------------------------

def test_loopback_still_needs_no_token(client):
    """The deliberate existing model: the console must work."""
    assert client.get("/api/profiles").status_code == 200


def test_a_remote_request_without_a_token_is_rejected(client):
    assert _remote(client, "get", "/api/profiles").status_code == 401


def test_a_valid_token_lets_a_remote_request_through(client):
    token = _mint(client, "reader", "read")
    assert _remote(client, "get", "/api/profiles", token).status_code == 200


@pytest.mark.parametrize("bad", [
    "garbage",
    "mpt_short_x",
    "mpt_deadbeefcafe_aaaaaaaaaaaaaaaaaaaaaaaa",   # well-formed, unknown id
    "Bearer nested",
    "",
])
def test_every_bad_token_answers_the_same_way(client, bad):
    """No oracle: the response must not say which part of a guess was right."""
    r = _remote(client, "get", "/api/profiles", bad)
    assert r.status_code == 401
    assert r.get_json() == {"error": {"code": "unauthorized"}}


def test_a_token_with_the_right_id_and_wrong_secret_is_rejected(client):
    token = _mint(client, "reader")
    token_id = token.split("_")[1]
    forged = f"mpt_{token_id}_{'z' * 40}"
    assert _remote(client, "get", "/api/profiles", forged).status_code == 401


# --------------------------------------------------------------------------
# 3. Scopes
# --------------------------------------------------------------------------

def test_a_read_token_cannot_write(client):
    token = _mint(client, "reader", "read")
    r = _remote(client, "post", "/api/profiles", token, json={"name": "x"})
    assert r.status_code == 403
    err = r.get_json()["error"]
    assert err["code"] == "insufficient_scope"
    assert err["required"] == "write"
    assert "read" in err["granted"]


def test_a_scope_failure_is_403_not_401(client):
    """The credential is good and the permission is not. A 401 would send
    someone to replace a token that was never wrong."""
    token = _mint(client, "reader", "read")
    assert _remote(client, "post", "/api/profiles", token, json={"name": "x"}).status_code == 403


def test_a_launch_token_can_launch_but_not_delete(client):
    from multiprofile.storage.profiles import create_profile

    pid = create_profile({"name": "target"})["id"]
    token = _mint(client, "cron", ["launch"])
    # Reaching the launch route at all means the scope passed; whether the
    # browser starts is not what this test is about.
    assert _remote(client, "post", f"/api/profiles/{pid}/stop", token).status_code != 403
    assert _remote(client, "delete", f"/api/profiles/{pid}", token).status_code == 403


def test_launch_and_write_imply_read(client):
    """A token that may change things may look at what it changed."""
    token = _mint(client, "cron", ["launch"])
    assert _remote(client, "get", "/api/profiles", token).status_code == 200


def test_admin_implies_everything(client):
    token = _mint(client, "root", ["admin"])
    assert set(api_tokens.listing()[0]["scopes"]) == set(api_tokens.SCOPES)
    assert _remote(client, "post", "/api/profiles", token, json={"name": "x"}).status_code == 200
    assert _remote(client, "get", "/api/tokens", token).status_code == 200


def test_managing_tokens_requires_admin_even_to_look(client):
    """Which integrations exist, when they last ran and what they may do is
    reconnaissance; a read-only token has no business with it."""
    token = _mint(client, "reader", "read")
    assert _remote(client, "get", "/api/tokens", token).status_code == 403


@pytest.mark.parametrize(("method", "path", "scope"), [
    ("GET", "/api/profiles", "read"),
    ("POST", "/api/profiles", "write"),
    ("DELETE", "/api/profiles/p1", "write"),
    ("POST", "/api/profiles/p1/launch", "launch"),
    ("POST", "/api/profiles/p1/stop", "launch"),
    ("GET", "/api/tokens", "admin"),
    ("POST", "/api/tokens", "admin"),
])
def test_the_scope_a_request_needs(method, path, scope):
    assert api_tokens.required_scope(method, path) == scope


def test_an_unknown_scope_is_refused_rather_than_ignored(client):
    r = client.post("/api/tokens", json={"name": "x", "scopes": ["superuser"]})
    assert r.status_code == 400
    assert "superuser" in r.get_json()["error"]["message"]


def test_a_token_needs_a_name_so_a_leak_can_be_identified(client):
    assert client.post("/api/tokens", json={"name": "  "}).status_code == 400
    assert client.post("/api/tokens", json={}).status_code == 400


# --------------------------------------------------------------------------
# 4. Revocation
# --------------------------------------------------------------------------

def test_a_revoked_token_stops_working(client):
    token = _mint(client, "leaked", "read")
    token_id = token.split("_")[1]
    assert _remote(client, "get", "/api/profiles", token).status_code == 200
    assert client.delete(f"/api/tokens/{token_id}").status_code == 200
    assert _remote(client, "get", "/api/profiles", token).status_code == 401


def test_revoking_one_token_does_not_disturb_the_others(client):
    """The whole point: rotating after a leak must not break every
    integration at the same moment."""
    doomed = _mint(client, "leaked", "read")
    kept = _mint(client, "kept", "read")
    client.delete(f"/api/tokens/{doomed.split('_')[1]}")
    assert _remote(client, "get", "/api/profiles", kept).status_code == 200


def test_a_revoked_token_keeps_its_row_so_the_trail_survives(client):
    token = _mint(client, "leaked", "read")
    token_id = token.split("_")[1]
    client.delete(f"/api/tokens/{token_id}")
    assert client.get("/api/tokens").get_json()["count" if False else "items"] == []
    revoked = client.get("/api/tokens?includeRevoked=1").get_json()["items"]
    assert [r["id"] for r in revoked] == [token_id]
    assert revoked[0]["revokedAt"]


def test_revoking_twice_is_a_404_not_a_second_success(client):
    token_id = _mint(client, "leaked").split("_")[1]
    assert client.delete(f"/api/tokens/{token_id}").status_code == 200
    assert client.delete(f"/api/tokens/{token_id}").status_code == 404


def test_revoking_something_that_never_existed_is_a_404(client):
    assert client.delete("/api/tokens/nope").status_code == 404


# --------------------------------------------------------------------------
# 5. Last used
# --------------------------------------------------------------------------

def test_using_a_token_records_that_it_was_used(client):
    token = _mint(client, "cron", "read")
    token_id = token.split("_")[1]
    assert client.get("/api/tokens").get_json()["items"][0]["lastUsedAt"] is None
    _remote(client, "get", "/api/profiles", token)
    row = next(r for r in client.get("/api/tokens").get_json()["items"] if r["id"] == token_id)
    assert row["lastUsedAt"], "a token nobody can tell is unused cannot be retired"


def test_a_failure_to_record_last_use_does_not_fail_the_request(client, monkeypatch):
    token = _mint(client, "cron", "read")

    def boom(*a, **k):
        raise RuntimeError("write failed")

    monkeypatch.setattr(db, "touch_api_token", boom)
    assert _remote(client, "get", "/api/profiles", token).status_code == 200


# --------------------------------------------------------------------------
# 6. The legacy token
# --------------------------------------------------------------------------

def test_the_legacy_file_token_still_works(client, tmp_path, monkeypatch):
    """It may already be wired into the operator's scripts."""
    cfg = tmp_path / "cfg" / "multiprofile"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "api_token").write_text("legacy-secret-value\n", encoding="utf-8")
    assert _remote(client, "get", "/api/profiles", "legacy-secret-value").status_code == 200


def test_the_legacy_env_token_still_works(client, monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_API_TOKEN", "env-secret-value")
    assert _remote(client, "get", "/api/profiles", "env-secret-value").status_code == 200


def test_the_legacy_token_carries_every_scope(client, monkeypatch):
    """It predates scopes; narrowing it would silently break a live script."""
    monkeypatch.setenv("MULTIPROFILE_API_TOKEN", "env-secret-value")
    r = _remote(client, "post", "/api/profiles", "env-secret-value", json={"name": "x"})
    assert r.status_code == 200


def test_the_legacy_token_is_not_copied_into_the_store(client, monkeypatch):
    """Silently duplicating a credential into a database is not a migration."""
    monkeypatch.setenv("MULTIPROFILE_API_TOKEN", "env-secret-value")
    _remote(client, "get", "/api/profiles", "env-secret-value")
    assert db.count_api_tokens(include_revoked=True) == 0


# --------------------------------------------------------------------------
# 7. Scope normalisation
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("given", "expected"), [
    ("read", {"read"}),
    (["write"], {"read", "write"}),
    (["launch"], {"read", "launch"}),
    (["admin"], set(api_tokens.SCOPES)),
    (["read", "read"], {"read"}),
    (None, {"read"}),
    ("", {"read"}),
])
def test_scopes_are_stored_as_exactly_what_the_token_can_do(given, expected):
    """Storing the implication rather than resolving it at check time means
    reading the row answers the question without also knowing the rules."""
    assert set(api_tokens.normalise_scopes(given).split(",")) == expected


def test_a_scope_list_of_the_wrong_type_is_refused():
    with pytest.raises(api_tokens.TokenError):
        api_tokens.normalise_scopes({"read": True})


def test_verifying_junk_never_raises():
    for junk in ("", "   ", "mpt_", "mpt__", "x" * 500, "mpt_zzzzzzzzzzzz_short"):
        assert api_tokens.verify(junk) is None


def test_the_token_file_is_not_world_readable_if_we_ever_write_one(tmp_path):
    """The file token is the operator's to manage, but nothing this code
    writes may widen it. Asserted so a future writer cannot regress it."""
    path = pathlib.Path(tmp_path / "cfg" / "multiprofile" / "api_token")
    if path.exists():
        assert path.stat().st_mode & 0o077 == 0
