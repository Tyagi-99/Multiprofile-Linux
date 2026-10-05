"""The profile's HTTP identity must match the one it claims in JavaScript.

This is the regression guard for F1, the worst defect this product has
shipped against its own value proposition.

A profile created without a device preset had an EMPTY ``userAgent``. The
launcher therefore passed no ``--user-agent`` at all, and
``apply_ua_override()`` sent ``Network.setUserAgentOverride`` down a one-shot
WebSocket that closed microseconds later. Chromium scopes that override to
the DevTools session that set it, so it was discarded -- but the command had
been ACKed, so the function returned True and the launcher appended
"CDP UA/client-hints override applied" to the launch notes.

Measured on the wire before the fix::

    User-Agent:         Mozilla/5.0 (X11; Linux x86_64) ... Chrome/141.0.0.0
    sec-ch-ua:          "Chromium";v="141", "Not?A_Brand";v="8"
    sec-ch-ua-platform: "Linux"
    navigator.platform: "Win32"          <- the page claimed Windows

and after::

    User-Agent:         Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/131.0.0.0
    sec-ch-ua:          "Google Chrome";v="131", "Chromium";v="131", "Not-A.Brand";v="99"
    sec-ch-ua-platform: "Windows"
    navigator.platform: "Win32"

A server comparing its own request headers against JS-reported values caught
the first case 100% of the time, with no fingerprinting library. A plain
unmodified Chromium would have looked like an ordinary Linux user, so the
product was making its users MORE identifiable, not less.

Three properties keep it fixed, and each has tests below:
  1. every fingerprint pool names a User-Agent, so there is always one to set
  2. the override only ever travels on a session the caller holds open
  3. the launch is verified against the real wire and REFUSED on a mismatch
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from multiprofile.browser.cdp_identity import (
    UA_OVERRIDE_METHOD,
    PinnedCdpSession,
    apply_ua_override,
    build_ua_override_params,
)
from multiprofile.browser.identity_assert import (
    IdentityAssertion,
    compare,
    expected_wire_identity,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "multiprofile"

WIN_FP = {
    "userAgent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "platform": "Win32",
    "ch_platform": "Windows",
    "language": "en-US",
    "languages": ["en-US", "en"],
    "touch": 0,
}


class FakeSession:
    """A PinnedCdpSession stand-in that records what it was asked to send."""

    def __init__(self, *, open_: bool = True, error: dict | None = None):
        self._open = open_
        self._error = error
        self.sent: list[tuple[str, dict]] = []

    @property
    def is_open(self) -> bool:
        return self._open

    def send(self, method: str, params: dict) -> dict | None:
        self.sent.append((method, params))
        if not self._open:
            return None
        if self._error:
            return {"id": 1, "error": self._error}
        return {"id": 1, "result": {}}


# ---------------------------------------------------------------------------
# 1. there is always a User-Agent to claim
# ---------------------------------------------------------------------------


def _pools() -> dict[str, dict]:
    from multiprofile.fingerprint._core import POOL_MAP

    return {pool["pool_name"]: pool for pool in POOL_MAP.values()}


@pytest.mark.parametrize("name", sorted(_pools()))
def test_every_pool_names_a_user_agent(name):
    """An empty UA is what made F1 possible: nothing to put on the wire."""
    ua = _pools()[name].get("userAgent") or ""
    assert ua.startswith("Mozilla/5.0"), f"pool {name} has no usable User-Agent"


@pytest.mark.parametrize("name", sorted(_pools()))
def test_pool_user_agent_agrees_with_its_own_client_hints(name):
    """A UA claiming one Chrome major beside brands claiming another is the
    same contradiction as F1, one layer down."""
    from multiprofile.fingerprint import build_fingerprint

    pool = _pools()[name]
    fp = build_fingerprint({"id": "x", "seed": 99, "userAgent": pool["userAgent"]})
    ua_major = re.search(r"(?:Chrome|CriOS)/(\d+)", pool["userAgent"])
    assert ua_major, f"pool {name} UA names no Chromium version"
    brand_majors = {
        b["version"] for b in fp["ch_brands"] if not b["brand"].startswith("Not")
    }
    assert brand_majors == {ua_major.group(1)}, (
        f"pool {name}: UA says {ua_major.group(1)}, client hints say {brand_majors}"
    )


@pytest.mark.parametrize("os_name,expect", [
    ("windows", "Windows NT"),
    ("macos", "Macintosh"),
    ("linux", "X11; Linux"),
])
def test_a_profile_created_with_no_user_agent_still_has_one(os_name, expect):
    """The quick-create "New Profile" flow produces exactly these profiles."""
    from multiprofile.fingerprint import build_fingerprint

    fp = build_fingerprint({"id": "p", "seed": 4242, "os": os_name})
    assert fp["userAgent"], "fingerprint has no UA — the wire will carry the host's"
    if fp["ch_platform"] == "Windows":
        assert expect in fp["userAgent"] or "Windows NT" in fp["userAgent"]


def test_the_default_fingerprint_ua_and_platform_agree():
    from multiprofile.fingerprint import build_fingerprint

    fp = build_fingerprint({"id": "p", "seed": 7})
    assert fp["platform"] == "Win32"
    assert "Windows NT" in fp["userAgent"]
    assert fp["ch_platform"] == "Windows"


def test_the_identity_report_claims_the_same_ua_as_the_wire():
    """The inspector the operator reads must not disagree with the headers."""
    from multiprofile.fingerprint._core import resolve_context

    ctx = resolve_context({"id": "p", "seed": 7})
    assert "Windows NT" in ctx["userAgent"]


# ---------------------------------------------------------------------------
# 2. the override only travels on a held session
# ---------------------------------------------------------------------------


def test_override_params_are_none_without_a_user_agent():
    assert build_ua_override_params({"platform": "Win32"}, "") is None


def test_override_params_accept_language_is_a_list_not_a_header():
    """Chromium adds the q-weights itself. Handing it a finished header
    produced "en-US,en;q=0.9;q=0.9" on the wire — measured, and a tell no
    real browser emits."""
    params = build_ua_override_params(WIN_FP)
    assert params["acceptLanguage"] == "en-US,en"
    assert "q=" not in params["acceptLanguage"]


def test_override_params_carry_the_client_hint_block():
    params = build_ua_override_params(WIN_FP)
    assert params["userAgent"] == WIN_FP["userAgent"]
    assert params["userAgentMetadata"]["platform"] == "Windows"
    assert params["userAgentMetadata"]["mobile"] is False


def test_preset_metadata_overrides_the_fingerprint_hints():
    meta = {"platform": "Android", "mobile": True, "brands": []}
    params = build_ua_override_params(WIN_FP, metadata=meta)
    assert params["userAgentMetadata"] is meta


def test_apply_ua_override_refuses_without_a_session():
    """THE regression test. The old signature took a port, opened a socket,
    closed it, and returned True."""
    assert apply_ua_override(None, WIN_FP) is False


def test_apply_ua_override_refuses_a_closed_session():
    sess = FakeSession(open_=False)
    assert apply_ua_override(sess, WIN_FP) is False
    assert sess.sent == [], "nothing may be sent down a session already closed"


def test_apply_ua_override_refuses_with_no_user_agent():
    sess = FakeSession()
    assert apply_ua_override(sess, {"platform": "Win32"}, "") is False
    assert sess.sent == []


def test_apply_ua_override_applies_on_an_open_session():
    sess = FakeSession()
    assert apply_ua_override(sess, WIN_FP) is True
    assert len(sess.sent) == 1
    method, params = sess.sent[0]
    assert method == UA_OVERRIDE_METHOD
    assert params["userAgent"] == WIN_FP["userAgent"]
    assert params["userAgentMetadata"]["platform"] == "Windows"


def test_apply_ua_override_reports_a_cdp_error_as_failure():
    sess = FakeSession(error={"message": "Invalid parameters"})
    assert apply_ua_override(sess, WIN_FP) is False


def test_a_closed_pinned_session_knows_it_is_closed():
    class _Ws:
        def close(self):
            pass

    sess = PinnedCdpSession(_Ws(), "about:blank")
    assert sess.is_open is True
    sess.close()
    assert sess.is_open is False
    assert sess.send("Network.setUserAgentOverride", {}) is None


def test_the_one_shot_cdp_sender_is_gone():
    """It existed, its own docstring said session-scoped overrides would not
    survive it, and apply_ua_override used it anyway. A footgun guarded only
    by a comment gets picked up again, so the helper itself is removed."""
    source = (SRC / "browser" / "cdp_identity.py").read_text(encoding="utf-8")
    assert "def _cdp_ws_send" not in source
    assert "def _pick_page_ws_url" not in source


def test_the_launcher_holds_the_identity_session_for_the_browsers_life():
    source = (SRC / "browser" / "launcher.py").read_text(encoding="utf-8")
    assert "open_pinned_cdp_session(debug_port)" in source
    assert "_hold_device_session(profile_id, proc.pid, _sess)" in source
    # The override must not be handed a bare port again.
    assert "apply_ua_override(debug_port" not in source


# ---------------------------------------------------------------------------
# 3. the injector carries the override to every target
# ---------------------------------------------------------------------------


def _injector(**kw):
    from multiprofile.browser.cdp_inject import IdentityInjector

    class FakeWs:
        def __init__(self):
            self.sent = []

        def send(self, raw):
            self.sent.append(json.loads(raw))

        def recv(self):
            raise TimeoutError("timed out")

        def close(self):
            pass

        def methods(self):
            return [m["method"] for m in self.sent]

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


def test_the_injector_sets_the_override_on_every_target():
    """A single pinned page session cannot cover a tab opened later; the
    injection session sees every target, so the override belongs there too."""
    params = build_ua_override_params(WIN_FP)
    inj, ws = _injector(script="X", ua_override=params)
    _attach(inj, target="t1", session="s1")
    _attach(inj, target="t2", session="s2")

    assert ws.methods().count(UA_OVERRIDE_METHOD) == 2
    assert inj.status.ua_override_applied is True


def test_the_injector_sets_the_override_before_unfreezing_the_page():
    params = build_ua_override_params(WIN_FP)
    inj, ws = _injector(script="X", ua_override=params)
    _attach(inj, waiting=True)

    methods = ws.methods()
    assert methods.index(UA_OVERRIDE_METHOD) < methods.index(
        "Runtime.runIfWaitingForDebugger"
    )


def test_no_override_is_sent_when_there_is_nothing_to_claim():
    inj, ws = _injector(script="X")
    _attach(inj)
    assert UA_OVERRIDE_METHOD not in ws.methods()
    assert inj.status.ua_override_applied is False


def test_a_failed_override_flips_status_false_and_is_not_swallowed():
    params = build_ua_override_params(WIN_FP)
    inj, ws = _injector(script="X", ua_override=params)
    _attach(inj)
    assert inj.status.ua_override_applied is True

    msg_id = next(m["id"] for m in ws.sent if m["method"] == UA_OVERRIDE_METHOD)
    inj._handle({"id": msg_id, "error": {"message": "Invalid parameters"}})

    assert inj.status.ua_override_applied is False
    assert inj.status.ok is False
    assert inj.status.to_dict()["uaOverrideApplied"] is False


# ---------------------------------------------------------------------------
# 4. the wire comparison
# ---------------------------------------------------------------------------


def test_expected_headers_are_derived_from_the_override_params():
    exp = expected_wire_identity(build_ua_override_params(WIN_FP))
    assert exp["user-agent"] == WIN_FP["userAgent"]
    assert exp["sec-ch-ua-platform"] == '"Windows"'
    assert exp["sec-ch-ua-mobile"] == "?0"
    assert exp["sec-ch-ua"] == "131"


def test_grease_brands_are_not_evidence():
    """"Not-A.Brand";v="99" is deliberate noise, not an identity claim."""
    exp = expected_wire_identity({
        "userAgent": "UA",
        "userAgentMetadata": {
            "platform": "Windows",
            "mobile": False,
            "brands": [
                {"brand": "Not-A.Brand", "version": "99"},
                {"brand": "Not?A_Brand", "version": "8"},
                {"brand": "Google Chrome", "version": "131"},
            ],
        },
    })
    assert exp["sec-ch-ua"] == "131"


def test_the_exact_f1_leak_is_a_mismatch():
    """The headers measured on the broken build, against the intent."""
    expected = expected_wire_identity(build_ua_override_params(WIN_FP))
    observed = {
        "user-agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Chromium";v="141", "Not?A_Brand";v="8"',
        "sec-ch-ua-platform": '"Linux"',
        "sec-ch-ua-mobile": "?0",
    }
    mismatches = compare(expected, observed)
    assert len(mismatches) == 3
    assert any("User-Agent" in m for m in mismatches)
    assert any("sec-ch-ua-platform" in m for m in mismatches)
    assert any("sec-ch-ua on the wire" in m for m in mismatches)


def test_the_measured_good_case_is_not_a_mismatch():
    expected = expected_wire_identity(build_ua_override_params(WIN_FP))
    observed = {
        "user-agent": WIN_FP["userAgent"],
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not-A.Brand";v="99"',
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-mobile": "?0",
    }
    assert compare(expected, observed) == []


def test_an_absent_client_hint_header_is_not_a_contradiction():
    """Hints are not attached to every request shape; absence is not a leak."""
    expected = expected_wire_identity(build_ua_override_params(WIN_FP))
    assert compare(expected, {"user-agent": WIN_FP["userAgent"]}) == []


def test_only_a_conclusive_mismatch_can_fail_a_launch():
    assert IdentityAssertion(ok=False, conclusive=False, reason="no CDP").failed is False
    assert IdentityAssertion(ok=True, conclusive=True).failed is False
    assert IdentityAssertion(ok=False, conclusive=True, mismatches=["x"]).failed is True


def test_the_assertion_serialises_for_the_api():
    a = IdentityAssertion(ok=True, conclusive=True, observed={"user-agent": "x"})
    assert json.dumps(a.to_dict())
    assert set(a.to_dict()) >= {"ok", "conclusive", "reason", "mismatches", "observed"}


def test_an_unverified_assertion_never_reads_as_a_pass():
    a = IdentityAssertion(reason="no CDP browser session")
    assert a.ok is False
    assert "NOT VERIFIED" in a.summary()


# ---------------------------------------------------------------------------
# 5. the launch itself — no preset, no explicit UA, still masked
# ---------------------------------------------------------------------------


@pytest.fixture()
def launcher_sandbox(tmp_path, monkeypatch):
    """Isolated storage + a faked browser spawn, so the flags can be read."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    # The probe needs a real browser; these tests read the launch command.
    monkeypatch.setenv("MULTIPROFILE_IDENTITY_ASSERT", "0")

    import multiprofile.browser.launcher as L

    class _Stderr:
        def read(self):
            return b""

        def close(self):
            pass

    class _Proc:
        def __init__(self, *a, **k):
            self.pid = 424242
            self.stderr = _Stderr()

        def poll(self):
            return None

    from multiprofile.browser.cdp_window import CdpError

    def _no_cdp(port, timeout_s=8.0):
        # CDP never comes up: the launch must still carry the identity on the
        # flag, which is the floor under the override.
        raise CdpError("cdp_unreachable", "faked: no browser in this test")

    monkeypatch.setattr(L, "find_chrome", lambda: "/nonexistent/chrome")
    monkeypatch.setattr(L.subprocess, "Popen", _Proc)
    monkeypatch.setattr(L, "wait_for_cdp", _no_cdp)
    return L


def _ua_flag(cmd: list[str]) -> str | None:
    for arg in cmd:
        if arg.startswith("--user-agent="):
            return arg.split("=", 1)[1]
    return None


def test_a_profile_with_no_preset_and_no_ua_is_still_launched_with_one(launcher_sandbox):
    """THE launcher-level regression test for F1.

    Before the fix this profile -- the one the quick-create button makes --
    got no ``--user-agent`` flag at all.
    """
    from multiprofile.storage.profiles import create_profile

    prof = create_profile({"name": "f1-default", "proxyType": "none", "os": "windows"})
    assert not prof.get("deviceId")
    assert not prof.get("userAgent")

    res = launcher_sandbox.launch_profile(prof["id"], url="about:blank", direct=True)
    try:
        ua = _ua_flag(res["cmd"])
        assert ua, "launched with no --user-agent: the wire carries the host identity"
        assert "Windows NT" in ua
        assert "Linux" not in ua
        assert any("User-Agent flag" in n for n in res["notes"])
    finally:
        launcher_sandbox.stop_profile(prof["id"])


def test_an_explicit_profile_user_agent_still_wins(launcher_sandbox):
    from multiprofile.storage.profiles import create_profile

    mine = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    prof = create_profile({"name": "f1-explicit", "proxyType": "none", "userAgent": mine})
    res = launcher_sandbox.launch_profile(prof["id"], url="about:blank", direct=True)
    try:
        assert _ua_flag(res["cmd"]) == mine
    finally:
        launcher_sandbox.stop_profile(prof["id"])


def test_the_resolved_identity_matches_the_flag_it_sets():
    """The flag, the CDP override and the assertion read one answer."""
    from multiprofile.browser.launcher import _resolve_wire_identity
    from multiprofile.fingerprint import build_fingerprint

    profile = {"id": "p", "seed": 11, "os": "windows"}
    fp = build_fingerprint(profile)
    ua, params = _resolve_wire_identity(profile, fp, None)
    assert ua
    assert params is not None
    assert params["userAgent"] == ua


def test_the_resolved_identity_takes_client_hints_from_a_device_preset():
    from multiprofile.browser.launcher import _resolve_wire_identity
    from multiprofile.devices.registry import DeviceRegistry
    from multiprofile.fingerprint import build_fingerprint

    preset = DeviceRegistry().get("pixel-9")
    assert preset is not None
    profile = {"id": "p", "seed": 11, "deviceId": "pixel-9"}
    ua, params = _resolve_wire_identity(profile, build_fingerprint(profile), preset)
    assert "Android" in ua
    assert params["userAgentMetadata"]["platform"] == "Android"
    assert params["userAgentMetadata"]["mobile"] is True
