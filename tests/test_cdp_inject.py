"""Identity injection over CDP — the replacement for the unloadable extension.

Google Chrome refuses --load-extension ("--disable-extensions-except is not
allowed in Google Chrome, ignoring"), so the MV3 extension never ran and the
fingerprint script, proxy authentication and cookie seeding were all dead
while the console reported profiles healthy.

These tests drive IdentityInjector against a fake CDP socket, so the protocol
contract is pinned without needing a browser. The live proof is in the commit
message: on Chrome 151 the page went from the host's own values
(hardwareConcurrency 12, platform "Linux x86_64") to the profile's
(hardwareConcurrency 4, platform "Win32"), and a real 407-challenging proxy
recorded two challenges and two correctly authenticated requests.
"""
from __future__ import annotations

import json

import pytest

from multiprofile.browser.cdp_inject import (
    IdentityInjector,
    ProxyAuth,
    build_identity_script,
)


class FakeWs:
    """Records commands; replays a scripted list of inbound CDP messages."""

    def __init__(self, inbound: list[dict] | None = None):
        self.sent: list[dict] = []
        self._inbound = list(inbound or [])
        self.closed = False

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def recv(self) -> str:
        if self._inbound:
            return json.dumps(self._inbound.pop(0))
        raise TimeoutError("timed out")

    def close(self) -> None:
        self.closed = True

    # helpers
    def methods(self, session_id: str | None = None) -> list[str]:
        return [
            m["method"]
            for m in self.sent
            if session_id is None or m.get("sessionId") == session_id
        ]

    def params_for(self, method: str) -> dict:
        for m in self.sent:
            if m.get("method") == method:
                return m.get("params") or {}
        return {}


def _injector(**kw) -> tuple[IdentityInjector, FakeWs]:
    inj = IdentityInjector(9222, profile_id="p1", **kw)
    ws = FakeWs()
    inj._ws = ws
    return inj, ws


def _attach(inj, *, session="s1", target="t1", waiting=True, ttype="page"):
    inj._handle({
        "method": "Target.attachedToTarget",
        "params": {
            "sessionId": session,
            "waitingForDebugger": waiting,
            "targetInfo": {"targetId": target, "type": ttype, "url": "http://example.test/"},
        },
    })


# ---------------------------------------------------------------------------
# the script
# ---------------------------------------------------------------------------


def test_script_carries_the_profile_config_first():
    out = build_identity_script({"seed": 7, "platform": "Win32"}, "/* body */")
    assert out.startswith("var __MP_INLINE_CFG__ =")
    assert "window.__MP_PROFILE__" in out
    assert out.index("__MP_PROFILE__") < out.index("/* body */")
    assert '"platform":"Win32"' in out


def test_script_neutralises_the_extension_placeholder():
    out = build_identity_script({}, 'var c = "__PROFILE_CONFIG_JSON__";')
    assert "__PROFILE_CONFIG_JSON__" not in out
    assert "var c = null;" in out


# ---------------------------------------------------------------------------
# attach behaviour
# ---------------------------------------------------------------------------


def test_a_new_page_gets_the_script_before_it_runs():
    inj, ws = _injector(script="X")
    _attach(inj, waiting=True)

    methods = ws.methods("s1")
    assert "Page.enable" in methods
    assert "Page.addScriptToEvaluateOnNewDocument" in methods
    # Unfrozen only after the identity is installed.
    assert methods.index("Page.addScriptToEvaluateOnNewDocument") < methods.index(
        "Runtime.runIfWaitingForDebugger"
    )
    assert inj.status.script_installed is True


def test_a_page_that_was_already_open_is_reloaded():
    """addScriptToEvaluateOnNewDocument only affects the NEXT document, so the
    start-URL tab would otherwise be the one page left unmasked."""
    inj, ws = _injector(script="X")
    _attach(inj, waiting=False)

    methods = ws.methods("s1")
    assert "Page.addScriptToEvaluateOnNewDocument" in methods
    assert "Page.reload" in methods
    assert "Runtime.runIfWaitingForDebugger" not in methods


def test_the_same_target_is_not_installed_twice():
    """setAutoAttach and our explicit attachToTarget both fire for the start
    page; hooking canvas twice is the bug loader.js used to cause."""
    inj, ws = _injector(script="X")
    _attach(inj, target="t1")
    _attach(inj, target="t1", session="s2")

    assert ws.methods().count("Page.addScriptToEvaluateOnNewDocument") == 1
    assert inj.status.targets_seen == 1


def test_a_second_distinct_target_does_get_the_script():
    inj, ws = _injector(script="X")
    _attach(inj, target="t1", session="s1")
    _attach(inj, target="t2", session="s2")

    assert ws.methods().count("Page.addScriptToEvaluateOnNewDocument") == 2
    assert inj.status.targets_seen == 2


def test_a_non_page_target_is_released_not_frozen():
    """A frozen worker would hang the browser."""
    inj, ws = _injector(script="X")
    _attach(inj, ttype="service_worker", waiting=True)

    assert "Runtime.runIfWaitingForDebugger" in ws.methods("s1")
    assert "Page.addScriptToEvaluateOnNewDocument" not in ws.methods("s1")


def test_fetch_is_only_enabled_when_a_proxy_needs_auth():
    inj, ws = _injector(script="X")
    _attach(inj)
    assert "Fetch.enable" not in ws.methods()
    assert inj.status.auth_armed is False

    inj2, ws2 = _injector(script="X", proxy_auth=ProxyAuth("u", "p"))
    _attach(inj2)
    assert ws2.params_for("Fetch.enable") == {"handleAuthRequests": True}
    assert inj2.status.auth_armed is True


def test_cookies_are_seeded_once():
    inj, ws = _injector(script="X", cookies=[{"name": "a", "value": "1", "domain": ".x.test"}])
    _attach(inj, target="t1", session="s1")
    _attach(inj, target="t2", session="s2")

    assert ws.methods().count("Network.setCookies") == 1
    assert inj.status.cookies_applied == 1


# ---------------------------------------------------------------------------
# proxy auth
# ---------------------------------------------------------------------------


def test_a_proxy_challenge_is_answered_with_the_credentials():
    inj, ws = _injector(proxy_auth=ProxyAuth("operator", "s3cret"))
    inj._handle({
        "method": "Fetch.authRequired",
        "sessionId": "s1",
        "params": {"requestId": "r1", "authChallenge": {"source": "Proxy"}},
    })

    params = ws.params_for("Fetch.continueWithAuth")
    assert params["requestId"] == "r1"
    assert params["authChallengeResponse"] == {
        "response": "ProvideCredentials",
        "username": "operator",
        "password": "s3cret",
    }


def test_a_site_challenge_never_receives_the_proxy_credentials():
    """A 401 from the site must not be answered with the proxy password."""
    inj, ws = _injector(proxy_auth=ProxyAuth("operator", "s3cret"))
    inj._handle({
        "method": "Fetch.authRequired",
        "sessionId": "s1",
        "params": {"requestId": "r1", "authChallenge": {"source": "Server"}},
    })

    params = ws.params_for("Fetch.continueWithAuth")
    assert params["authChallengeResponse"] == {"response": "Default"}
    assert "s3cret" not in json.dumps(ws.sent)


def test_a_challenge_with_no_credentials_configured_is_declined():
    inj, ws = _injector()
    inj._handle({
        "method": "Fetch.authRequired",
        "sessionId": "s1",
        "params": {"requestId": "r1", "authChallenge": {"source": "Proxy"}},
    })
    assert ws.params_for("Fetch.continueWithAuth")["authChallengeResponse"]["response"] == "Default"


def test_paused_requests_are_always_released():
    """Fetch.enable without continuing every request hangs the page."""
    inj, ws = _injector(proxy_auth=ProxyAuth("u", "p"))
    inj._handle({"method": "Fetch.requestPaused", "sessionId": "s1", "params": {"requestId": "r9"}})
    assert ws.params_for("Fetch.continueRequest") == {"requestId": "r9"}


def test_the_password_is_never_in_a_repr():
    auth = ProxyAuth("operator", "super-secret")
    assert "super-secret" not in repr(auth)
    assert "<redacted>" in repr(auth)
    inj, _ = _injector(proxy_auth=auth)
    assert "super-secret" not in repr(inj.proxy_auth)


# ---------------------------------------------------------------------------
# failures are reported, not swallowed
# ---------------------------------------------------------------------------


def test_a_failed_script_install_is_recorded_and_flips_ok_false():
    inj, ws = _injector(script="X")
    _attach(inj)
    assert inj.status.script_installed is True

    msg_id = next(
        m["id"] for m in ws.sent if m["method"] == "Page.addScriptToEvaluateOnNewDocument"
    )
    inj._handle({"id": msg_id, "error": {"message": "Page domain not available"}})

    assert inj.status.script_installed is False
    assert inj.status.ok is False
    assert any("Page.addScriptToEvaluateOnNewDocument" in d for d in inj.status.detail)


def test_a_successful_response_is_not_recorded_as_an_error():
    inj, ws = _injector(script="X")
    _attach(inj)
    msg_id = ws.sent[-1]["id"]
    inj._handle({"id": msg_id, "result": {}})
    assert inj.status.detail == [] or all("error" not in d for d in inj.status.detail)


def test_status_serialises_for_the_runtime_registry():
    inj, _ = _injector(script="X")
    _attach(inj)
    d = inj.status.to_dict()
    assert set(d) >= {"ok", "scriptInstalled", "authArmed", "cookiesApplied", "targetsSeen", "error"}
    assert json.dumps(d)  # must be JSON-safe for the API


def test_missing_websocket_dependency_is_reported_not_raised(monkeypatch):
    import multiprofile.browser.cdp_inject as mod

    monkeypatch.setattr(mod, "websocket", None)
    status = mod.IdentityInjector(9222, profile_id="p1").start()
    assert status.ok is False
    assert "websocket-client" in (status.error or "")


# ---------------------------------------------------------------------------
# cookie conversion
# ---------------------------------------------------------------------------


def test_cookie_conversion_to_cdp_shape():
    from multiprofile.browser.launcher import _cdp_cookies

    out = _cdp_cookies([
        {"name": "a", "value": "1", "domain": ".x.test", "path": "/p",
         "secure": True, "httpOnly": True, "sameSite": "lax", "expirationDate": 1700000000},
        {"name": "", "domain": ".x.test"},        # no name -> dropped
        {"name": "c", "value": "3"},              # no domain -> dropped
    ])

    assert len(out) == 1
    assert out[0] == {
        "name": "a", "value": "1", "domain": ".x.test", "path": "/p",
        "secure": True, "httpOnly": True, "sameSite": "Lax", "expires": 1700000000.0,
    }


@pytest.mark.parametrize("stored", [None, []])
def test_cookie_conversion_handles_nothing(stored):
    from multiprofile.browser.launcher import _cdp_cookies

    assert _cdp_cookies(stored) == []
