"""F17: a route that could not be resolved must not launch on the host IP.

``NetworkConfig`` records an unresolvable route as ``blocked_reason`` on a
DIRECT-looking config -- ``mode=DIRECT``, ``proxy=None``. That shape is
deliberate: ``needs_proxy_check()`` is False when ``proxy`` is None, so a proxy
mode carrying no proxy would sail through the gate, and the refusal is therefore
stated outright rather than encoded as an empty proxy.
``check_proxy_before_launch`` honours it, checks it first, and says so in its
docstring.

The launcher then called that gate only::

    if settings.proxy_check_enabled and net.needs_proxy_check():

which a blocked config can never satisfy, and which a config switch could turn
off outright. Measured before the fix, on a profile in a pool with no enabled
member::

    resolved : mode=DIRECT blocked_reason="Pool 'dead-pool' has no enabled member"
    LAUNCH ok : True | error: None
    proxy flag: (NONE - browsing from the host IP)
    notes     : "Network mode: DIRECT"
                "identity verified on the wire: sec-ch-ua … Windows …"

A perfect fingerprint on the operator's own IP, reported as a healthy launch with
DIRECT presented as the intent. The pool's fail-closed guard was correct and
unreachable; this is the test that reaches it.
"""
from __future__ import annotations

import pytest

from multiprofile.network import NetworkConfig, NetworkMode, network_config_for_profile


@pytest.fixture()
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")


def _dead_pool_profile():
    """A profile whose pool cannot supply a member -- reachable from the console."""
    from multiprofile.storage.pools import create_pool
    from multiprofile.storage.profiles import create_profile

    pool = create_pool({"label": "dead-pool", "members": []})
    return create_profile({"name": "pooled", "proxyType": "none", "poolId": pool["id"]})


def test_a_pool_with_no_member_blocks_the_launch(xdg):
    """The measurement in the module docstring, as an assertion."""
    import multiprofile.browser.launcher as L

    profile = _dead_pool_profile()
    net = network_config_for_profile(profile)
    assert net.blocked_reason, "the pool guard itself regressed"
    assert net.needs_proxy_check() is False, (
        "this test is only meaningful while a blocked config still looks DIRECT"
    )

    res = L.launch_profile(profile["id"], url="about:blank")
    assert res.get("ok") is False, f"launched on the host IP: {res}"
    assert res.get("errors") == ["route_unresolved"]
    assert "no enabled member" in (res.get("error") or "")


def test_disabling_the_probe_does_not_re_open_it(xdg, monkeypatch):
    """Turning off the proxy *probe* is not consent to use the host IP.

    The old check sat behind ``settings.proxy_check_enabled``, so this one
    switch silently restored the leak for every profile at once.
    """
    import multiprofile.browser.launcher as L

    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    profile = _dead_pool_profile()
    res = L.launch_profile(profile["id"], url="about:blank")
    assert res.get("ok") is False, f"launched on the host IP: {res}"
    assert res.get("errors") == ["route_unresolved"]


def test_a_blocked_launch_gives_back_its_slot(xdg):
    """The refusal happens after the slot is taken, so it must release it.

    A leaked slot would be the worse failure at scale: a pool that stops
    resolving would spend the whole concurrency budget on launches that never
    happened, and the next real launch would wait 120 s for a slot.
    """
    import multiprofile.browser.launcher as L
    from multiprofile.browser import manager as browser_manager

    profile = _dead_pool_profile()
    before = browser_manager.slot_status()["active"]
    res = L.launch_profile(profile["id"], url="about:blank")
    assert res.get("errors") == ["route_unresolved"], res
    assert browser_manager.slot_status()["active"] == before
    assert profile["id"] not in browser_manager.active_ids()


def test_a_blocked_launch_is_recorded_as_an_error(xdg):
    """Reporting it as healthy is the defect; silence would be only better."""
    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime

    profile = _dead_pool_profile()
    L.launch_profile(profile["id"], url="about:blank")
    rt = get_runtime(profile["id"])
    assert rt is None or "route_unresolved" in str(rt.error or rt.launch_state or "")


def test_the_ordinary_dead_proxy_path_is_not_shadowed(xdg):
    """The new check must not swallow the health check that already worked."""
    from multiprofile.network import ProxyConfig, check_proxy_before_launch

    net = NetworkConfig(
        mode=NetworkMode.SOCKS5,
        proxy=ProxyConfig(scheme="socks5", host="127.0.0.1", port=1),
    )
    assert net.blocked_reason is None
    allowed, reason, _ = check_proxy_before_launch(net, timeout=1.0)
    assert allowed is False and reason
