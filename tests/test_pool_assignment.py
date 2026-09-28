"""A proxy pool must spread profiles across its members, and remember.

``choose_member`` is deliberately pure -- no disk write, no network -- so that
reading a profile's route stays a read. The writer, ``pools.assign()``, was
written for the launch path and then never called by it: the only route in was
``POST /api/pools/<id>/assign``, which no UI code and no launch touches.

So the pool never learned anything. ``assignments`` stayed ``{}`` forever and
``cursor`` never moved, which meant the "least loaded" tie-break scored every
member at zero and picked the first. Measured before the fix::

    5 profiles in a 3-member STICKY pool:   all five -> 10.0.0.1
    assignments recorded:                    {}
    3 profiles in a 2-member ROTATE pool:   all three -> 10.1.0.1  (cursor: 0)

Every profile in a pool egressed through one member. That is worse than a
missing feature: profiles an operator deliberately spread across a pool were
sharing a single IP, which **correlates them** -- the one outcome a pool exists
to prevent, and the same class of defect as F17 except that this one was on the
happy path.

The rule is therefore two-sided, and both sides are load-bearing:
a launch commits the decision; a look never does.
"""
from __future__ import annotations

import pytest

from multiprofile.network import network_config_for_profile
from multiprofile.storage.pools import create_pool, get_pool
from multiprofile.storage.profiles import create_profile


@pytest.fixture()
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")


def _pool(strategy: str, hosts: list[str]) -> dict:
    return create_pool({
        "label": f"{strategy}-pool",
        "strategy": strategy,
        "members": [
            {"scheme": "socks5", "host": h, "port": 1080, "enabled": True} for h in hosts
        ],
    })


def _profile(name: str, pool_id: str) -> dict:
    return create_profile({"name": name, "proxyType": "none", "poolId": pool_id})


def _egress(profile: dict, *, commit: bool = False) -> str:
    net = network_config_for_profile(profile, commit=commit)
    assert net.proxy is not None, f"no route resolved: {net.blocked_reason}"
    return net.proxy.host


# --------------------------------------------------------------------------
# 1. A launch spreads profiles across the pool
# --------------------------------------------------------------------------

def test_a_sticky_pool_does_not_put_every_profile_on_one_member(xdg):
    """The measurement in the module docstring, as an assertion."""
    hosts = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    pool = _pool("sticky", hosts)
    used = [_egress(_profile(f"p{i}", pool["id"]), commit=True) for i in range(6)]

    assert len(set(used)) == len(hosts), f"pool collapsed onto {set(used)}"
    # Six profiles over three members: evenly, not merely "more than one".
    assert sorted(used) == sorted(hosts * 2), used


def test_the_assignment_is_recorded(xdg):
    """An unrecorded decision is why the tie-break kept scoring zero."""
    pool = _pool("sticky", ["10.0.0.1", "10.0.0.2"])
    profiles = [_profile(f"p{i}", pool["id"]) for i in range(4)]
    for p in profiles:
        _egress(p, commit=True)

    assignments = get_pool(pool["id"])["assignments"]
    assert len(assignments) == 4
    assert set(assignments) == {p["id"] for p in profiles}


def test_a_profile_keeps_its_egress_across_launches(xdg):
    """Sticky is the whole point: the same profile, the same IP, tomorrow."""
    pool = _pool("sticky", ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
    profiles = [_profile(f"p{i}", pool["id"]) for i in range(5)]

    first = [_egress(p, commit=True) for p in profiles]
    for _ in range(3):
        assert [_egress(p, commit=True) for p in profiles] == first


def test_pool_usage_reflects_reality(xdg):
    """The pools UI reads this; it showed 0 for every member."""
    from multiprofile.storage.pools import pool_usage

    pool = _pool("sticky", ["10.0.0.1", "10.0.0.2"])
    for i in range(4):
        _egress(_profile(f"p{i}", pool["id"]), commit=True)

    usage = pool_usage(pool["id"])
    assert sum(usage.values()) == 4, usage
    assert all(v > 0 for v in usage.values()), usage


# --------------------------------------------------------------------------
# 2. A rotate pool actually rotates
# --------------------------------------------------------------------------

def test_a_rotate_pool_advances_on_each_launch(xdg):
    pool = _pool("rotate", ["10.1.0.1", "10.1.0.2"])
    profile = _profile("r", pool["id"])
    assert [_egress(profile, commit=True) for _ in range(4)] == [
        "10.1.0.1", "10.1.0.2", "10.1.0.1", "10.1.0.2",
    ]


def test_the_rotate_cursor_is_persisted(xdg):
    pool = _pool("rotate", ["10.1.0.1", "10.1.0.2", "10.1.0.3"])
    profile = _profile("r", pool["id"])
    _egress(profile, commit=True)
    assert int(get_pool(pool["id"])["cursor"]) == 1


# --------------------------------------------------------------------------
# 3. A look is not a launch
# --------------------------------------------------------------------------

def test_reading_a_route_records_nothing(xdg):
    """network_config_for_profile is also called by the health poller, the
    diagnostics route and /api/profiles/<id>/network-health. If a read
    committed, a rotate pool would hand a profile a different egress every
    time anyone opened its page.
    """
    pool = _pool("sticky", ["10.0.0.1", "10.0.0.2"])
    profile = _profile("p", pool["id"])

    for _ in range(5):
        _egress(profile)  # commit defaults to False
    assert get_pool(pool["id"])["assignments"] == {}


def test_reading_does_not_advance_a_rotate_cursor(xdg):
    pool = _pool("rotate", ["10.1.0.1", "10.1.0.2"])
    profile = _profile("r", pool["id"])

    seen = {_egress(profile) for _ in range(5)}
    assert seen == {"10.1.0.1"}, seen
    assert int(get_pool(pool["id"])["cursor"]) == 0


def test_a_read_after_a_launch_agrees_with_the_launch(xdg):
    """The page must show the egress the profile is actually using."""
    pool = _pool("sticky", ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
    profiles = [_profile(f"p{i}", pool["id"]) for i in range(3)]
    launched = [_egress(p, commit=True) for p in profiles]
    assert [_egress(p) for p in profiles] == launched


# --------------------------------------------------------------------------
# 4. The fail-closed rule from F17 still holds
# --------------------------------------------------------------------------

def test_an_empty_pool_still_blocks_rather_than_going_direct(xdg):
    pool = _pool("sticky", [])
    net = network_config_for_profile(_profile("p", pool["id"]), commit=True)
    assert net.proxy is None
    assert net.blocked_reason and "no enabled member" in net.blocked_reason


def test_a_disabled_member_is_never_assigned(xdg):
    pool = create_pool({
        "label": "mixed", "strategy": "sticky",
        "members": [
            {"scheme": "socks5", "host": "10.0.0.1", "port": 1080, "enabled": False},
            {"scheme": "socks5", "host": "10.0.0.2", "port": 1080, "enabled": True},
        ],
    })
    used = {_egress(_profile(f"p{i}", pool["id"]), commit=True) for i in range(4)}
    assert used == {"10.0.0.2"}, used
