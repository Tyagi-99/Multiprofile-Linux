"""The WebRTC control must actually harden WebRTC.

Two vocabularies grew up in the same codebase and nothing translated between
them. ``WebRTCPolicy``, ``_map_webrtc``, ``fingerprint/_core`` and
``evasion.js`` all speak::

    default | disable | disable_non_proxied_udp

while the API validator alone accepted::

    default | disabled | proxy

So both non-default values a client could actually send fell through to
``WebRTCPolicy.DEFAULT``: no Chrome flags, no JS hardening, and a privacy
control that reported itself set while doing nothing. F8 then made it worse --
tightening field validation turned the two words that *do* work into 400s, so
the setting was not merely inert, it was unreachable.

The fix is one vocabulary with the console's spellings kept as aliases, so
profiles already stored as ``"disabled"`` start being honoured rather than
staying quietly open. Unknown values normalise to ``"default"``, which is the
only safe direction: a typo must never silently *weaken* a profile.

Both halves are asserted here, because fixing one and not the other is how
this class of bug survives -- the Chrome flags and the mode handed to the
injected script have to agree.
"""
from __future__ import annotations

import pytest

from multiprofile.fingerprint import build_fingerprint
from multiprofile.network import (
    WEBRTC_MODES,
    NetworkConfig,
    NetworkMode,
    WebRTCPolicy,
    _map_webrtc,
    chrome_network_flags,
    normalize_webrtc_mode,
)

HEADERS = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from multiprofile.api.server import create_app

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _webrtc_flags(mode: str) -> list[str]:
    net = NetworkConfig(mode=NetworkMode.DIRECT, webrtc_policy=_map_webrtc(mode))
    return [f for f in chrome_network_flags(net) if "webrtc" in f.lower()]


# --------------------------------------------------------------------------
# 1. The vocabulary
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "spelling,canonical",
    [
        ("default", "default"),
        ("disable", "disable"),
        ("disable_non_proxied_udp", "disable_non_proxied_udp"),
        # The console's own words, which used to mean nothing.
        ("disabled", "disable"),
        ("proxy", "disable_non_proxied_udp"),
        # Case and padding.
        ("DISABLED", "disable"),
        ("  Proxy  ", "disable_non_proxied_udp"),
    ],
)
def test_every_spelling_normalises_to_one_vocabulary(spelling, canonical):
    assert normalize_webrtc_mode(spelling) == canonical


@pytest.mark.parametrize("junk", ["nonsense", "", None, "off_maybe", 17])
def test_an_unknown_value_falls_back_to_default_not_to_silence(junk):
    """Safe direction only. A typo must not silently weaken a profile."""
    assert normalize_webrtc_mode(junk) == "default"


def test_the_canonical_set_is_what_the_policy_enum_holds():
    assert set(WEBRTC_MODES) == {p.value for p in WebRTCPolicy}


# --------------------------------------------------------------------------
# 2. The HTTP half: Chrome actually gets flags
# --------------------------------------------------------------------------

def test_the_consoles_words_now_produce_chrome_flags():
    """The bug, as an assertion: these two used to produce none."""
    assert _webrtc_flags("disabled"), "webrtcMode=disabled emitted no flags"
    assert _webrtc_flags("proxy"), "webrtcMode=proxy emitted no flags"


def test_an_alias_produces_exactly_what_its_canonical_does():
    assert _webrtc_flags("disabled") == _webrtc_flags("disable")
    assert _webrtc_flags("proxy") == _webrtc_flags("disable_non_proxied_udp")


def test_default_still_adds_nothing():
    assert _webrtc_flags("default") == []


def test_disable_is_stricter_than_proxy_only():
    """If these ever come out equal, one of them has stopped meaning anything."""
    assert set(_webrtc_flags("disable")) > set(_webrtc_flags("disable_non_proxied_udp"))


# --------------------------------------------------------------------------
# 3. The JS half: evasion.js gets the same answer
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sent,expected",
    [("disabled", "disable"), ("proxy", "disable_non_proxied_udp"), ("default", "default")],
)
def test_the_injected_script_is_handed_the_canonical_mode(sent, expected):
    """evasion.js branches on exactly these strings (`MP_WEBRTC_MODE`)."""
    fp = build_fingerprint({"id": "p", "webrtcMode": sent})
    assert fp["webrtcMode"] == expected
    assert fp["webrtcPolicy"] == expected


def test_the_two_halves_cannot_disagree():
    """A profile with flags but no JS hardening (or vice versa) is the bug."""
    for spelling in ("default", "disable", "disabled", "proxy", "disable_non_proxied_udp"):
        fp_mode = build_fingerprint({"id": "p", "webrtcMode": spelling})["webrtcMode"]
        assert fp_mode == normalize_webrtc_mode(spelling)
        assert bool(_webrtc_flags(spelling)) == (fp_mode != "default")


# --------------------------------------------------------------------------
# 4. The API accepts both vocabularies and stores one
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sent", ["default", "disable", "disabled", "proxy", "disable_non_proxied_udp"])
def test_the_api_accepts_every_spelling(client, sent):
    r = client.post("/api/profiles", json={"name": f"w-{sent}", "webrtcMode": sent}, headers=HEADERS)
    assert r.status_code == 200, r.get_json()


def test_the_api_stores_the_canonical_spelling(client):
    """Storing the alias would leave the translation to every later reader."""
    r = client.post("/api/profiles", json={"name": "w", "webrtcMode": "disabled"}, headers=HEADERS)
    assert r.get_json()["profile"]["webrtcMode"] == "disable"


def test_a_meaningless_webrtc_mode_is_still_refused(client):
    r = client.post("/api/profiles", json={"name": "w", "webrtcMode": "carrier-pigeon"}, headers=HEADERS)
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "webrtcMode"
