"""F42: picking an identity from the catalogue had to actually produce it.

``GET /api/user-agents`` offers ten identities — Windows, macOS, Linux,
Android, iOS, iPadOS. ``userAgentId`` was accepted by the API, validated,
aliased from ``uaId``, stored on the profile and carried by templates. It was
read by nothing that builds a fingerprint, which derives everything from
``userAgent`` and ``isMobile``.

Measured before the fix, creating one profile per catalogue id:

    win_chrome     -> fp.platform='Win32'   ch_platform='Windows'
    mac_chrome     -> fp.platform='Win32'   ch_platform='Windows'
    linux_chrome   -> fp.platform='Win32'   ch_platform='Windows'
    pixel_9        -> fp.platform='Win32'   ch_platform='Windows'
    iphone_15      -> fp.platform='Win32'   ch_platform='Windows'

Every selection produced Windows. This is the shape of F16 (wizard steps that
configured nothing) and F19 (an API vocabulary matching nothing downstream): a
control present in the interface and absent from the path.

The second-order cost is worse than the first. An operator who cannot make a
macOS profile at least notices eventually; a fleet in which *every* profile
claims Windows regardless of what was chosen shares a value across all of
them, which is itself a linkage signal — the same failure as F31, where every
profile claimed New York.
"""
from __future__ import annotations

import pytest

from multiprofile.api.user_agents import known_user_agent_ids, resolve_user_agent_id


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


@pytest.fixture
def client():
    from multiprofile.api.server import create_app

    return create_app().test_client()


def _fingerprint_for(**payload):
    from multiprofile.storage.profiles import create_profile

    return create_profile({"name": "t", **payload}).get("fingerprint") or {}


# --------------------------------------------------------------------------
# 1. The selection reaches the identity
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("uaid", "platform", "ch_platform"), [
    ("win_chrome", "Win32", "Windows"),
    ("win_edge", "Win32", "Windows"),
    ("mac_chrome", "MacIntel", "macOS"),
    ("linux_chrome", "Linux x86_64", "Linux"),
    ("pixel_9", "Linux armv8l", "Android"),
    ("iphone_15", "iPhone", "iOS"),
])
def test_choosing_an_identity_produces_that_identity(uaid, platform, ch_platform):
    """The finding, one row per selection that used to come back Windows."""
    fp = _fingerprint_for(userAgentId=uaid)
    assert fp.get("platform") == platform, f"{uaid} claimed {fp.get('platform')}"
    assert fp.get("ch_platform") == ch_platform


def test_not_every_profile_claims_windows():
    """The fleet-level failure: one shared value across every profile is a
    linkage signal, the same shape as F31's shared New York."""
    platforms = {
        _fingerprint_for(userAgentId=uaid).get("platform")
        for uaid in ("win_chrome", "mac_chrome", "linux_chrome", "pixel_9", "iphone_15")
    }
    assert len(platforms) >= 4, platforms


def test_a_mobile_catalogue_entry_makes_a_mobile_profile():
    """`isMobile` drives the whole mobile pool -- touch points, screen, GPU."""
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "t", "userAgentId": "pixel_9"})
    assert profile["isMobile"] is True
    assert profile["fingerprint"]["touch"] > 0


def test_a_desktop_entry_does_not_claim_touch():
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "t", "userAgentId": "win_chrome"})
    assert profile["isMobile"] is False


def test_the_user_agent_is_realigned_to_the_installed_engine():
    """The catalogue literals say 131; the engine here is not 131 (F5)."""
    from multiprofile.engine import detect_engine_version

    engine = detect_engine_version()
    if not engine.known:
        pytest.skip("engine version unknown on this host")
    fp = _fingerprint_for(userAgentId="mac_chrome")
    assert f"Chrome/{engine.major}." in fp["userAgent"]


# --------------------------------------------------------------------------
# 2. An explicit User-Agent still wins
# --------------------------------------------------------------------------

def test_an_explicit_user_agent_is_not_overwritten():
    """Someone who pasted a UA is matching a fingerprint they got elsewhere;
    overwriting it from a leftover id would break the pairing."""
    mine = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    fp = _fingerprint_for(userAgentId="mac_chrome", userAgent=mine)
    assert fp["userAgent"] == mine
    assert fp["platform"] == "Linux x86_64"


def test_no_id_and_no_user_agent_still_produces_an_identity():
    """The F1 guarantee: a profile must always say what it is."""
    fp = _fingerprint_for()
    assert fp.get("userAgent")
    assert fp.get("platform")


# --------------------------------------------------------------------------
# 3. An unknown id is refused, not ignored
# --------------------------------------------------------------------------

def test_an_unknown_id_is_rejected_by_the_api(client):
    """Storing it and doing nothing is how a typo produces a profile claiming
    something nobody picked."""
    r = client.post("/api/profiles", json={"name": "t", "userAgentId": "macos_chrome"})
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert err["field"] == "userAgentId"
    assert "win_chrome" in err["message"]


def test_the_alias_is_validated_too(client):
    """`uaId` is aliased to `userAgentId` by the server, so it must not be a
    way around the check."""
    r = client.post("/api/profiles", json={"name": "t", "uaId": "nonsense"})
    assert r.status_code == 400


@pytest.mark.parametrize("uaid", known_user_agent_ids())
def test_every_catalogue_id_is_accepted(uaid, client):
    """The validator and the catalogue must not drift apart."""
    r = client.post("/api/profiles", json={"name": uaid, "userAgentId": uaid})
    assert r.status_code == 200, r.get_json()


def test_an_id_that_predates_the_catalogue_does_not_crash_a_load():
    """A stored profile naming an id this build no longer has must still
    load, with the pool default rather than an invented identity."""
    from multiprofile.storage.profiles import _normalize_fields

    profile = {"name": "old", "userAgentId": "retired_preset"}
    _normalize_fields(profile)
    assert not profile.get("userAgent")


# --------------------------------------------------------------------------
# 4. The resolver itself
# --------------------------------------------------------------------------

def test_resolution_is_case_insensitive():
    assert resolve_user_agent_id("MAC_CHROME")["id"] == "mac_chrome"


@pytest.mark.parametrize("junk", ["", "   ", None, "nope"])
def test_junk_resolves_to_nothing_rather_than_raising(junk):
    assert resolve_user_agent_id(junk) is None


def test_the_catalogue_ids_are_unique():
    ids = known_user_agent_ids()
    assert len(ids) == len(set(ids))


def test_the_alias_works_on_update_too(client):
    """It used to be applied only on create, so `uaId` on a PUT was accepted
    and silently dropped -- F8 and F21's shape."""
    pid = client.post("/api/profiles", json={"name": "t"}).get_json()["profile"]["id"]
    r = client.put(f"/api/profiles/{pid}", json={"uaId": "mac_chrome"})
    assert r.status_code == 200
    assert r.get_json()["profile"]["fingerprint"]["ch_platform"] == "macOS"


def test_a_hand_typed_user_agent_survives_a_picker_change(client):
    """The other half of the rule: a UA the operator pasted is theirs."""
    mine = "Mozilla/5.0 (SomeThing Custom) Chrome/99.0.0.0 Safari/537.36"
    pid = client.post("/api/profiles", json={"name": "t", "userAgent": mine}).get_json()["profile"]["id"]
    r = client.put(f"/api/profiles/{pid}", json={"userAgentId": "mac_chrome"})
    assert r.status_code == 200
    assert r.get_json()["profile"]["userAgent"] == mine


def test_the_picker_can_be_changed_more_than_once(client):
    """It used to jam after one change, because the previous resolution
    looked like an operator's own choice."""
    pid = client.post("/api/profiles", json={"name": "t", "userAgentId": "win_chrome"}).get_json()["profile"]["id"]
    for uaid, expected in (("mac_chrome", "macOS"), ("linux_chrome", "Linux"), ("win_chrome", "Windows")):
        r = client.put(f"/api/profiles/{pid}", json={"userAgentId": uaid})
        assert r.status_code == 200
        assert r.get_json()["profile"]["fingerprint"]["ch_platform"] == expected, uaid
