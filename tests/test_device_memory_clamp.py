"""F44: profiles reported a deviceMemory no Chrome can report.

Found while diffing every hooked function against bare Chromium for F3. The
diff turned up `Navigator.prototype.deviceMemory` as present in a profile and
absent in stock Chromium, which sent me to check the *values* -- and the
device pools held 16 and 6:

    _core.py  "memory": [4, 8, 16]      desktop
              "memory": [8, 16]         high-end
              "memory": [4, 6, 8]       mobile

The Device Memory spec rounds the machine's RAM to a power of two and clamps
it to [0.25, 8], deliberately, to cap the entropy the API contributes. Chrome
on a 16 GB or a 64 GB machine reports **8**. So

    navigator.deviceMemory === 16     // no Chrome build has ever said this
    navigator.deviceMemory === 6      // nor this; not a power of two

Either one identifies the profile as spoofed on its own, with no other
measurement, and it survived every realm and consistency check because
nothing compared the value against the set of values that can exist. Same
shape as F29 -- a Windows profile with no speech voices -- where the claim is
the tell rather than anything missing.

The pools keep describing hardware, because 16 GB is an ordinary machine.
`web_device_memory` is what that machine is permitted to say about itself.
"""
from __future__ import annotations

import pytest

from multiprofile.fingerprint import build_fingerprint, hardware, navigator
from multiprofile.fingerprint._core import _DEVICE_MEMORY_STEPS, web_device_memory

#: Everything Chrome can report. Anything else is a giveaway.
LEGAL = set(_DEVICE_MEMORY_STEPS)


@pytest.mark.parametrize(("ram", "reported"), [
    (16, 8),      # the value that was actually being emitted
    (6, 4),       # not a power of two
    (32, 8), (64, 8), (12, 8),
    (8, 8), (4, 4), (2, 2), (1, 1),
    (3, 2), (0.5, 0.5), (0.25, 0.25),
    (0.1, 0.25),  # below the floor
])
def test_ram_maps_to_something_chrome_could_report(ram, reported):
    assert web_device_memory(ram) == reported


def test_every_output_is_a_value_chrome_can_report():
    for ram in (0.1, 0.25, 0.5, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 24, 32, 48, 64, 128):
        assert web_device_memory(ram) in LEGAL, ram


def test_it_rounds_down_never_up():
    """Overstating is the direction that produces the impossible claim, and
    a machine understating its memory is ordinary."""
    for ram in (3, 5, 6, 7, 12, 16, 64):
        assert web_device_memory(ram) <= ram


def test_absence_is_not_a_number_to_correct():
    """iOS exposes nothing here. None must survive, or an iPhone profile
    grows a property the real device does not have -- which is F28/F29 in
    reverse."""
    assert web_device_memory(None) is None


@pytest.mark.parametrize("junk", ["", "lots", object(), [], {}])
def test_junk_claims_nothing(junk):
    assert web_device_memory(junk) is None


# --------------------------------------------------------------------------
# The three surfaces that must agree
# --------------------------------------------------------------------------

_UAS = {
    "win": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "mac": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "android": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36",
    "iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
              "Mobile/15E148 Safari/604.1",
}


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("kind", sorted(_UAS))
def test_no_generated_profile_claims_an_impossible_memory(seed, kind):
    """160 profiles across four platforms. Before the clamp this produced 16
    for a large share of them."""
    fp = build_fingerprint({"id": f"p{seed}", "seed": seed, "userAgent": _UAS[kind]})
    value = fp.get("memory")
    assert value is None or value in LEGAL, (kind, seed, value)


@pytest.mark.parametrize("seed", range(25))
def test_the_three_surfaces_report_the_same_memory(seed):
    """navigator, hardware and the injected fingerprint read the same pool
    with the same salt. If only one of them clamped, a page and the
    consistency report would disagree about the same profile."""
    profile = {"id": f"p{seed}", "seed": seed, "userAgent": _UAS["win"]}
    fp = build_fingerprint(profile)
    nav = navigator.build(seed, profile)
    hw = hardware.build(seed, profile)
    assert fp["memory"] == nav["deviceMemory"] == hw["deviceMemory"]
    assert fp["memory"] in LEGAL


def test_an_iphone_profile_still_exposes_nothing():
    fp = build_fingerprint({"id": "i", "seed": 7, "userAgent": _UAS["iphone"]})
    assert fp.get("memory") is None
