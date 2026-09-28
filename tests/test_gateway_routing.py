"""F16: the route the operator picked must reach the resolver.

Nothing translated the console's own vocabulary. The wizard sent top-level
lower-case ``networkMode: "gateway"`` plus ``gatewayId``; the resolver read the
nested upper-case ``networkConfig.mode``, and ``gatewayId`` was only ever
consulted for a proxy *pool's* member. So a profile created as "gateway"
resolved to an ordinary DIRECT config with ``blocked_reason`` unset -- not
distinguishable from a profile the operator deliberately set to direct.
Measured before the fix::

    wizard: gateway  -> mode=DIRECT  blocked=None
    wizard: direct   -> mode=DIRECT  blocked=None
    wizard: socks5   -> mode=SOCKS5  blocked=None

Only the proxy branches worked, and only because the wizard also sent the legacy
flat ``proxyHost`` / ``proxyPort`` / ``proxyType`` fields.

The rule these tests pin down is one sentence: **if the profile asked for
something other than DIRECT and DIRECT is what came back, the request did not
resolve** -- which also covers ``networkMode: "socks5"`` with empty proxy fields,
a case nobody had filed. Whether a blocked route then stops the launch is F17's
job; see ``test_route_fail_closed.py``.
"""
from __future__ import annotations

import pytest

from multiprofile.api import validation as V
from multiprofile.network import NetworkMode, ProxyFallback, network_config_for_profile

HEADERS = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}


@pytest.fixture()
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")


@pytest.fixture()
def client(xdg):
    from multiprofile.api.server import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def gateway(xdg):
    from multiprofile.storage.gateways import create_gateway

    return create_gateway({
        "label": "gw-test", "providerType": "custom",
        "scheme": "socks5", "host": "10.0.0.9", "port": 1080,
        "username": "u", "password": "p",
    })


# --------------------------------------------------------------------------
# 1. A request that cannot resolve never becomes a silent DIRECT
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "profile,expect_blocked",
    [
        ({"id": "a", "networkMode": "socks5", "proxyType": "none"}, True),
        ({"id": "b", "networkMode": "http", "proxyType": "none"}, True),
        ({"id": "c", "networkMode": "gateway"}, True),
        ({"id": "d", "networkMode": "direct"}, False),
        ({"id": "e", "networkMode": "none"}, False),
        # No preference is not a request, so the legacy inference still rules.
        ({"id": "f", "proxyType": "none"}, False),
        (
            {"id": "g", "networkMode": "socks5", "proxyType": "socks5",
             "proxyHost": "127.0.0.1", "proxyPort": 1080},
            False,
        ),
    ],
)
def test_an_unmet_request_blocks_and_a_satisfied_one_does_not(xdg, profile, expect_blocked):
    net = network_config_for_profile(profile)
    assert bool(net.blocked_reason) is expect_blocked, (
        f"{profile} -> mode={net.mode.value} blocked={net.blocked_reason!r}"
    )


def test_no_route_preference_is_left_alone(xdg):
    """The common case must not start failing closed.

    "No preference" and "direct, please" are different statements, and only an
    unmet statement may block -- otherwise every ordinary profile would.
    """
    net = network_config_for_profile({"id": "plain", "proxyType": "none"})
    assert net.mode == NetworkMode.DIRECT
    assert net.blocked_reason is None


@pytest.mark.parametrize("spelling", ["gateway", "GATEWAY", "remote_gateway", "Remote_Gateway"])
def test_the_request_is_read_in_any_spelling(xdg, gateway, spelling):
    """The wizard says "gateway", NetworkConfig says "REMOTE_GATEWAY"."""
    net = network_config_for_profile(
        {"id": "p", "networkMode": spelling, "gatewayId": gateway["id"]}
    )
    assert net.mode == NetworkMode.REMOTE_GATEWAY


def test_a_nested_network_config_mode_is_still_read(xdg, gateway):
    """The upper-case nested form was the only one that ever worked."""
    net = network_config_for_profile(
        {"id": "p", "networkConfig": {"mode": "REMOTE_GATEWAY", "gatewayId": gateway["id"]}}
    )
    assert net.mode == NetworkMode.REMOTE_GATEWAY
    assert net.proxy is not None


# --------------------------------------------------------------------------
# 2. The gateway route the wizard was asking for all along
# --------------------------------------------------------------------------

def test_the_wizards_gateway_branch_reaches_the_gateway(xdg, gateway):
    net = network_config_for_profile(
        {"id": "p", "networkMode": "gateway", "gatewayId": gateway["id"]}
    )
    assert net.mode == NetworkMode.REMOTE_GATEWAY
    assert net.proxy is not None
    assert (net.proxy.host, net.proxy.port) == ("10.0.0.9", 1080)
    assert net.gateway_id == gateway["id"]


def test_the_route_carries_the_real_password(xdg, gateway):
    """get_gateway() masks by default, and "***" would reach Chrome as a password."""
    net = network_config_for_profile(
        {"id": "p", "networkMode": "gateway", "gatewayId": gateway["id"]}
    )
    assert net.proxy is not None
    assert net.proxy.password == "p"


def test_a_bare_gateway_id_is_a_request_for_that_gateway(xdg, gateway):
    net = network_config_for_profile({"id": "p", "gatewayId": gateway["id"]})
    assert net.mode == NetworkMode.REMOTE_GATEWAY


@pytest.mark.parametrize(
    "profile,fragment",
    [
        ({"id": "p", "networkMode": "gateway"}, "names none"),
        ({"id": "p", "networkMode": "gateway", "gatewayId": "gw_nope"}, "no longer exists"),
    ],
)
def test_a_gateway_that_cannot_be_resolved_blocks(xdg, profile, fragment):
    net = network_config_for_profile(profile)
    assert net.blocked_reason and fragment in net.blocked_reason


def test_a_gateway_with_no_endpoint_blocks(xdg):
    from multiprofile.storage.gateways import create_gateway

    gw = create_gateway({"label": "empty", "providerType": "custom", "scheme": "socks5"})
    net = network_config_for_profile({"id": "p", "gatewayId": gw["id"]})
    assert net.blocked_reason and "no endpoint" in net.blocked_reason


def test_a_gateway_fails_closed_exactly_as_a_pool_does(xdg, gateway):
    """Not ALLOW. A dead gateway blocks; it never falls back."""
    net = network_config_for_profile(
        {"id": "p", "networkMode": "gateway", "gatewayId": gateway["id"]}
    )
    assert net.proxy_fallback == ProxyFallback.BLOCK


def test_a_pool_still_wins_over_a_profiles_own_gateway(xdg, gateway):
    """The operator put the profile in a pool precisely so the pool decides."""
    from multiprofile.storage.pools import create_pool

    pool = create_pool({
        "label": "p1",
        "members": [{"scheme": "socks5", "host": "10.9.9.9", "port": 1080, "enabled": True}],
    })
    net = network_config_for_profile(
        {"id": "p", "poolId": pool["id"], "networkMode": "gateway", "gatewayId": gateway["id"]}
    )
    assert net.proxy is not None
    assert net.proxy.host == "10.9.9.9", "the profile's own gateway overrode its pool"


# --------------------------------------------------------------------------
# 3. The API boundary
# --------------------------------------------------------------------------

def test_the_route_keys_are_honoured_not_warned(client, gateway):
    r = client.post(
        "/api/profiles",
        json={"name": "wiz", "networkMode": "gateway", "gatewayId": gateway["id"]},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.get_json()
    warned = " ".join(r.get_json()["warnings"])
    assert "networkMode" not in warned and "gatewayId" not in warned, warned


def test_they_left_the_unimplemented_tier(client):
    assert "networkMode" not in V.PROFILE_FIELDS_UNIMPLEMENTED
    assert "gatewayId" not in V.PROFILE_FIELDS_UNIMPLEMENTED
    assert {"networkMode", "gatewayId"} <= V.PROFILE_FIELDS


def test_a_gateway_mode_with_no_gateway_is_refused_at_the_door(client):
    """Refusing at create beats refusing at launch, with the same outcome."""
    r = client.post(
        "/api/profiles", json={"name": "wiz", "networkMode": "gateway"}, headers=HEADERS
    )
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "gatewayId"


def test_an_unknown_network_mode_is_refused(client):
    r = client.post(
        "/api/profiles", json={"name": "wiz", "networkMode": "carrier-pigeon"}, headers=HEADERS
    )
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "networkMode"


@pytest.mark.parametrize("mode", ["direct", "http", "https", "socks5", "gateway"])
def test_every_mode_the_wizard_can_send_is_accepted(client, gateway, mode):
    """``NetMode`` in wizard-dialog.tsx -- none of its branches may 400."""
    body = {"name": f"wiz-{mode}", "networkMode": mode}
    if mode == "gateway":
        body["gatewayId"] = gateway["id"]
    else:
        body.update({"proxyType": mode if mode != "direct" else "none",
                     "proxyHost": "" if mode == "direct" else "127.0.0.1",
                     "proxyPort": None if mode == "direct" else 1080})
    r = client.post("/api/profiles", json=body, headers=HEADERS)
    assert r.status_code == 200, r.get_json()


def test_the_stored_profile_routes_through_the_gateway(client, gateway):
    """End to end: what the wizard sends is what the launcher will use."""
    from multiprofile.storage.profiles import get_profile

    pid = client.post(
        "/api/profiles",
        json={"name": "wiz", "networkMode": "gateway", "gatewayId": gateway["id"]},
        headers=HEADERS,
    ).get_json()["profile"]["id"]
    net = network_config_for_profile(get_profile(pid))
    assert net.mode == NetworkMode.REMOTE_GATEWAY
    assert net.proxy is not None and net.proxy.host == "10.0.0.9"


def test_fingerprint_strategy_is_still_open(client):
    """The other half of F16 is a product decision and is not fixed here."""
    assert "fingerprintStrategy" in V.PROFILE_FIELDS_UNIMPLEMENTED
    r = client.post(
        "/api/profiles", json={"name": "fp", "fingerprintStrategy": "stable"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert any("fingerprintStrategy" in w for w in r.get_json()["warnings"])
