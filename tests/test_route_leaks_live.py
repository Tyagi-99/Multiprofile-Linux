"""WebRTC, IPv6 and downloads — Phase 3's remaining named paths.

The roadmap lists these beside the exit IP because each is a *separate path*
in Chromium, not an HTTP request wearing a different hat:

* **WebRTC** gathers ICE candidates over UDP, outside the HTTP stack
  entirely, which is why it has been the classic proxy leak for a decade.
* **IPv6** is a different address family. A proxy that tunnels only IPv4
  leaves a dual-stack host free to reach a destination directly over v6.
* **A download** is handled by the download manager, not the renderer, so
  "the page went through the proxy" does not imply "the download did".

Each test here measures both directions where it can: a control showing the
mechanism is observable at all, and the restricted case showing it was
actually restricted. A one-sided assertion passes on a host where the
capability is simply absent, which is the failure mode this lab exists to
avoid.
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
    FileDestination,
    HttpConnectProxy,
    Ipv6Destination,
    ipv6_loopback_available,
    loopback_alias_available,
)


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    return tmp_path


def _evaluate(port: int, expression: str, *, timeout: float = 30.0,
              url_fragment: str = "") -> object:
    import websocket

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
    )
    targets = json.loads(urllib.request.urlopen(request, timeout=5).read())
    target = next(t for t in targets if t.get("type") == "page"
                  and url_fragment in (t.get("url") or ""))
    ws = websocket.create_connection(
        target["webSocketDebuggerUrl"], timeout=timeout + 10, suppress_origin=True)
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True,
                       "returnByValue": True},
        }))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = json.loads(ws.recv())
            if message.get("id") != 1:
                continue
            result = message.get("result") or {}
            if result.get("exceptionDetails"):
                return {"error": str(result["exceptionDetails"])[:300]}
            return (result.get("result") or {}).get("value")
        return {"error": "timeout"}
    finally:
        ws.close()


# ICE candidate types and whether a routable address appeared. The address
# itself is never returned: nothing here should be pasteable into an issue
# and leak the operator's network.
_ICE_PROBE = r"""
(() => new Promise(res => {
  let pc;
  try {
    pc = new RTCPeerConnection({iceServers: [{urls: 'stun:127.0.0.1:3478'}]});
  } catch (e) { res(JSON.stringify({error: String(e)})); return; }
  const types = new Set(), protocols = new Set();
  let routable = 0, mdns = 0, priv = 0, total = 0;
  const finish = () => {
    try { pc.close(); } catch (e) {}
    res(JSON.stringify({total, types: [...types].sort(),
      protocols: [...protocols].sort(), routable, mdns, private: priv}));
  };
  pc.onicecandidate = e => {
    if (!e.candidate) { finish(); return; }
    total++;
    const c = e.candidate;
    if (c.type) types.add(c.type);
    if (c.protocol) protocols.add(c.protocol);
    const a = c.address || '';
    if (/\.local$/.test(a)) mdns++;
    else if (!a) { /* redacted by the browser */ }
    else if (/^(127\.|::1$|169\.254\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|fe80:)/.test(a)) priv++;
    else routable++;
  };
  pc.createDataChannel('probe');
  pc.createOffer().then(o => pc.setLocalDescription(o)).catch(e => res(JSON.stringify({error: String(e)})));
  setTimeout(finish, 9000);
}))()
"""


def _launch(payload: dict, url: str) -> tuple[str, int]:
    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    profile_id = create_profile({**payload, "startUrl": url})["id"]
    result = L.launch_profile(profile_id, url=url, direct=False)
    assert result.get("ok") is True, result
    time.sleep(2.5)
    return profile_id, get_runtime(profile_id).cdp_port


# --------------------------------------------------------------------------
# 1. WebRTC
# --------------------------------------------------------------------------

def test_webrtc_gathers_candidates_at_all_by_default(xdg):
    """The control. Without this, "no routable candidate" below could mean
    WebRTC is simply unavailable on this host."""
    import multiprofile.browser.launcher as L

    destination = Destination()
    try:
        profile_id, port = _launch(
            {"name": "ice-default", "proxyType": "none"}, destination.url)
        try:
            observed = json.loads(_evaluate(port, _ICE_PROBE))
            assert not observed.get("error"), observed
            assert observed["total"] > 0, (
                "no ICE candidate at all, so this file cannot measure WebRTC "
                f"on this host: {observed}"
            )
            assert "host" in observed["types"], observed
        finally:
            L.stop_profile(profile_id)
    finally:
        destination.close()


def test_proxy_only_webrtc_exposes_no_routable_address(xdg):
    """`disable_non_proxied_udp` must leave no routable address in a
    candidate. mDNS `.local` host candidates are fine -- that is Chrome's
    own default obfuscation and carries no address."""
    import multiprofile.browser.launcher as L

    destination = Destination()
    try:
        profile_id, port = _launch({
            "name": "ice-proxyonly", "proxyType": "none",
            "webrtcMode": "disable_non_proxied_udp",
        }, destination.url)
        try:
            observed = json.loads(_evaluate(port, _ICE_PROBE))
            assert not observed.get("error"), observed
            assert observed["routable"] == 0, (
                f"a routable address appeared in an ICE candidate: {observed}"
            )
        finally:
            L.stop_profile(profile_id)
    finally:
        destination.close()


def test_the_webrtc_flags_are_actually_emitted(xdg):
    """The flags behind the behaviour, so a silent rename upstream is caught
    here rather than by a leak."""
    from multiprofile.network import chrome_network_flags, network_config_for_profile

    config = network_config_for_profile(
        {"name": "f", "proxyType": "none",
         "webrtcMode": "disable_non_proxied_udp"}, commit=False)
    flags = chrome_network_flags(config)
    assert any("force-webrtc-ip-handling-policy=disable_non_proxied_udp" in f
               for f in flags), flags
    assert any("enforce-webrtc-ip-permission-check" in f for f in flags), flags


# --------------------------------------------------------------------------
# 2. IPv6
# --------------------------------------------------------------------------

# IPv6 is not an operator setting, and that is the right design: a proxied
# profile gets `IPv6Policy.DISABLED` unconditionally, and a direct profile
# keeps IPv6 because there is no proxy for v6 traffic to leak around. The
# first version of these tests passed `ipv6Policy` on the profile, which is
# not a field -- the policy is decided by whether a proxy is configured.
#
# The fetch result is deliberately not the assertion. A cross-origin fetch to
# the v6 destination fails CORS even when it *succeeds* at the network layer,
# so what is measured is whether the **destination saw a request**.

_NO_CORS_FETCH = (
    "(async () => { try {"
    " await fetch(__URL__, {mode: 'no-cors', cache: 'no-store'});"
    " return 'sent';"
    "} catch (e) { return 'err:' + String(e); } })()"
)


@pytest.mark.skipif(not ipv6_loopback_available(),
                    reason="this host cannot bind ::1, so 'IPv6 was blocked' "
                           "would pass for the wrong reason")
def test_a_direct_profile_can_reach_an_ipv6_destination(xdg):
    """The control. Without it, the proxied case below could pass on a host
    where IPv6 simply does not work."""
    import multiprofile.browser.launcher as L

    v6 = Ipv6Destination()
    v4 = Destination()
    try:
        profile_id, port = _launch(
            {"name": "v6-direct", "proxyType": "none"}, v4.url)
        try:
            _evaluate(port, _NO_CORS_FETCH.replace("__URL__", json.dumps(v6.url)))
            time.sleep(1.0)
            assert v6.requests, (
                "a direct profile could not reach ::1, so IPv6 is unusable on "
                "this host and the proxied assertion below proves nothing"
            )
        finally:
            L.stop_profile(profile_id)
    finally:
        v6.close()
        v4.close()


def _global_ipv6_address() -> str | None:
    """The host's own global IPv6, read at runtime.

    A destination on ``::1`` cannot answer the question that matters: Chrome
    bypasses the proxy for loopback, so a hit there proves nothing about
    routing. A destination on a **routable** address does, and the host's own
    global address is one that needs no external network.

    The address is never written to a report, a log line or an assertion
    message -- it identifies the operator's machine.
    """
    import subprocess

    try:
        out = subprocess.run(["ip", "-6", "-o", "addr", "show", "scope", "global"],
                             capture_output=True, text=True, timeout=5).stdout
        return out.split("inet6 ")[1].split("/")[0].strip() or None
    except Exception:  # noqa: BLE001 - absence is a skip, not a failure
        return None


@pytest.mark.skipif(_global_ipv6_address() is None,
                    reason="no global IPv6 on this host, so a routable IPv6 "
                           "destination cannot be built and ::1 would only "
                           "measure Chrome's loopback bypass")
def test_a_proxied_profile_hands_ipv6_destinations_to_the_proxy(xdg):
    """F57: the protection is real; the flag that claimed to provide it is not.

    `--disable-ipv6` was emitted for every proxied profile and
    `docs/NETWORK_ISOLATION.md` §4.2 described it as the mechanism. Chromium
    151 ignores it. Measured against a destination on the host's own global
    IPv6 address -- not ``::1``, so loopback cannot be the explanation -- with
    a nonexistent flag as a control:

        stock, no flag                 ipv6 destination reached:  yes
        stock, --disable-ipv6          ipv6 destination reached:  yes
        stock, a flag that does not exist   reached:              yes

    The flag has exactly the effect of a flag that is not there.

    The protection holds anyway, by the same route as the DNS one: Chromium
    hands the IPv6 destination to the proxy rather than connecting to it. That
    is what this test pins, because it is the thing that is actually true.
    """
    import socket as socket_module
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import multiprofile.browser.launcher as L

    address = _global_ipv6_address()
    assert address, "precondition"
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - handler API
            hits.append(self.path)
            body = b'{"ok":1}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        address_family = socket_module.AF_INET6

    server = Server((address, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    v6_url = f"http://[{address}]:{server.server_address[1]}/g6"

    v4 = Destination()
    # An IPv4-only proxy on purpose: it cannot complete a connection to an
    # IPv6 destination, so if Chromium routes the request through it the
    # destination gets nothing, and if Chromium goes around it the
    # destination gets a hit. The two outcomes are distinguishable.
    proxy = HttpConnectProxy("127.0.0.3")
    try:
        profile_id, port = _launch({
            "name": "v6-proxied", "proxyType": "http",
            "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
        }, v4.url)
        try:
            _evaluate(port, _NO_CORS_FETCH.replace("__URL__", json.dumps(v6_url)))
            time.sleep(1.5)
            # The mechanism: the proxy was asked for the IPv6 destination.
            asked = [h for h in proxy.requested_hostnames if ":" in h]
            assert asked, (
                "the proxy was never asked to reach an IPv6 destination, so "
                "the request went somewhere else: "
                f"hostnames={len(proxy.requested_hostnames)}"
            )
            # The property: it never arrived directly.
            assert hits == [], (
                "a proxied profile reached a routable IPv6 destination "
                f"directly, outside the proxy: {hits}"
            )
        finally:
            L.stop_profile(profile_id)
    finally:
        proxy.stop()
        v4.close()
        server.shutdown()
        server.server_close()


def test_the_dead_ipv6_flag_is_no_longer_emitted(xdg):
    """A flag Chromium ignores, advertised as a protection, is worse than no
    flag: `docs/NETWORK_ISOLATION.md` §4.2 called it the mechanism."""
    from multiprofile.network import chrome_network_flags, network_config_for_profile

    for payload in (
        {"name": "f", "proxyType": "http", "proxyHost": "127.0.0.1",
         "proxyPort": 8080},
        {"name": "f", "proxyType": "none"},
    ):
        config = network_config_for_profile(payload, commit=False)
        assert "--disable-ipv6" not in chrome_network_flags(config), payload


# --------------------------------------------------------------------------
# 3. Downloads
# --------------------------------------------------------------------------

@pytest.mark.skipif(not loopback_alias_available("127.0.0.2"),
                    reason="this host does not allow binding loopback aliases")
def test_a_download_follows_the_proxy_route(xdg, monkeypatch):
    """The download manager is a separate path from the renderer, so a page
    that went through the proxy does not prove the file did."""
    import multiprofile.browser.launcher as L
    import multiprofile.network as net_mod

    # Chrome bypasses the proxy for loopback; see
    # tests/test_route_egress_live.py for why this is appended and what it
    # costs.
    original = net_mod.chrome_network_flags

    def flags(config):
        built = list(original(config))
        if any(f.startswith("--proxy-server=") for f in built):
            built.append("--proxy-bypass-list=<-loopback>")
        return built

    monkeypatch.setattr(net_mod, "chrome_network_flags", flags)
    monkeypatch.setattr(L, "chrome_network_flags", flags)

    files = FileDestination()
    proxy = HttpConnectProxy("127.0.0.2")
    try:
        profile_id, port = _launch({
            "name": "dl-proxy", "proxyType": "http",
            "proxyHost": "127.0.0.1", "proxyPort": proxy.port,
        }, files.url)
        try:
            # A set: the page is fetched more than once (the navigation and
            # a favicon, and the lab proxy forwards keep-alive requests
            # verbatim so the path spelling varies). What matters is that
            # every one of them came from the proxy.
            assert set(files.sources_for("/page")) == {"127.0.0.2"}, (
                "the page itself did not go through the proxy, so the "
                f"download result would mean nothing: {files.requests}"
            )
            # Fetch the attachment from the page, which routes through the
            # same stack a click would.
            _evaluate(port, (
                "(async () => { try {"
                " const r = await fetch('/file.bin', {cache: 'no-store'});"
                " const b = await r.blob();"
                " return 'ok:' + b.size;"
                "} catch (e) { return 'err:' + String(e); } })()"
            ))
            time.sleep(1.0)
            sources = files.sources_for("/file.bin")
            assert sources, f"the attachment was never fetched: {files.requests}"
            assert set(sources) == {"127.0.0.2"}, (
                f"the download did not follow the proxy route: {sources}"
            )
        finally:
            L.stop_profile(profile_id)
    finally:
        proxy.stop()
        files.close()
