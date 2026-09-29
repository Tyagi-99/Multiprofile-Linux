"""Creating a fleet from a list of proxies.

The product could create, clone, duplicate and build from a template, but
every one of those makes a single profile. An operator arriving with a block
of proxies from a provider -- which is how a fleet is actually filled -- had to
type each one in.

Parsing is not reimplemented: ``proxy_util.parse_proxy`` already accepts every
shape a provider hands out, and it is the same function the launcher routes
through, so an imported profile cannot parse differently from one typed by
hand. It had no direct test coverage before this; the format table below is
now that coverage as well.

Two behaviours are deliberate and are asserted here. A bad line does not
reject the batch, because someone pasting 150 proxies usually has one typo and
failing all 150 means finding it by bisection. And a repeated proxy is skipped
rather than duplicated, because pasting the same block twice is a far more
common accident than wanting two profiles on one exit, and the costs are
asymmetric: a skipped row is a line in a report, a duplicated fleet is 150
profiles to delete by hand.
"""
from __future__ import annotations

import pathlib

import pytest

from multiprofile.profile_import import MAX_ROWS, import_profiles, parse_proxy_list


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")


# --------------------------------------------------------------------------
# 1. The formats a provider actually sends
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("line", "host", "port", "user", "password"), [
    ("1.2.3.4:8080", "1.2.3.4", 8080, None, None),
    ("1.2.3.4:8080:bob:hunter2", "1.2.3.4", 8080, "bob", "hunter2"),
    ("bob:hunter2@1.2.3.4:8080", "1.2.3.4", 8080, "bob", "hunter2"),
    ("http://1.2.3.4:8080", "1.2.3.4", 8080, None, None),
    ("socks5://bob:hunter2@1.2.3.4:1080", "1.2.3.4", 1080, "bob", "hunter2"),
    ("socks5h://1.2.3.4:1080", "1.2.3.4", 1080, None, None),
    ("proxy.example.com:3128", "proxy.example.com", 3128, None, None),
])
def test_the_shapes_a_provider_sends_are_understood(line, host, port, user, password):
    rows = parse_proxy_list(line)["rows"]
    assert len(rows) == 1, line
    proxy = rows[0]["proxy"]
    assert proxy["host"] == host
    assert proxy["port"] == port
    assert proxy["username"] == user
    assert proxy["password"] == password


@pytest.mark.parametrize("line", [
    "not-a-proxy",
    "1.2.3.4",
    "1.2.3.4:notaport",
    "1.2.3.4:99999",
    "1.2.3.4:0",
])
def test_a_line_that_is_not_a_proxy_is_reported(line):
    result = parse_proxy_list(line)
    assert result["rows"] == []
    assert len(result["errors"]) == 1
    assert result["errors"][0]["error"] == "unparseable"


def test_blank_lines_and_comments_are_ignored():
    """Operators annotate the blocks their provider sends them."""
    result = parse_proxy_list("""
# batch A, expires friday
1.2.3.4:8080

// leftover note
5.6.7.8:3128
""")
    assert len(result["rows"]) == 2
    assert result["errors"] == []


# --------------------------------------------------------------------------
# 2. Partial success
# --------------------------------------------------------------------------

def test_one_bad_line_does_not_reject_the_batch():
    report = import_profiles("1.2.3.4:8080\ngarbage\n5.6.7.8:3128")
    assert report["counts"]["created"] == 2
    assert report["counts"]["failed"] == 1


def test_every_row_is_reported_by_its_input_line():
    """So a caller fixes line 2 rather than re-pasting all of it."""
    report = import_profiles("1.2.3.4:8080\ngarbage\n5.6.7.8:3128")
    assert [c["line"] for c in report["created"]] == [1, 3]
    assert report["failed"][0]["line"] == 2
    assert report["failed"][0]["input"] == "garbage"


def test_a_repeated_proxy_is_skipped_and_points_at_the_first():
    report = import_profiles("1.2.3.4:8080\n5.6.7.8:3128\n1.2.3.4:8080")
    assert report["counts"]["created"] == 2
    assert report["counts"]["skipped"] == 1
    skipped = report["skipped"][0]
    assert skipped["line"] == 3
    assert "line 1" in skipped["message"]


def test_the_same_host_and_port_with_a_different_user_is_not_a_duplicate():
    """Rotating providers hand out one endpoint with many sessions."""
    report = import_profiles(
        "1.2.3.4:8080:sessionA:pw\n1.2.3.4:8080:sessionB:pw"
    )
    assert report["counts"]["created"] == 2
    assert report["counts"]["skipped"] == 0


def test_a_proxy_an_existing_profile_uses_is_warned_about_not_refused():
    """Sharing an exit is legitimate, if risky -- and refusing it would make
    re-importing after a partial failure impossible."""
    first = import_profiles("1.2.3.4:8080")
    assert first["counts"]["created"] == 1
    second = import_profiles("1.2.3.4:8080")
    assert second["counts"]["created"] == 1
    assert second["counts"]["warnings"] == 1
    assert second["warnings"][0]["warning"] == "proxy_already_used"


def test_one_request_is_bounded(monkeypatch):
    # MAX_ROWS bounds one *request*; the fleet capacity bounds the store, and
    # at its default of 150 it would be the binding limit here and this test
    # would be measuring the wrong one. Raised so the row bound is what fails.
    monkeypatch.setenv("MAX_PROFILES", "2000")
    lines = "\n".join(f"10.0.{i // 256}.{i % 256}:8080" for i in range(MAX_ROWS + 25))
    report = import_profiles(lines)
    assert report["counts"]["created"] == MAX_ROWS
    assert all(s["reason"] == "over_limit" for s in report["skipped"])


# --------------------------------------------------------------------------
# 3. What ends up on the profile
# --------------------------------------------------------------------------

def test_the_credentials_reach_the_route():
    from multiprofile.network import network_config_for_profile
    from multiprofile.storage.profiles import get_profile

    report = import_profiles("5.6.7.8:1080:bob:hunter2")
    profile = get_profile(report["created"][0]["id"])
    net = network_config_for_profile(profile)
    assert net.proxy.host == "5.6.7.8"
    assert net.proxy.port == 1080
    assert net.proxy.username == "bob"
    assert net.proxy.password == "hunter2"


def test_the_password_is_not_written_to_disk_in_clear():
    """The first version of this stored the pasted line in `customProxy` as
    "what the operator typed", which put the password on disk in clear beside
    the encrypted copy of the same password: `_encrypt_profile_secrets` wraps
    `proxyPass` and nothing else. `backup.py` already treats `customProxy` as
    a secret field, which was the hint.

    Redacting it was not an option either -- `NetworkConfig.from_profile`
    prefers `customProxy` over the split fields, so a redacted string would be
    what the launch routed on.
    """
    from multiprofile.storage.db import db_path

    import_profiles("5.6.7.8:1080:bob:sup3rs3cret")
    root = pathlib.Path(db_path()).parent
    blob = b"".join(f.read_bytes() for f in root.glob("control.db*") if f.is_file())
    assert b"sup3rs3cret" not in blob


def test_a_bare_row_takes_the_batch_proxy_type():
    """Without this every bare row became HTTP, so importing a SOCKS5 list
    produced a fleet that silently spoke the wrong protocol to every proxy."""
    from multiprofile.storage.profiles import get_profile

    report = import_profiles("1.2.3.4:1080", proxy_type="socks5")
    assert get_profile(report["created"][0]["id"])["proxyType"] == "socks5"


def test_a_row_with_its_own_scheme_keeps_it():
    from multiprofile.storage.profiles import get_profile

    report = import_profiles("http://8.8.8.8:3128", proxy_type="socks5")
    assert get_profile(report["created"][0]["id"])["proxyType"] == "http"


def test_the_batch_fields_are_applied_to_every_profile():
    from multiprofile.storage.profiles import get_profile

    report = import_profiles(
        "1.2.3.4:8080\n5.6.7.8:3128",
        name_prefix="Shop",
        base={"tags": ["imported", "shop"], "clientId": "acme"},
    )
    for created in report["created"]:
        profile = get_profile(created["id"])
        assert profile["tags"] == ["imported", "shop"]
        assert profile["clientId"] == "acme"


def test_profiles_are_named_in_sequence():
    report = import_profiles("1.2.3.4:8080\n5.6.7.8:3128", name_prefix="Shop")
    assert [c["name"] for c in report["created"]] == ["Shop 1", "Shop 2"]


def test_each_imported_profile_gets_its_own_fingerprint():
    """Otherwise an imported fleet would be one machine behind 150 exits."""
    from multiprofile.storage.profiles import get_profile

    report = import_profiles("1.2.3.4:8080\n5.6.7.8:3128\n9.9.9.9:1080")
    seeds = {get_profile(c["id"])["seed"] for c in report["created"]}
    assert len(seeds) == 3


# --------------------------------------------------------------------------
# 4. The endpoint
# --------------------------------------------------------------------------

@pytest.fixture()
def client():
    from multiprofile.app import create_app

    return create_app().test_client()


def test_a_clean_import_is_200(client):
    resp = client.post("/api/profiles/import", json={"proxies": "1.2.3.4:8080"})
    assert resp.status_code == 200
    assert resp.get_json()["counts"]["created"] == 1


def test_a_partial_import_is_207_not_200(client):
    """A flat 200 would let a client treat "created 0 of 150" as success."""
    resp = client.post(
        "/api/profiles/import", json={"proxies": "1.2.3.4:8080\ngarbage"}
    )
    assert resp.status_code == 207
    body = resp.get_json()
    assert body["counts"]["created"] == 1
    assert body["counts"]["failed"] == 1


def test_no_proxies_is_a_400(client):
    resp = client.post("/api/profiles/import", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["field"] == "proxies"


def test_an_array_is_accepted_as_well_as_text(client):
    resp = client.post(
        "/api/profiles/import",
        json={"proxies": ["1.2.3.4:8080", "5.6.7.8:3128"]},
    )
    assert resp.status_code == 200
    assert resp.get_json()["counts"]["created"] == 2


def test_the_imported_profiles_appear_in_the_list(client):
    client.post("/api/profiles/import",
                json={"proxies": "1.2.3.4:8080\n5.6.7.8:3128", "namePrefix": "Imp"})
    names = [p["name"] for p in client.get("/api/profiles?limit=200").get_json()["items"]]
    assert "Imp 1" in names
    assert "Imp 2" in names
