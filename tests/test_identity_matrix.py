"""The supported identity matrix, and the default it decides.

The parity roadmap asks for an enforced matrix and says plainly what to do
when a claim cannot meet it: *"If a Windows identity cannot meet this gate,
offer a coherent native-Linux mode and label cross-OS emulation
experimental."*

The measurement that settles it, taken on this host against a stock Chromium
control in the same session:

    Windows-exclusive faces   product 0 of 10 present, control 0 of 10
    Liberation Mono           product present,         control present

Both directions are wrong, and only one is fixable here. Absent Windows faces
need the font files, which is licensing. The present Linux face is the
contradiction: **no Windows machine has Liberation Mono**, and it cannot be
hidden by renaming because Chrome reads the font file's internal name table,
which fontconfig cannot rewrite.

So the native platform is ``supported`` and every cross-OS claim is
``experimental`` -- coherent on every surface the product controls, with one
surface known to contradict it. Experimental is not refused: an operator who
asks for Windows gets Windows, and gets told.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


# --------------------------------------------------------------------------
# 1. The matrix itself
# --------------------------------------------------------------------------

def test_the_host_claim_is_identified():
    from multiprofile.fingerprint.identity_matrix import host_platform_claim

    assert host_platform_claim() in ("linux", "macos", "windows"), (
        "the host platform could not be identified, so 'native' is undefined"
    )


def test_the_native_claim_is_supported():
    from multiprofile.fingerprint.identity_matrix import (
        SUPPORTED,
        host_platform_claim,
        verdict_for,
    )

    verdict = verdict_for(host_platform_claim())
    assert verdict.status == SUPPORTED, verdict.to_dict()
    assert verdict.native is True
    assert verdict.contradicts == []


def test_a_cross_os_claim_is_experimental_and_says_why():
    """Not 'unsupported'. The claim is coherent on every surface the product
    controls; one surface it does not control contradicts it, and the reason
    has to name that surface or an operator cannot weigh it."""
    from multiprofile.fingerprint.identity_matrix import (
        EXPERIMENTAL,
        host_platform_claim,
        verdict_for,
    )

    cross = "windows" if host_platform_claim() != "windows" else "linux"
    verdict = verdict_for(cross)
    assert verdict.status == EXPERIMENTAL, verdict.to_dict()
    assert verdict.native is False
    assert "fonts" in verdict.contradicts, verdict.to_dict()
    assert verdict.reasons, "experimental with no stated reason is just a label"
    joined = " ".join(verdict.reasons).lower()
    assert "name table" in joined or "enumerable" in joined, verdict.reasons


def test_mobile_claims_name_the_engine_build():
    """A phone preset moves the viewport and the device metrics; it does not
    change which engine binary is running. That is a different reason from
    the font one and must be stated separately."""
    from multiprofile.fingerprint.identity_matrix import EXPERIMENTAL, verdict_for

    for claim in ("android", "iphone"):
        verdict = verdict_for(claim)
        assert verdict.status == EXPERIMENTAL, verdict.to_dict()
        assert "engine build" in verdict.contradicts, verdict.to_dict()


def test_an_unmodelled_claim_is_unsupported_not_experimental():
    from multiprofile.fingerprint.identity_matrix import UNSUPPORTED, verdict_for

    for claim in ("", "freebsd", "haiku", "nonsense"):
        assert verdict_for(claim).status == UNSUPPORTED, claim


def test_the_matrix_covers_every_pool_the_product_models():
    """A claim the fingerprint layer can produce and the matrix cannot judge
    is a claim that ships without a verdict."""
    from multiprofile.fingerprint._core import POOL_MAP
    from multiprofile.fingerprint.identity_matrix import matrix

    judged = {v.claim for v in matrix()}
    canonical = {"win": "windows", "mac": "macos", "ios": "iphone"}
    for key in POOL_MAP:
        assert canonical.get(key, key) in judged, key


def test_aliases_resolve_to_the_same_verdict():
    from multiprofile.fingerprint.identity_matrix import verdict_for

    for alias, canonical in (("win", "windows"), ("mac", "macos"), ("ios", "iphone")):
        assert verdict_for(alias).to_dict() == verdict_for(canonical).to_dict()


# --------------------------------------------------------------------------
# 2. The default it decides
# --------------------------------------------------------------------------

def test_a_new_profile_claims_the_host_platform():
    """The shipped default used to claim Windows on this Linux host, by
    falling through `_detect_pool("")` whose default is the Windows pool."""
    from multiprofile.fingerprint import build_fingerprint
    from multiprofile.fingerprint.identity_matrix import host_platform_claim
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "native-default"})
    fingerprint = build_fingerprint(profile)
    expected = {
        "linux": "Linux x86_64", "windows": "Win32", "macos": "MacIntel",
    }[host_platform_claim()]
    assert fingerprint["platform"] == expected, fingerprint["platform"]


def test_the_choice_is_recorded_on_the_profile_not_left_to_a_fallback():
    """So the operator can see it and change it, and so the next change of
    default cannot silently re-identify this profile."""
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "recorded"})
    assert profile.get("userAgentId"), profile
    assert profile.get("userAgent"), "the id did not resolve to an identity"


def test_an_existing_profile_keeps_the_identity_it_has():
    """The reason the default is applied at create time and not in
    `_detect_pool`.

    A profile already in the database has no `userAgentId`. Changing the
    derivation default would re-identify it -- a profile with live logged-in
    sessions would come back as a different machine, which is the opposite of
    what a profile manager is for.
    """
    from multiprofile.fingerprint import build_fingerprint

    legacy = {"id": "profile_legacy", "name": "legacy", "seed": 424242}
    assert build_fingerprint(legacy)["platform"] == "Win32", (
        "an existing profile's derived platform changed, which re-identifies "
        "every profile created before the default moved"
    )


def test_an_operator_choice_still_wins():
    """Experimental is a label, not a refusal."""
    from multiprofile.fingerprint import build_fingerprint
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "chosen", "userAgentId": "win_chrome"})
    assert build_fingerprint(profile)["platform"] == "Win32"


def test_an_explicit_user_agent_is_not_overwritten():
    mine = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
    from multiprofile.fingerprint import build_fingerprint
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "mine", "userAgent": mine})
    assert profile["userAgent"] == mine
    assert build_fingerprint(profile)["platform"] == "MacIntel"


@pytest.mark.parametrize("field", ("deviceId", "device_id", "devicePresetId"))
def test_a_device_preset_is_not_overridden_by_the_default(field):
    """A preset carries its own platform; stamping one would fight it.

    Every spelling, because the canonical field is `deviceId` and the first
    version of this guard checked only `devicePresetId` -- so a real
    `deviceId: pixel-9` profile was stamped with the host claim and lost its
    Android User-Agent. `validation.py` already warns that this exact
    confusion has cost a test before; parametrising is what stops it costing
    another one.
    """
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": f"preset-{field}", field: "pixel-9"})
    assert not profile.get("userAgentId"), profile.get("userAgentId")


def test_a_mobile_profile_is_not_stamped_desktop():
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "mobile", "isMobile": True})
    assert not profile.get("userAgentId"), profile.get("userAgentId")


# --------------------------------------------------------------------------
# 3. The operator has to be able to see it
# --------------------------------------------------------------------------
#
# "Enforced" that an operator cannot see is a policy nobody can act on. The
# verdict reaches three places on purpose, because each answers a different
# question at a different moment: /api/health for "what can this machine
# back", the profile detail for "what does THIS profile claim", and a launch
# note for the moment the decision goes live.


def _client():
    from multiprofile.api.server import create_app

    return create_app().test_client()


def test_health_reports_the_matrix():
    body = _client().get("/api/health").get_json()
    matrix = body.get("identityMatrix") or {}
    assert matrix.get("host", {}).get("platformClaim"), matrix
    assert matrix.get("supported"), matrix
    assert matrix.get("experimental"), matrix
    claims = {c["claim"] for c in matrix.get("claims") or []}
    assert {"linux", "windows", "macos", "android", "iphone"} <= claims, claims


def test_the_profile_detail_carries_its_own_verdict():
    """A global diagnostic does not answer "is *this* profile safe to move an
    account onto"."""
    client = _client()
    native = client.post("/api/profiles", json={"name": "d-native"}).get_json()
    cross = client.post(
        "/api/profiles", json={"name": "d-win", "userAgentId": "win_chrome"}
    ).get_json()

    def verdict(created):
        pid = created["profile"]["id"]
        body = client.get(f"/api/profiles/{pid}").get_json()
        return (body.get("profile", body) or {}).get("identity") or {}

    assert verdict(native).get("status") == "supported", verdict(native)
    experimental = verdict(cross)
    assert experimental.get("status") == "experimental", experimental
    assert "fonts" in (experimental.get("contradicts") or []), experimental


def test_an_experimental_claim_is_noted_at_launch():
    """The moment the decision goes live, in the place the operator is
    actually looking."""
    from multiprofile.browser.launcher import _note_identity_matrix

    notes: list[str] = []
    _note_identity_matrix(notes, {"platform": "Win32", "ch_platform": "Windows"}, "p1")
    assert notes, "an experimental claim produced no launch note"
    assert "EXPERIMENTAL" in notes[0], notes
    assert "fonts" in notes[0], notes


def test_a_supported_claim_is_not_noted_at_launch():
    """A note on every launch is a note nobody reads."""
    from multiprofile.browser.launcher import _note_identity_matrix

    notes: list[str] = []
    _note_identity_matrix(
        notes, {"platform": "Linux x86_64", "ch_platform": "Linux"}, "p2")
    assert notes == [], notes


def test_the_launch_note_is_not_recorded_as_an_identity_fault():
    """A cross-OS claim has not *failed* -- it is doing what was asked on
    every surface the product controls. Recording it as a fault would put a
    permanent entry in /api/health for a configuration the operator chose,
    and devalue the faults that mean something broke."""
    from multiprofile.browser import identity_faults
    from multiprofile.browser.launcher import _note_identity_matrix

    before = len(identity_faults.recent(limit=100))
    notes: list[str] = []
    _note_identity_matrix(notes, {"platform": "Win32", "ch_platform": "Windows"}, "p3")
    assert notes, "precondition: the note should have been produced"
    assert len(identity_faults.recent(limit=100)) == before, (
        "the experimental note was recorded as an identity fault"
    )
