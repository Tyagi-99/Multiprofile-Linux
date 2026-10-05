"""A profile must not enumerate fonts its claimed platform cannot have.

Measured before this existed, a profile reporting ``navigator.platform ===
'Win32'`` and ``sec-ch-ua-platform: "Windows"`` enumerated zero Windows fonts
and thirteen Linux ones::

    win: []
    lin: [Liberation Sans, Liberation Serif, Liberation Mono, Noto Sans,
          Noto Serif, Nimbus Sans, Nimbus Roman, URW Bookman,
          C059, P052, Z003, D050000L, Standard Symbols PS]

``C059``, ``P052``, ``Z003``, ``D050000L`` and ``Standard Symbols PS`` are the
URW base-35 faces ghostscript installs. They are on no Windows machine, so
they are not weak evidence about an unusual install -- they are proof.

The fix decides the font set for the engine through a per-profile fontconfig
rather than hooking ``measureText``, so every surface agrees without lying.
These tests therefore check the policy *and* the two properties that a
JavaScript hook would have broken: that canvas and DOM still agree, and that
the generic families still resolve to something sane.
"""
from __future__ import annotations

import pytest

from multiprofile.browser import fontconfig as F

WINDOWS = {"platform": "Win32", "ch_platform": "Windows", "seed": 111}
MACOS = {"platform": "MacIntel", "ch_platform": "macOS", "seed": 111}
LINUX = {"platform": "Linux x86_64", "ch_platform": "Linux", "seed": 111}

# A host whose font set is known, so the policy is tested rather than whatever
# happens to be installed on the machine running the suite.
FAKE_HOST = {
    "liberation sans": ["/f/LiberationSans-Regular.ttf"],
    "liberation serif": ["/f/LiberationSerif-Regular.ttf"],
    "liberation mono": ["/f/LiberationMono-Regular.ttf"],
    "c059": ["/f/C059-Roman.otf"],
    "p052": ["/f/P052-Roman.otf"],
    "z003": ["/f/Z003-MediumItalic.otf"],
    "d050000l": ["/f/D050000L.otf"],
    "standard symbols ps": ["/f/StandardSymbolsPS.otf"],
    "nimbus sans": ["/f/NimbusSans-Regular.otf"],
    "dejavu sans": ["/f/DejaVuSans.ttf"],
    "cantarell": ["/f/Cantarell-Regular.otf"],
    "noto color emoji": ["/f/NotoColorEmoji.ttf"],
    "georgia": ["/f/Georgia.ttf"],
}


def _plan(fp, host=None):
    return F.plan_for_platform(fp, "p1", host if host is not None else FAKE_HOST)


# --------------------------------------------------------------------------
# 1. The measurement in the docstring, as a policy assertion
# --------------------------------------------------------------------------

def test_a_windows_profile_exposes_no_ghostscript_font():
    """The five names that are proof, not evidence."""
    files = set(_plan(WINDOWS)["files"])
    for smoking_gun in ("C059-Roman.otf", "P052-Roman.otf", "Z003-MediumItalic.otf",
                        "D050000L.otf", "StandardSymbolsPS.otf"):
        assert not any(f.endswith(smoking_gun) for f in files), smoking_gun


def test_a_windows_profile_exposes_no_distribution_font():
    files = set(_plan(WINDOWS)["files"])
    assert not any("DejaVu" in f or "Cantarell" in f or "NimbusSans" in f
                   for f in files), files


def test_a_windows_profile_claims_the_core_windows_families():
    """Arial, Times New Roman and Courier New are what probes test first."""
    claimed = _plan(WINDOWS)["claimed"]
    for name in ("Arial", "Times New Roman", "Courier New"):
        assert name in claimed, claimed


def test_a_macos_profile_claims_helvetica():
    assert "Helvetica" in _plan(MACOS)["claimed"]


# --------------------------------------------------------------------------
# 2. Honesty: a name is claimed only when a real file can back it
# --------------------------------------------------------------------------

def test_calibri_is_not_claimed_without_carlito():
    """Faking Calibri with the wrong metrics is a worse tell than absence.

    A Windows box missing Calibri is unremarkable. A Calibri that measures
    like Liberation Sans is a contradiction, and a probe comparing widths
    against known Calibri metrics sees it immediately.
    """
    plan = _plan(WINDOWS)
    assert "Calibri" not in plan["claimed"]
    assert "Calibri" in plan["unbacked"]


def test_calibri_is_claimed_when_carlito_is_present():
    host = dict(FAKE_HOST, carlito=["/f/Carlito-Regular.ttf"])
    plan = _plan(WINDOWS, host)
    assert "Calibri" in plan["claimed"]
    assert plan["renames"]["Carlito"] == ["Calibri"]


def test_every_claimed_name_is_backed_by_a_file():
    plan = _plan(WINDOWS)
    backed = set(plan["renames"].keys())
    for name in plan["claimed"]:
        assert (name.lower() in FAKE_HOST
                or any(name in v for v in plan["renames"].values())), name
    assert backed <= {"Liberation Sans", "Liberation Serif", "Liberation Mono"}


def test_an_already_installed_neutral_family_is_used_directly():
    """Georgia is on the fake host and is not a Linux signal: no rename."""
    plan = _plan(WINDOWS)
    assert "Georgia" in plan["claimed"]
    assert "Georgia" not in plan["renames"]


# --------------------------------------------------------------------------
# 3. Rendering must not be collateral damage
# --------------------------------------------------------------------------

def test_emoji_survives_every_claim():
    """Every desktop has emoji, and a page without it renders visibly wrong."""
    for fp in (WINDOWS, MACOS):
        assert any("NotoColorEmoji" in f for f in _plan(fp)["files"]), fp


def test_the_generic_families_resolve(tmp_path):
    """Measured here: without this, sans-serif resolved to Courier New and
    every page would have rendered in monospace."""
    cfg = F.render_config(_plan(WINDOWS), tmp_path / "f", tmp_path / "c")
    for generic in ("sans-serif", "serif", "monospace"):
        assert f"<family>{generic}</family>" in cfg, generic


def test_a_host_we_cannot_read_is_left_alone():
    """An empty font directory is worse than the leak it would close."""
    plan = F.plan_for_platform(WINDOWS, "p1", {})
    assert plan["restrict"] is False
    assert plan["files"] == []


# --------------------------------------------------------------------------
# 4. The config itself
# --------------------------------------------------------------------------

def test_all_three_name_channels_are_rewritten(tmp_path):
    """Editing ``family`` alone leaves fullname and postscriptname announcing
    the origin, which is how "Liberation Mono" stayed matchable."""
    plan = F.plan_for_platform(
        WINDOWS, "p1", FAKE_HOST,
    )
    plan["faces"] = [{"file": "/f/LiberationMono-Regular.ttf",
                      "family": "Liberation Mono", "style": "Regular",
                      "postscriptname": "LiberationMono"}]
    cfg = F.render_config(plan, tmp_path / "f", tmp_path / "c")
    assert '<edit name="family" mode="assign"><string>Courier New</string></edit>' in cfg
    assert '<edit name="fullname" mode="assign"><string>Courier New</string></edit>' in cfg
    assert ('<edit name="postscriptname" mode="assign">'
            "<string>CourierNewPSMT</string></edit>") in cfg


def test_host_family_names_are_blocked_with_their_real_case(tmp_path):
    """fontconfig's compare="eq" is case-sensitive.

    A rule emitted for "liberation mono" never fires against Chrome's request
    for "Liberation Mono" -- and while it did not fire, the system's
    30-metric-aliases.conf substituted the name to Courier New, which now
    exists, so the request resolved and read as installed.
    """
    cfg = F.render_config(_plan(WINDOWS), tmp_path / "f", tmp_path / "c")
    assert "<string>Liberation Mono</string>" in cfg
    assert "<string>liberation mono</string>" not in cfg


def test_the_blocks_precede_the_system_include(tmp_path):
    """Order is load-bearing: after the include, the generic family lists have
    been expanded and the request is no longer the bare name."""
    cfg = F.render_config(_plan(WINDOWS), tmp_path / "f", tmp_path / "c")
    assert cfg.index("__mp_no_such_family__") < cfg.index("/etc/fonts/conf.d")


def test_host_font_directories_are_rejected(tmp_path):
    """The system include must not bring back the host's font dirs."""
    cfg = F.render_config(_plan(WINDOWS), tmp_path / "f", tmp_path / "c")
    assert "<glob>/usr/share/fonts/*</glob>" in cfg


def test_the_config_is_well_formed_xml(tmp_path):
    from xml.etree import ElementTree

    cfg = F.render_config(_plan(WINDOWS), tmp_path / "f", tmp_path / "c")
    ElementTree.fromstring(cfg)  # raises if malformed


def test_postscript_names_look_like_the_real_thing():
    """A probe reading PostScript names knows what Arial's are called."""
    assert F._names_for("Arial", "Regular") == ("Arial", "ArialMT")
    assert F._names_for("Arial", "Bold") == ("Arial Bold", "Arial-BoldMT")
    assert F._names_for("Times New Roman", "Regular")[1] == "TimesNewRomanPSMT"
    assert F._names_for("Courier New", "Italic")[1] == "CourierNewPS-ItalicMT"


# --------------------------------------------------------------------------
# 5. Profiles must not share one font list
# --------------------------------------------------------------------------

def test_two_profiles_drop_different_optional_families():
    """Real machines differ by which applications were installed. An
    identical list across every profile is a linking signal of its own.
    """
    host = dict(FAKE_HOST)
    for name in ("comic sans ms", "impact", "franklin gothic medium",
                 "palatino linotype", "lucida console"):
        host[name] = [f"/f/{name.replace(' ', '')}.ttf"]
    seen = {
        tuple(F.plan_for_platform({**WINDOWS, "seed": s}, f"p{s}", host)["claimed"])
        for s in range(1, 12)
    }
    assert len(seen) > 1, seen


def test_the_claim_is_stable_for_one_profile():
    a = _plan(WINDOWS)["claimed"]
    b = _plan(WINDOWS)["claimed"]
    assert a == b


# --------------------------------------------------------------------------
# 6. A profile claiming the host's own platform has nothing to hide
# --------------------------------------------------------------------------

def test_a_linux_profile_keeps_the_host_set():
    plan = _plan(LINUX)
    assert plan["bucket"] == "linux"
    assert plan["renames"] == {}
    assert len(plan["files"]) > 5


def test_the_platform_bucket_reads_the_claim():
    assert F._platform_bucket(WINDOWS) == "windows"
    assert F._platform_bucket(MACOS) == "macos"
    assert F._platform_bucket(LINUX) == "linux"
    assert F._platform_bucket({"platform": "iPhone"}) == "ios"
    assert F._platform_bucket({}) == "linux"


# --------------------------------------------------------------------------
# 7. Never let font policy break a launch
# --------------------------------------------------------------------------

def test_a_broken_host_query_does_not_raise(monkeypatch):
    def boom(*a, **k):
        raise OSError("fc-list is not here")

    monkeypatch.setattr(F.subprocess, "run", boom)
    assert F.available_families() == {}
    assert F.face_records() == []


def test_prepare_returns_none_when_the_host_is_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "available_families", dict)
    notes: list[str] = []
    assert F.prepare_for_profile("p1", WINDOWS, tmp_path, notes) is None
    assert any("host font set left as-is" in n for n in notes)


@pytest.mark.skipif(not F.available_families(), reason="no fc-list on this host")
def test_prepare_writes_a_usable_config(tmp_path):
    notes: list[str] = []
    conf = F.prepare_for_profile("p1", WINDOWS, tmp_path, notes)
    assert conf is not None and conf.exists()
    assert (tmp_path / "fonts").is_dir()
    assert any((tmp_path / "fonts").iterdir())
    assert any("families claimed" in n for n in notes)


@pytest.mark.skipif(not F.available_families(), reason="no fc-list on this host")
def test_a_relaunch_rebuilds_the_font_view(tmp_path):
    """A stale link from a previously claimed platform would reintroduce
    exactly the leak this closes."""
    F.prepare_for_profile("p1", LINUX, tmp_path, [])
    before = len(list((tmp_path / "fonts").iterdir()))
    F.prepare_for_profile("p1", WINDOWS, tmp_path, [])
    after = list((tmp_path / "fonts").iterdir())
    assert len(after) != before or before == 0
    assert not any("DejaVu" in f.name or "C059" in f.name for f in after)
