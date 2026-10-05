"""WebGPU and WebGL must describe the same card.

A page can read both, and they use different vocabularies for the same
hardware. Measured on this host before the fix, one profile said both of
these at once::

    WebGL   ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ...)
    WebGPU  requestAdapter() -> null

No Windows machine with an RTX 4060 has no WebGPU adapter. The null was ours,
not the host's: the launcher forces ``--ozone-platform=x11`` because window
placement needs it, and that leaves Dawn without an adapter. Measured against
stock Chromium 151 on the same host::

    --ozone-platform=x11    ... requestAdapter() -> null
    --ozone-platform=wayland ... {vendor: "intel", architecture: "gen-12lp"}
    --ozone-platform=x11 --enable-features=Vulkan
                             ... {vendor: "intel", architecture: "gen-12lp"}

And where an adapter *was* returned it described the host truthfully and
identically for every profile, so the surface linked them to each other as
well as contradicting the WebGL claim.
"""
from __future__ import annotations

import pytest

from multiprofile.fingerprint import build_fingerprint
from multiprofile.fingerprint._core import webgpu_identity

# --------------------------------------------------------------------------
# 1. The derivation: one source of truth for both vocabularies
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("renderer", "vendor", "architecture"), [
    ("ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
     "nvidia", "ada"),
    ("ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
     "nvidia", "ampere"),
    ("ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0, D3D11)",
     "nvidia", "turing"),
    ("ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)",
     "amd", "rdna-2"),
    ("ANGLE (Apple, Apple M2 Pro, OpenGL ES 3.0)", "apple", "apple-m2"),
    ("ANGLE (Qualcomm, Adreno (TM) 740, OpenGL ES 3.2)", "qualcomm", "adreno-7xx"),
])
def test_the_architecture_follows_the_claimed_card(renderer, vendor, architecture):
    """Chrome reports the GPU family, not the model: an RTX 4060 is ada."""
    info = webgpu_identity(renderer)
    assert info["vendor"] == vendor, info
    assert info["architecture"] == architecture, info


def test_an_unrecognised_card_claims_nothing():
    """Inventing an identity that disagreed with the WebGL string would
    recreate the contradiction this exists to remove."""
    assert webgpu_identity("ANGLE (Something, Unknown Device 9999)") == {}


def test_every_pooled_gpu_has_a_webgpu_identity():
    """A pool entry with no mapping would launch a profile whose WebGPU
    surface says nothing while WebGL names a card."""
    missing = []
    for n in range(40):
        fp = build_fingerprint({"id": f"p{n}"})
        if not fp["gpu"].get("webgpu"):
            missing.append(fp["gpu"]["renderer"])
    assert not missing, missing


def test_the_identity_is_stable_for_a_profile():
    a = build_fingerprint({"id": "p1"})["gpu"]["webgpu"]
    b = build_fingerprint({"id": "p1"})["gpu"]["webgpu"]
    assert a == b


def test_the_fields_are_the_ones_chrome_reports():
    info = build_fingerprint({"id": "p1"})["gpu"]["webgpu"]
    assert set(info) == {"vendor", "architecture", "device", "description"}
    # Chrome reports these lowercase.
    assert info["vendor"] == info["vendor"].lower()


# --------------------------------------------------------------------------
# 2. The launch flag that keeps an adapter at all
# --------------------------------------------------------------------------

def test_the_launch_keeps_webgpu_alive(monkeypatch):
    """Vulkan lives in the composed --enable-features list rather than in the
    GPU args, because Chrome honours only the last such flag and a second one
    would drop it."""
    import multiprofile.browser.launcher as L

    monkeypatch.delenv("MULTIPROFILE_WEBGPU", raising=False)
    assert "Vulkan" in L.chrome_enabled_features()


def test_webgpu_can_be_turned_off_for_a_broken_vulkan_stack(monkeypatch):
    import multiprofile.browser.launcher as L

    monkeypatch.setenv("MULTIPROFILE_WEBGPU", "0")
    assert "Vulkan" not in L.chrome_enabled_features()
