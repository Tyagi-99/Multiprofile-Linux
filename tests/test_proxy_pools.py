"""Shared proxy pools: sticky assignment, and a pool that never leaks the host IP.

The safety property under test is narrow and absolute: a profile attached to
a pool that cannot supply a route must NOT launch. Not "launch direct", not
"launch with a warning" -- blocked. A proxy pool whose failure mode is the
operator's real IP is worse than no pool at all.
"""
from __future__ import annotations

import pytest

from multiprofile.network import (
    NetworkMode,
    ProxyFallback,
    check_proxy_before_launch,
    network_config_for_profile,
)
from multiprofile.storage import pools


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


def _pool(**kw):
    members = kw.pop(
        "members",
        [
            {"label": "a", "scheme": "socks5", "host": "198.51.100.1", "port": 1080},
            {"label": "b", "scheme": "socks5", "host": "198.51.100.2", "port": 1080},
            {"label": "c", "scheme": "socks5", "host": "198.51.100.3", "port": 1080},
        ],
    )
    return pools.create_pool({"label": "US", "members": members, **kw})


# ---------------------------------------------------------------------------
# fail-closed — the reason this module exists
# ---------------------------------------------------------------------------


def test_empty_pool_blocks_the_launch_and_does_not_go_direct(xdg):
    p = _pool(members=[])
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})

    allowed, message, _ = check_proxy_before_launch(net)

    assert allowed is False
    assert net.proxy is None
    assert "no enabled member" in message


def test_pool_with_every_member_disabled_blocks(xdg):
    p = _pool(
        members=[
            {"label": "a", "host": "198.51.100.1", "port": 1080, "enabled": False},
            {"label": "b", "host": "198.51.100.2", "port": 1080, "enabled": False},
        ]
    )
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})
    allowed, message, _ = check_proxy_before_launch(net)

    assert allowed is False
    assert "2 disabled" in message


def test_deleted_pool_blocks(xdg):
    p = _pool()
    pools.delete_pool(p["id"])
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})

    allowed, message, _ = check_proxy_before_launch(net)

    assert allowed is False
    assert "no longer exists" in message


def test_member_without_a_host_blocks(xdg):
    p = _pool(members=[{"label": "broken", "scheme": "socks5", "port": 1080}])
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})

    allowed, message, _ = check_proxy_before_launch(net)

    assert allowed is False
    assert "no usable endpoint" in message


def test_member_pointing_at_a_missing_gateway_blocks(xdg):
    p = _pool(members=[{"label": "gw", "gatewayId": "gw_does_not_exist"}])
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})

    allowed, _, _ = check_proxy_before_launch(net)
    assert allowed is False


def test_a_blocked_config_is_not_mistaken_for_direct(xdg):
    """DIRECT normally sails through. blocked_reason must be checked first."""
    p = _pool(members=[])
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})

    assert net.mode is NetworkMode.DIRECT  # looks direct...
    assert net.needs_proxy_check() is False  # ...and skips the probe...
    assert check_proxy_before_launch(net)[0] is False  # ...but still blocks.


def test_pool_route_keeps_the_fail_closed_fallback(xdg):
    p = _pool()
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})
    assert net.proxy_fallback is ProxyFallback.BLOCK


def test_pool_overrides_the_profiles_own_proxy_fields(xdg):
    """In a pool, the pool decides — a stale inline proxy must not win."""
    p = _pool()
    net = network_config_for_profile(
        {
            "id": "prof-1",
            "poolId": p["id"],
            "proxyType": "http",
            "proxyHost": "10.0.0.1",
            "proxyPort": 3128,
        }
    )
    assert net.proxy is not None
    assert net.proxy.host.startswith("198.51.100.")


# ---------------------------------------------------------------------------
# sticky assignment — a profile keeps its egress
# ---------------------------------------------------------------------------


def test_the_same_profile_keeps_the_same_member(xdg):
    p = _pool()
    first, _ = pools.assign(p["id"], "prof-1")
    for _ in range(5):
        again, reason = pools.assign(p["id"], "prof-1")
        assert again["id"] == first["id"]
        assert "keeping" in reason


def test_assignment_survives_a_reload(xdg):
    p = _pool()
    first, _ = pools.assign(p["id"], "prof-1")
    reloaded = pools.get_pool(p["id"])
    assert reloaded["assignments"]["prof-1"] == first["id"]


def test_profiles_spread_across_members(xdg):
    p = _pool()
    for i in range(6):
        pools.assign(p["id"], f"prof-{i}")
    usage = pools.pool_usage(p["id"])
    assert sorted(usage.values()) == [2, 2, 2]


def test_a_disabled_assigned_member_blocks_instead_of_silently_moving(xdg):
    """Reassigning behind the operator's back would change the egress IP."""
    p = _pool()
    member, _ = pools.assign(p["id"], "prof-1")
    members = [
        {**m, "enabled": m["id"] != member["id"]} for m in pools.get_pool(p["id"])["members"]
    ]
    pools.update_pool(p["id"], {"members": members})

    chosen, reason = pools.choose_member(pools.get_pool(p["id"]), "prof-1")

    assert chosen is None
    assert "is disabled" in reason
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})
    assert check_proxy_before_launch(net)[0] is False


def test_releasing_an_assignment_lets_the_profile_pick_again(xdg):
    p = _pool()
    first, _ = pools.assign(p["id"], "prof-1")
    assert pools.release(p["id"], "prof-1") is True
    assert "prof-1" not in pools.get_pool(p["id"])["assignments"]
    again, reason = pools.assign(p["id"], "prof-1")
    assert "new assignment" in reason


def test_deleting_a_member_drops_its_assignments(xdg):
    p = _pool()
    pools.assign(p["id"], "prof-1")
    keep = pools.get_pool(p["id"])["members"][1:]
    updated = pools.update_pool(p["id"], {"members": keep})
    live = {m["id"] for m in updated["members"]}
    assert all(mid in live for mid in updated["assignments"].values())


# ---------------------------------------------------------------------------
# rotation — opt-in only
# ---------------------------------------------------------------------------


def test_sticky_is_the_default_strategy(xdg):
    assert _pool()["strategy"] == pools.STRATEGY_STICKY


def test_an_unknown_strategy_falls_back_to_sticky(xdg):
    assert _pool(strategy="chaos")["strategy"] == pools.STRATEGY_STICKY


def test_rotate_advances_and_wraps(xdg):
    p = _pool(strategy="rotate")
    seen = [pools.assign(p["id"], "prof-1")[0]["label"] for _ in range(4)]
    assert seen == ["a", "b", "c", "a"]


def test_rotate_records_no_assignments(xdg):
    p = _pool(strategy="rotate")
    pools.assign(p["id"], "prof-1")
    assert pools.get_pool(p["id"])["assignments"] == {}


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def test_passwords_are_never_in_the_api_shape(xdg):
    p = _pool(members=[{"label": "a", "host": "198.51.100.1", "port": 1080,
                        "username": "u", "password": "s3cret"}])
    safe = pools.to_safe_dict(pools.get_pool(p["id"]))

    assert "s3cret" not in repr(safe)
    assert safe["members"][0]["hasPassword"] is True
    assert "password" not in safe["members"][0]
    assert safe["members"][0]["username"] == "u"


def test_the_password_still_reaches_the_launch_route(xdg):
    p = _pool(members=[{"label": "a", "host": "198.51.100.1", "port": 1080,
                        "username": "u", "password": "s3cret"}])
    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})
    assert net.proxy.password == "s3cret"
    # ...and still never in the serialised form.
    assert "s3cret" not in repr(net.to_dict())


def test_pools_file_is_not_world_readable(xdg):
    _pool()
    assert (pools.pools_path().stat().st_mode & 0o077) == 0


# ---------------------------------------------------------------------------
# gateway-backed members
# ---------------------------------------------------------------------------


def test_a_member_can_borrow_a_gateways_endpoint(xdg):
    from multiprofile.storage.gateways import create_gateway

    gw = create_gateway({
        "label": "Shared EU", "providerType": "custom", "scheme": "socks5",
        "host": "203.0.113.9", "port": 1080, "username": "gu", "password": "gp",
    })
    p = _pool(members=[{"label": "via gw", "gatewayId": gw["id"]}])

    net = network_config_for_profile({"id": "prof-1", "poolId": p["id"]})

    assert net.proxy.host == "203.0.113.9"
    assert net.proxy.username == "gu"
    assert net.proxy.password == "gp"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_crud_round_trip(xdg):
    p = _pool()
    assert pools.get_pool(p["id"])["label"] == "US"
    pools.update_pool(p["id"], {"label": "EU"})
    assert pools.get_pool(p["id"])["label"] == "EU"
    assert pools.delete_pool(p["id"]) is True
    assert pools.get_pool(p["id"]) is None
    assert pools.delete_pool(p["id"]) is False


def test_missing_file_reads_as_empty(xdg):
    assert pools.load_pools() == []


def test_corrupt_file_reads_as_empty_rather_than_raising(xdg):
    pools.pools_path().parent.mkdir(parents=True, exist_ok=True)
    pools.pools_path().write_text("{ not json", encoding="utf-8")
    assert pools.load_pools() == []


def test_a_profile_without_a_pool_is_untouched(xdg):
    """The pool path must not disturb ordinary profiles."""
    direct = network_config_for_profile({"id": "p", "proxyType": "none"})
    assert direct.mode is NetworkMode.DIRECT
    assert direct.blocked_reason is None
    assert check_proxy_before_launch(direct)[0] is True
