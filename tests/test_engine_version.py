"""The claimed Chrome version must come from the engine, not from a literal.

F5: every UA pool, all seven device presets and the UA picker were written
against Chrome 131. The engine they run on is whatever Chromium the host
installed -- 151 on the primary dev machine, ~20 stable releases later. A
page can tell, twice over: by probing an engine feature that shipped after
131, and by cohort alone, since Chrome 131 has negligible real-world share.

Measured before the fix, a profile with no preset and no explicit UA::

    User-Agent:  ... Chrome/131.0.0.0 Safari/537.36
    sec-ch-ua:   "Google Chrome";v="131", "Chromium";v="131", ...
    engine:      Chromium 151.x                      <- 20 releases apart

The rules these tests pin down:
  1. a pool default and a preset track the installed binary
  2. an EXPLICIT operator UA is never renumbered -- they may be matching a
     fingerprint bought elsewhere -- but the disagreement is reported
  3. an unknown engine version yields the literals plus an "unverified"
     record; it never yields an invented number
  4. the UA string carries the reduced <major>.0.0.0 form while
     Sec-CH-UA-Full-Version-List carries the real build, because that is
     what a real Chrome sends. Publishing "131.0.0.0" in both claimed a
     build number no Chrome release has ever had.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from multiprofile import engine as E  # noqa: E402
from multiprofile.fingerprint import build_fingerprint  # noqa: E402

PINNED = "151.0.7654.32"


@pytest.fixture
def pinned_engine(monkeypatch):
    """Claim a known engine version without needing one installed."""
    monkeypatch.setenv("MULTIPROFILE_ENGINE_VERSION", PINNED)
    monkeypatch.delenv("MULTIPROFILE_CLAIM_ENGINE_VERSION", raising=False)
    E.reset_engine_version_cache()
    yield E.detect_engine_version()
    E.reset_engine_version_cache()


@pytest.fixture
def unknown_engine(monkeypatch):
    """No binary and no pin: the version cannot be checked."""
    monkeypatch.delenv("MULTIPROFILE_ENGINE_VERSION", raising=False)
    monkeypatch.delenv("MULTIPROFILE_CLAIM_ENGINE_VERSION", raising=False)
    monkeypatch.setattr(E, "find_browser_binary", lambda: None)
    E.reset_engine_version_cache()
    yield E.detect_engine_version()
    E.reset_engine_version_cache()


# --------------------------------------------------------------------------
# 1. Detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "output,expected",
    [
        ("Chromium 151.0.7654.32 Arch Linux", "151.0.7654.32"),
        ("Google Chrome 141.0.7390.54 ", "141.0.7390.54"),
        ("Chromium 141.0.7390.37 snap", "141.0.7390.37"),
        ("Chromium 120.0.6099", "120.0.6099.0"),
        ("Chromium unknown", None),
        ("", None),
    ],
)
def test_parse_version_reads_real_banners(output, expected):
    assert E.parse_version(output) == expected


def test_detection_asks_the_binary(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_ENGINE_VERSION", raising=False)
    monkeypatch.delenv("MULTIPROFILE_CLAIM_ENGINE_VERSION", raising=False)
    monkeypatch.setattr(E, "find_browser_binary", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(E, "_probe_binary", lambda b: ("151.0.7654.32", "Chromium 151.0.7654.32"))
    E.reset_engine_version_cache()
    try:
        ev = E.detect_engine_version()
        assert (ev.major, ev.full, ev.source, ev.known) == (151, "151.0.7654.32", "binary", True)
        assert ev.ua_version == "151.0.0.0"
    finally:
        E.reset_engine_version_cache()


def test_unknown_engine_invents_nothing(unknown_engine):
    assert unknown_engine.known is False
    assert unknown_engine.major is None
    assert unknown_engine.ua_version is None
    # and it says why, so the launch note can report it as unverified
    assert "PATH" in unknown_engine.detail


def test_a_failing_binary_is_unavailable_not_a_guess(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_ENGINE_VERSION", raising=False)
    monkeypatch.delenv("MULTIPROFILE_CLAIM_ENGINE_VERSION", raising=False)
    monkeypatch.setattr(E, "find_browser_binary", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(E, "_probe_binary", lambda b: (None, "boom"))
    E.reset_engine_version_cache()
    try:
        ev = E.detect_engine_version()
        assert ev.source == "unavailable" and ev.known is False and ev.detail == "boom"
    finally:
        E.reset_engine_version_cache()


def test_detection_is_cached_per_process(monkeypatch):
    calls = []
    monkeypatch.delenv("MULTIPROFILE_ENGINE_VERSION", raising=False)
    monkeypatch.setattr(E, "find_browser_binary", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(E, "_probe_binary", lambda b: (calls.append(b), ("151.0.7654.32", "x"))[1])
    E.reset_engine_version_cache()
    try:
        E.detect_engine_version()
        E.detect_engine_version()
        assert len(calls) == 1, "a launch must not pay for a subprocess it already ran"
    finally:
        E.reset_engine_version_cache()


def test_alignment_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_CLAIM_ENGINE_VERSION", "0")
    monkeypatch.setenv("MULTIPROFILE_ENGINE_VERSION", PINNED)
    E.reset_engine_version_cache()
    try:
        ev = E.detect_engine_version()
        assert ev.source == "disabled" and ev.known is False
        ua = "Mozilla/5.0 (X11; Linux x86_64) ... Chrome/131.0.0.0 Safari/537.36"
        assert E.align_ua_string(ua, ev) == ua
    finally:
        E.reset_engine_version_cache()


# --------------------------------------------------------------------------
# 2. Rewrites
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ua,expected",
    [
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        ),
        # CriOS is the iPhone pool's token; matching only "Chrome/" left it behind
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) CriOS/131.0.0.0 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) CriOS/151.0.0.0 Mobile/15E148 Safari/604.1",
        ),
        # Edge tracks the Chromium major, so BOTH tokens move
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0",
        ),
    ],
)
def test_align_ua_string_moves_only_the_version(ua, expected, pinned_engine):
    assert E.align_ua_string(ua, pinned_engine) == expected


def test_a_safari_ua_has_no_chromium_token_and_is_untouched(pinned_engine):
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Safari/605.1.15")
    assert E.align_ua_string(ua, pinned_engine) == ua


def test_align_brands_keeps_grease_and_the_incoming_key(pinned_engine):
    brands = [
        {"brand": "Google Chrome", "version": "131"},
        {"brand": "Chromium", "version": "131"},
        {"brand": "Not-A.Brand", "version": "99"},
    ]
    assert E.align_brands(brands, pinned_engine) == [
        {"brand": "Google Chrome", "version": "151"},
        {"brand": "Chromium", "version": "151"},
        {"brand": "Not-A.Brand", "version": "99"},  # GREASE is noise, not a claim
    ]
    # the preset catalogue spells it fullVersion; the key must survive
    preset_shape = [{"brand": "Chromium", "fullVersion": "131.0.6778.73"}]
    assert E.align_brands(preset_shape, pinned_engine, full=True) == [
        {"brand": "Chromium", "fullVersion": PINNED}
    ]


def test_rewrites_are_no_ops_when_the_engine_is_unknown(unknown_engine):
    ua = "Mozilla/5.0 (X11; Linux x86_64) ... Chrome/131.0.0.0 Safari/537.36"
    brands = [{"brand": "Chromium", "version": "131"}]
    assert E.align_ua_string(ua, unknown_engine) == ua
    assert E.align_brands(brands, unknown_engine) == brands


def test_align_ua_metadata_rewrites_both_lists(pinned_engine):
    meta = {
        "brands": [{"brand": "Chromium", "version": "131"}],
        "fullVersionList": [{"brand": "Chromium", "version": "131.0.6778.73"}],
        "platform": "Windows",
    }
    out = E.align_ua_metadata(meta, pinned_engine)
    assert out["brands"] == [{"brand": "Chromium", "version": "151"}]
    assert out["fullVersionList"] == [{"brand": "Chromium", "version": PINNED}]
    assert out["platform"] == "Windows"
    assert meta["brands"][0]["version"] == "131", "must not mutate the caller's dict"


# --------------------------------------------------------------------------
# 3. build_fingerprint
# --------------------------------------------------------------------------

def test_pool_default_ua_tracks_the_engine(pinned_engine):
    fp = build_fingerprint({"id": "seed-a"})
    assert "Chrome/151.0.0.0" in fp["userAgent"]
    assert {b["version"] for b in fp["ch_brands"] if "Not-A" not in b["brand"]} == {"151"}


def test_every_pool_default_carries_a_version_token_the_parser_can_see(pinned_engine):
    """A pool whose token the parser misses silently keeps the 131 literal.

    The iPhone pool says ``CriOS/``; the old parser only matched ``Chrome/``,
    so that pool fell back to the hardcoded 131 no matter what the engine
    was -- and would now disagree with its own aligned UA string.
    """
    from multiprofile.fingerprint._core import POOL_MAP, _chrome_major

    for name, pool in sorted(POOL_MAP.items()):
        aligned = E.align_ua_string(pool["userAgent"], pinned_engine)
        assert "151.0.0.0" in aligned, f"{name}: {aligned!r} was not realigned"
        assert _chrome_major(aligned) == 151, f"{name}: brands would claim {_chrome_major(aligned)}"


def test_full_version_list_carries_the_real_build_not_the_reduced_form(pinned_engine):
    fp = build_fingerprint({"id": "seed-b"})
    assert "Chrome/151.0.0.0" in fp["userAgent"], "the UA string uses the reduced form"
    reals = {b["version"] for b in fp["ch_full_version_list"] if "Not-A" not in b["brand"]}
    assert reals == {PINNED}, "the full-version list must carry the real build"


def test_an_explicit_operator_ua_is_never_renumbered(pinned_engine):
    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    fp = build_fingerprint({"id": "seed-c", "userAgent": ua})
    assert fp["userAgent"] == ua
    # and the hints follow the UA, so the two never claim different browsers
    assert {b["version"] for b in fp["ch_brands"] if "Not-A" not in b["brand"]} == {"120"}


def test_unknown_engine_keeps_the_literal_and_records_it(unknown_engine):
    fp = build_fingerprint({"id": "seed-d"})
    assert "Chrome/131.0.0.0" in fp["userAgent"]
    assert fp["engineVersion"]["source"] == "unavailable"
    assert fp["engineVersion"]["major"] is None, "an unchecked claim is not a pass"


# --------------------------------------------------------------------------
# 4. The launch path
# --------------------------------------------------------------------------

def test_a_preset_ua_and_its_hints_are_aligned_together(pinned_engine):
    from multiprofile.browser.launcher import _resolve_wire_identity
    from multiprofile.devices.registry import registry

    preset = registry.get("win11-chrome")
    assert preset is not None and "Chrome/131" in preset.user_agent, "the literal under test"

    fp = build_fingerprint({"id": "seed-e", "deviceId": preset.id})
    ua, override = _resolve_wire_identity({"id": "seed-e", "deviceId": preset.id}, fp, preset)
    assert "Chrome/151.0.0.0" in ua
    meta = override["userAgentMetadata"]
    assert {b["version"] for b in meta["brands"] if "Not-A" not in b["brand"]} == {"151"}


def test_explicit_ua_beside_a_preset_leaves_both_on_the_operators_version(pinned_engine):
    """Never align half an identity: the UA and the hints move together."""
    from multiprofile.browser.launcher import _resolve_wire_identity
    from multiprofile.devices.registry import registry

    preset = registry.get("win11-chrome")
    explicit = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    profile = {"id": "seed-f", "deviceId": preset.id, "userAgent": explicit}
    fp = build_fingerprint(profile)
    ua, override = _resolve_wire_identity(profile, fp, preset)
    assert ua == explicit
    majors = {b["version"] for b in override["userAgentMetadata"]["brands"] if "Not-A" not in b["brand"]}
    assert majors == {"131"}, "the preset's own hints, untouched — not silently moved to 151"


def test_launch_notes_report_the_authority_for_the_claim(pinned_engine):
    from multiprofile.browser.launcher import _engine_version_notes

    notes = _engine_version_notes("... Chrome/151.0.0.0 ...", "")
    assert any("Claimed Chrome version: 151" in n for n in notes)
    assert not any("UNVERIFIED" in n for n in notes)


def test_launch_notes_call_an_unchecked_claim_unverified(unknown_engine):
    from multiprofile.browser.launcher import _engine_version_notes

    notes = _engine_version_notes("... Chrome/131.0.0.0 ...", "")
    assert any("UNVERIFIED" in n for n in notes)


def test_launch_notes_flag_an_explicit_ua_that_disagrees(pinned_engine):
    from multiprofile.browser.launcher import _engine_version_notes

    explicit = "... Chrome/120.0.0.0 ..."
    notes = _engine_version_notes(explicit, explicit)
    assert any("claims Chrome 120" in n and "engine is 151" in n for n in notes)


# --------------------------------------------------------------------------
# 5. What the operator is shown
# --------------------------------------------------------------------------

def test_the_ua_picker_offers_the_engine_it_will_actually_run(pinned_engine):
    from multiprofile.api.user_agents import user_agent_catalog

    entries = {e["id"]: e for e in user_agent_catalog()}
    assert "Chrome/151.0.0.0" in entries["win_chrome"]["ua"]
    assert entries["win_chrome"]["name"] == "Windows 11 — Chrome 151"
    assert "Edg/151.0.0.0" in entries["win_edge"]["ua"]
    # Safari entries carry no Chromium token and keep their own labels
    assert entries["mac_safari"]["name"] == "macOS Sonoma — Safari 18"
    assert "Version/18.1" in entries["mac_safari"]["ua"]


def test_the_device_catalogue_shows_the_claim_a_launch_would_make(pinned_engine):
    from multiprofile.devices.registry import preset_view, registry

    view = preset_view(registry.get("win11-chrome"))
    assert "Chrome/151.0.0.0" in view["user_agent"]
    assert view["browser_version_resolved"] == 151
    assert {b["version"] for b in view["client_hints"]["brands"] if "Not-A" not in b["brand"]} == {"151"}
    assert {b["fullVersion"] for b in view["client_hints"]["full_version_list"]
            if "Not-A" not in b["brand"]} == {PINNED}


def test_no_preset_still_pins_a_version_policy():
    """`pinned:131` was the policy that let the drift happen silently."""
    from multiprofile.devices.registry import registry

    for p in registry.list():
        assert p.browser_version_policy == "match-engine", p.id
