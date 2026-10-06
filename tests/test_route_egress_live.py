"""Where the traffic actually left from, observed at the destination.

Phase 3 of the parity roadmap: *"a prelaunch check cannot prove that a later
disconnect, WebRTC path, DNS lookup, IPv6 connection, WebSocket, worker, or
download stays on the intended route"* and *"this gate should be judged by
measured traffic, not only the app's health-check response."*

Everything already in the suite tests the **decision** -- whether a launch is
blocked, whether a flag is emitted. Nothing tested the **egress**. These do,
by giving each hop its own loopback source address so the destination's
``remote_addr`` names the hop that reached it:

    direct                   127.0.0.1
    HTTP proxy               127.0.0.2
    SOCKS5 proxy             127.0.0.3
    HTTP proxy + auth        127.0.0.4
    SOCKS5 proxy + auth      127.0.0.5

See ``tests/_route_lab.py``. What this does **not** cover is a real
third-party proxy over a real network: no NAT, no MTU, no latency, no IPv6
path, and no provider that can fail in ways a local process cannot. Real-world
proxy validation is outstanding and recorded as such in
``docs/NETWORK_ISOLATION.md``.
"""
from __future__ import annotations

import json
import time
import urllib.request

import pytest

pytestmark = pytest.mark.network


def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


pytest.importorskip("websocket")
if not _chrome_available():
    pytest.skip("Chrome is not installed", allow_module_level=True)

from _route_lab import (  # noqa: E402
    Destination,
    HttpConnectProxy,
    Socks5Proxy,
    loopback_alias_available,
)

if not all(loopback_alias_available(a) for a in
           ("127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5")):
    pytest.skip(
        "this host does not allow binding loopback aliases, so a destination "
        "cannot attribute a hop and every assertion here would be vacuous",
        allow_module_level=True,
    )


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A destination, four proxies, and an isolated store.

    **One lab artefact, stated plainly.** Chrome implicitly bypasses the
    proxy for loopback and link-local destinations, so a profile pointed at
    127.0.0.1 connects directly however its proxy is configured. That is
    correct behaviour for real browsing and the product does not change it --
    but it means nothing on loopback is observable without turning it off, so
    this fixture appends ``--proxy-bypass-list=<-loopback>`` to whatever the
    launcher built.

    It *appends*: every routing flag the product emits is still emitted and
    still exercised. What is disabled is an unrelated default, and the
    consequence is that these tests do not cover the bypass list itself --
    a real proxy on a real network would.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    # The pre-launch probe would reject a proxy that is up but has nothing
    # behind it yet; these tests are about egress, and the fail-closed
    # decision has its own suite (test_route_fail_closed.py).
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    # See the docstring: without this, loopback never reaches a proxy.
    import multiprofile.network as net_mod

    original_flags = net_mod.chrome_network_flags

    def flags_without_loopback_bypass(network_config):
        flags = list(original_flags(network_config))
        if any(f.startswith("--proxy-server=") for f in flags):
            flags.append("--proxy-bypass-list=<-loopback>")
        return flags

    monkeypatch.setattr(net_mod, "chrome_network_flags",
                        flags_without_loopback_bypass)
    import multiprofile.browser.launcher as launcher_mod

    if hasattr(launcher_mod, "chrome_network_flags"):
        monkeypatch.setattr(launcher_mod, "chrome_network_flags",
                            flags_without_loopback_bypass)

    destination = Destination()
    proxies = {
        "http": HttpConnectProxy("127.0.0.2"),
        "socks5": Socks5Proxy("127.0.0.3"),
        "http_auth": HttpConnectProxy("127.0.0.4", username="lab-http"),
        "socks5_auth": Socks5Proxy("127.0.0.5", username="lab-socks"),
    }
    try:
        yield {"destination": destination, "proxies": proxies}
    finally:
        for proxy in proxies.values():
            proxy.stop()
        destination.close()


def _fetch_in_page(port: int, url: str, timeout: float = 25.0,
                   attempts: int = 3) -> dict:
    """Make the *browser* fetch the URL, and report what it saw.

    Driven from inside the page rather than from Python, so the request goes
    through the profile's own network stack.

    Retried a bounded number of times, and only on a failed fetch. What that
    tolerates is one specific race: proxy authentication is answered by the
    CDP ``Fetch.authRequired`` handler, and on a slow host a first request can
    reach the proxy before that handler is armed -- the proxy returns 407,
    nothing answers it, and the fetch fails. Observed in CI and not
    reproducible locally (3 of 3 runs passed).

    It does **not** tolerate the thing these tests are about. The
    destination-observed source assertions are unchanged, so a request that
    arrives from the wrong hop still fails on the first attempt and every
    one after it. Only "the request did not arrive at all" is retried.
    """
    import websocket

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
    )
    targets = json.loads(urllib.request.urlopen(request, timeout=5).read())
    target = next(t for t in targets if t.get("type") == "page")
    ws = websocket.create_connection(
        target["webSocketDebuggerUrl"], timeout=timeout + 10, suppress_origin=True
    )
    try:
        expression = (
            "(async () => { try {"
            f"  const r = await fetch({json.dumps(url)}, {{cache: 'no-store'}});"
            "   return JSON.stringify({ok: r.ok, status: r.status});"
            "} catch (e) { return JSON.stringify({ok: false, error: String(e)}); }"
            "})()"
        )
        outcome: dict = {"ok": False, "error": "not attempted"}
        for attempt in range(1, attempts + 1):
            ws.send(json.dumps({
                "id": attempt, "method": "Runtime.evaluate",
                "params": {"expression": expression, "awaitPromise": True,
                           "returnByValue": True},
            }))
            deadline = time.time() + timeout
            outcome = {"ok": False, "error": "timeout"}
            while time.time() < deadline:
                message = json.loads(ws.recv())
                if message.get("id") != attempt:
                    continue
                result = message.get("result") or {}
                if result.get("exceptionDetails"):
                    outcome = {"ok": False,
                               "error": str(result["exceptionDetails"])}
                else:
                    raw = (result.get("result") or {}).get("value")
                    outcome = (json.loads(raw) if raw
                               else {"ok": False, "error": "no value"})
                break
            if outcome.get("ok"):
                return outcome
            # Give the auth handler a moment to arm before trying again.
            time.sleep(1.0)
        return outcome
    finally:
        ws.close()


def _launch(payload: dict, start_url: str) -> tuple[str, int]:
    """Launch on the destination's own origin.

    `about:blank` is an opaque origin and cannot `fetch` an http:// URL at
    all -- the first version of this file measured nothing but
    "TypeError: Failed to fetch". Starting on the destination means the
    initial navigation is itself routed traffic, which is the stronger
    evidence anyway: it is the request a profile makes before any script of
    ours has run.
    """
    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    profile_id = create_profile({**payload, "startUrl": start_url})["id"]
    result = L.launch_profile(profile_id, url=start_url, direct=False)
    assert result.get("ok") is True, result
    time.sleep(2.5)
    return profile_id, get_runtime(profile_id).cdp_port


def _route_evidence(lab, name: str, result: dict | None = None) -> str:
    """What the proxy and the destination actually saw.

    The authenticated-HTTP cases fail in CI and not here, with
    ``TypeError: Failed to fetch`` and "no request arrived". Those are two
    different stories -- the browser never reached the proxy, or it reached it
    and the auth exchange failed -- and the assertion message could not tell
    them apart. A reduced failure count cannot either, which is why this
    records the counters instead of inferring from how often it happens.
    """
    proxy = lab["proxies"].get(name)
    destination = lab["destination"]
    bits = [f"route={name}"]
    if result is not None:
        bits.append(f"fetch={result}")
    if proxy is not None:
        bits.append(
            f"proxy[connections={getattr(proxy, 'connections', '?')}, "
            f"auth_failures={getattr(proxy, 'auth_failures', '?')}, "
            f"rejected={getattr(proxy, 'rejected', '?')}, "
            f"authenticated={bool(getattr(proxy, 'username', None))}]"
        )
    bits.append(f"destination_sources={destination.sources_for(f'route={name}')}")
    return " ".join(bits)


ROUTES = (
    # name,          profile fields,            expected destination-observed source
    ("direct", {"proxyType": "none"}, "127.0.0.1"),
    ("http", {"proxyType": "http"}, "127.0.0.2"),
    ("socks5", {"proxyType": "socks5"}, "127.0.0.3"),
    ("http_auth", {"proxyType": "http"}, "127.0.0.4"),
    # socks5_auth is deliberately absent: Chromium cannot use it at all, and
    # the launch is refused. See test_authenticated_socks5_is_refused below.
)


@pytest.mark.parametrize("name,fields,expected_source", ROUTES)
def test_the_destination_sees_the_intended_hop(lab, name, fields, expected_source):
    """The Phase 3 gate: measured egress, not a health response."""
    import multiprofile.browser.launcher as L

    destination = lab["destination"]
    payload = {"name": f"route-{name}", **fields}
    if name != "direct":
        proxy = lab["proxies"][name]
        payload |= {"proxyHost": "127.0.0.1", "proxyPort": proxy.port}
        if proxy.username:
            payload |= {"proxyUser": proxy.username, "proxyPass": proxy.password}

    nav = f"http://127.0.0.1:{destination.port}/dest?route={name}"
    profile_id, port = _launch(payload, nav)
    try:
        # The navigation already happened; a same-origin fetch adds a second
        # request through the same route, so a one-off is distinguishable
        # from the steady state.
        result = _fetch_in_page(port, f"/dest?route={name}&fetch=1")
        assert result.get("ok") is True, _route_evidence(lab, name, result)
        sources = destination.sources_for(f"route={name}")
        assert sources, (
            f"{name}: the destination saw no request at all. "
            + _route_evidence(lab, name, result))
        assert set(sources) == {expected_source}, (
            f"{name}: expected every request from {expected_source}, saw {sources}. "
            + _route_evidence(lab, name, result)
        )
        assert len(sources) >= 2, (
            f"{name}: only {len(sources)} request(s) observed, so the "
            "navigation or the fetch did not arrive. "
            + _route_evidence(lab, name, result)
        )
    finally:
        L.stop_profile(profile_id)


@pytest.mark.parametrize("name", ("http", "socks5", "http_auth"))
def test_a_proxied_profile_never_reaches_the_destination_directly(lab, name):
    """The assertion that matters more than the positive one.

    "The proxy was used" and "the host address was never used" are different
    claims, and only the second is a leak test.
    """
    import multiprofile.browser.launcher as L

    destination = lab["destination"]
    proxy = lab["proxies"][name]
    payload = {
        "name": f"leak-{name}",
        "proxyType": "socks5" if "socks5" in name else "http",
        "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
    }
    if proxy.username:
        payload |= {"proxyUser": proxy.username, "proxyPass": proxy.password}

    nav = f"http://127.0.0.1:{destination.port}/dest?leak={name}"
    profile_id, port = _launch(payload, nav)
    try:
        result = _fetch_in_page(port, f"/dest?leak={name}&fetch=1")
        sources = destination.sources_for(f"leak={name}")
        assert sources, (
            f"{name}: no request arrived, so nothing was proven. "
            f"fetch={result} proxy[connections={proxy.connections}, "
            f"auth_failures={getattr(proxy, 'auth_failures', '?')}, "
            f"rejected={proxy.rejected}, "
            f"authenticated={bool(proxy.username)}] -- connections=0 means "
            f"the browser never reached the proxy at all."
        )
        assert "127.0.0.1" not in sources, (
            f"{name}: the host address reached the destination: {sources}"
        )
    finally:
        L.stop_profile(profile_id)


def test_an_authenticated_proxy_was_actually_challenged(lab):
    """Otherwise "it worked" might mean the proxy never asked.

    The CDP ``Fetch.authRequired`` path is what answers a 407, and a proxy
    that never challenged would exercise none of it.
    """
    import multiprofile.browser.launcher as L

    proxy = lab["proxies"]["http_auth"]
    destination = lab["destination"]
    nav = f"http://127.0.0.1:{destination.port}/dest?auth=1"
    profile_id, port = _launch({
        "name": "auth-challenge", "proxyType": "http",
        "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
        "proxyUser": proxy.username, "proxyPass": proxy.password,
    }, nav)
    try:
        result = _fetch_in_page(port, "/dest?auth=1&fetch=1")
        assert result.get("ok") is True, (
            "the browser could not fetch through the authenticated proxy. "
            f"fetch={result} proxy[connections={proxy.connections}, "
            f"auth_failures={proxy.auth_failures}, rejected={proxy.rejected}] "
            f"destination_sources={destination.sources_for('auth=1')} -- "
            "connections=0 means the browser never arrived; "
            "auth_failures>0 with connections=0 means it arrived, was "
            "challenged, and never came back."
        )
        assert proxy.auth_failures > 0, (
            "the proxy never issued a 407, so the authentication path was "
            f"not exercised. proxy[connections={proxy.connections}, "
            f"rejected={proxy.rejected}]"
        )
        assert proxy.connections > 0
        assert set(destination.sources_for("auth=1")) == {"127.0.0.4"}
    finally:
        L.stop_profile(profile_id)


def test_the_proxy_resolved_the_hostname_not_the_browser(lab):
    """The DNS claim in docs/NETWORK_ISOLATION.md §4.1, observed.

    The doc says a proxied profile emits no DNS of its own because Chrome
    hands the *name* to the proxy. A SOCKS5 proxy can see which: address type
    0x03 is a domain name, 0x01 is a literal the client resolved itself.
    """
    import multiprofile.browser.launcher as L

    proxy = lab["proxies"]["socks5"]
    destination = lab["destination"]
    # `localhost` is a name; 127.0.0.1 would be a literal and would prove
    # nothing either way. The navigation carries it.
    nav = f"http://localhost:{destination.port}/dest?dns=1"
    profile_id, port = _launch({
        "name": "dns-route", "proxyType": "socks5",
        "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
    }, nav)
    try:
        result = _fetch_in_page(port, "/dest?dns=1&fetch=1")
        assert result.get("ok") is True, result
        assert "localhost" in proxy.requested_hostnames, (
            "the browser did not hand the hostname to the proxy; it resolved "
            f"it first. literals seen: {proxy.requested_literals}"
        )
    finally:
        L.stop_profile(profile_id)


def test_authenticated_socks5_is_refused_rather_than_launched_blind(lab):
    """Chromium does not implement SOCKS5 authentication, so this cannot work.

    Measured on Chromium 151 before the gate was added, with the profile
    configured exactly as an operator would:

        launch ok                 True     <- the product reported success
        destination requests      0        <- nothing loaded, ever
        socks connections opened  0
        socks auth failures       40       <- the method was refused, 40 times
        launch note               "proxy auth=armed"

    It failed closed -- no host-IP leak, which is the one good thing about it
    -- but silently from the operator's side: a green launch, a note claiming
    proxy auth was armed, and a browser that could not load a page. That note
    refers to the CDP Fetch.authRequired handler, which answers an HTTP 407
    and has nothing to do with SOCKS5.

    Authenticated SOCKS5 is what most commercial proxy providers sell, so it
    is a configuration an operator is likely to try first.
    """
    import multiprofile.browser.launcher as L
    from multiprofile.network import network_config_for_profile, unsupported_route_reason
    from multiprofile.storage.profiles import create_profile

    proxy = lab["proxies"]["socks5_auth"]
    destination = lab["destination"]
    nav = f"http://127.0.0.1:{destination.port}/dest?sa=1"
    payload = {
        "name": "socks5-auth", "proxyType": "socks5",
        "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
        "proxyUser": proxy.username, "proxyPass": proxy.password,
        "startUrl": nav,
    }

    # The reason is produced from the config alone, before any probe.
    config = network_config_for_profile(dict(payload), commit=False)
    reason = unsupported_route_reason(config)
    assert reason, "an authenticated SOCKS5 route was not recognised as unusable"
    assert "SOCKS5 authentication" in reason, reason

    profile_id = create_profile(payload)["id"]
    try:
        result = L.launch_profile(profile_id, url=nav, direct=False)
        assert result.get("ok") is not True, (
            "the launch succeeded for a route that cannot load a page: "
            f"{result}"
        )
        assert "SOCKS5" in str(result.get("error") or ""), result
        # And still no leak: nothing reached the destination from anywhere.
        assert destination.sources_for("sa=1") == [], destination.requests
    finally:
        L.stop_profile(profile_id)


def test_an_ip_authorised_socks5_proxy_is_not_refused(lab):
    """The gate must catch credentials, not SOCKS5 itself.

    An IP-authorised SOCKS5 endpoint has no username and works fine -- it is
    the route `test_the_destination_sees_the_intended_hop[socks5]` proves. A
    gate that blocked all SOCKS5 would break the working case.
    """
    from multiprofile.network import network_config_for_profile, unsupported_route_reason

    proxy = lab["proxies"]["socks5"]
    config = network_config_for_profile({
        "name": "socks5-noauth", "proxyType": "socks5",
        "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
    }, commit=False)
    assert unsupported_route_reason(config) is None


# --------------------------------------------------------------------------
# The proxy goes away mid-session
# --------------------------------------------------------------------------
#
# The roadmap's sharpest requirement: "a prelaunch check cannot prove that a
# later disconnect ... stays on the intended route" and "deliberately
# disconnect the proxy during a session and prove what happens next. For
# profiles requiring a fixed route, do not silently fall back to the host
# connection."
#
# A pre-launch probe is structurally unable to answer this. The only way to
# know is to kill the proxy with the browser still running and then watch the
# destination.


@pytest.mark.parametrize("name,expected_source",
                         (("http", "127.0.0.2"), ("socks5", "127.0.0.3")))
def test_a_mid_session_proxy_drop_does_not_fall_back_to_the_host(
    lab, name, expected_source
):
    """Kill the proxy with the browser running; the host address must not
    appear at the destination afterwards."""
    import multiprofile.browser.launcher as L

    destination = lab["destination"]
    proxy = lab["proxies"][name]
    nav = f"http://127.0.0.1:{destination.port}/dest?drop={name}&phase=before"
    profile_id, port = _launch({
        "name": f"drop-{name}",
        "proxyType": "socks5" if name == "socks5" else "http",
        "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
    }, nav)
    try:
        # Precondition: the route works at all, or the rest proves nothing.
        before = _fetch_in_page(port, f"/dest?drop={name}&phase=before2")
        assert before.get("ok") is True, (name, before)
        assert set(destination.sources_for(f"drop={name}")) == {expected_source}

        # The event under test. hard_stop, not stop: closing only the
        # listener left Chromium relaying through an already-established
        # tunnel, so this test passed for the wrong reason -- the request
        # arrived from the proxy's own source address, which is correct
        # behaviour for a proxy that is still running. A proxy going away
        # severs what the browser had open.
        proxy.hard_stop()
        time.sleep(1.5)

        _fetch_in_page(port, f"/dest?drop={name}&phase=after")
        sources_after = destination.sources_for(f"drop={name}&phase=after")

        # THE GATE, and the only claim this lab can make honestly: whatever
        # happens after the proxy dies, the request must never arrive from
        # the host address. That is the roadmap's requirement -- "do not
        # silently fall back to the host connection" -- and it holds.
        assert "127.0.0.1" not in sources_after, (
            f"{name}: after the proxy died the browser reached the "
            f"destination from the host address: {sources_after}"
        )

        # What is deliberately NOT asserted, and why.
        #
        # "No request arrives at all" would be the stronger claim, and for
        # SOCKS5 it is what happens. For the HTTP proxy it is not assertable
        # *with this lab*, because the lab proxy is a dumb pipe: after it
        # relays the first request it is a bidirectional socket pair, so
        # HTTP/1.1 keep-alive requests pass through its logic untouched --
        # visible in the destination's own log, which shows absolute-form
        # paths (`http://127.0.0.1:PORT/dest?...`) that only a verbatim
        # forward produces. A real proxy parses every request on the
        # connection; this one does not, so "the proxy is gone" and "the pipe
        # is gone" are not the same event here.
        #
        # Severing every tracked socket (`hard_stop`) still left one request
        # arriving from 127.0.0.2 in origin form, which means it went through
        # the proxy's own handler after the listener was closed -- a lab
        # behaviour this harness does not yet explain. It is recorded in
        # docs/NETWORK_ISOLATION.md §7b rather than asserted around, because
        # the security-relevant question is answered either way: the source
        # was the proxy, never the host.
        assert all(src in ("127.0.0.2", "127.0.0.3") for src in sources_after), (
            f"{name}: a request arrived from an unexpected source after the "
            f"drop: {sources_after}"
        )
    finally:
        L.stop_profile(profile_id)


def test_a_websocket_follows_the_proxy_route(lab):
    """WebSockets are named in the roadmap because they are a separate code
    path in Chromium, not an HTTP request with a different header."""
    import multiprofile.browser.launcher as L

    destination = lab["destination"]
    proxy = lab["proxies"]["http"]
    nav = f"http://127.0.0.1:{destination.port}/dest?ws=nav"
    profile_id, port = _launch({
        "name": "ws-route", "proxyType": "http",
        "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
    }, nav)
    try:
        before = proxy.connections
        # The destination is a plain HTTP server, so the upgrade fails -- but
        # the *connection attempt* is what is being measured, and it has to
        # have gone through the proxy to be counted here.
        import json as _json

        import websocket

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        target = next(t for t in _json.loads(
            urllib.request.urlopen(request, timeout=5).read())
            if t.get("type") == "page")
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        try:
            ws.send(_json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": (
                    "new Promise(res => {"
                    f"  const s = new WebSocket('ws://127.0.0.1:{destination.port}/ws');"
                    "   s.onopen = () => res('open');"
                    "   s.onerror = () => res('error');"
                    "   s.onclose = () => res('closed');"
                    "   setTimeout(() => res('timeout'), 6000);"
                    "})"
                ), "awaitPromise": True, "returnByValue": True},
            }))
            deadline = time.time() + 20
            while time.time() < deadline:
                message = _json.loads(ws.recv())
                if message.get("id") == 1:
                    break
        finally:
            ws.close()
        assert proxy.connections > before, (
            "the WebSocket attempt did not go through the proxy: connections "
            f"stayed at {before}"
        )
    finally:
        L.stop_profile(profile_id)
