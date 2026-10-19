"""A profile's announced browser version must follow the engine it runs on.

F5 made the version *at build time* track the installed binary. This is the
other half: a fingerprint is built once and stored, so the claim it froze
goes stale the moment the engine is upgraded. Measured on the dev host after
``pacman -Syu chromium`` took it from 151 to 154::

    CDP Browser    Chrome/154.0.8037.97      <- what is actually rendering
    announced UA   Chrome/151.0.0.0
    sec-ch-ua      "Google Chrome";v="151", "Chromium";v="151"
    uaFullVersion  151.0.0.0

Nothing reported it. ``engine_choice`` watches which *binary* answers and
never which version, and ``consistency`` has no check pairing the announced
major with the running one, so five real profiles spent the upgrade claiming
a build the host had not had for a month. A stale-but-honest engine is a
cohort problem; an engine that lies about itself is a contradiction any page
can measure.

Two mechanisms froze it, and both are covered here:

  1. the stored ``fingerprint`` dict, reused verbatim at launch
  2. a top-level ``userAgent`` resolved from ``userAgentId``, which
     ``build_fingerprint`` and the launcher both read as an *explicit*
     operator UA -- a derived value promoted to a choice by being persisted,
     and the promotion became permanent because the catalogue comparison was
     version-sensitive and so stopped matching after the upgrade

The rules:
  * a derived claim -- pool default, device preset, or a picked
    ``userAgentId`` -- tracks the engine, on every read
  * a UA the operator pasted is never renumbered, and its hints keep
    matching it rather than the engine
  * seed-derived entropy is never touched by alignment: an aligned profile
    is the same profile
  * an unknown engine aligns nothing; the stale truth beats an invention
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from multiprofile import engine as E  # noqa: E402
from multiprofile.api.user_agents import (  # noqa: E402
    is_catalogue_user_agent,
    operator_pinned_user_agent,
)
from multiprofile.fingerprint import build_fingerprint, effective_fingerprint  # noqa: E402

OLD = "151.0.7922.173"
NEW = "154.0.8037.97"

#: A UA no catalogue entry produces: a different Chrome build string shape.
PASTED = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.109 Safari/537.36 OPR/106.0.0.0"
)


@pytest.fixture
def engine_at():
    """Pin the engine version, and let a test move it mid-run."""
    def _set(version: str | None):
        if version is None:
            os.environ.pop("MULTIPROFILE_ENGINE_VERSION", None)
        else:
            os.environ["MULTIPROFILE_ENGINE_VERSION"] = version
        os.environ.pop("MULTIPROFILE_CLAIM_ENGINE_VERSION", None)
        E.reset_engine_version_cache()
        return E.detect_engine_version()

    yield _set
    os.environ.pop("MULTIPROFILE_ENGINE_VERSION", None)
    E.reset_engine_version_cache()


def _majors(fp: dict) -> set[str]:
    """Every Chromium major this fingerprint announces, from every surface."""
    found = set()
    ua = fp.get("userAgent") or ""
    import re

    for m in re.finditer(r"\b(?:Chrome|Chromium)/(\d+)", ua):
        found.add(m.group(1))
    for key in ("ch_brands", "ch_full_version_list"):
        for entry in fp.get(key) or []:
            if not E.is_grease_brand(entry.get("brand")):
                found.add(str(entry.get("version", "")).split(".")[0])
    hints = fp.get("clientHints") or {}
    for entry in hints.get("brands") or []:
        if not E.is_grease_brand(entry.get("brand")):
            found.add(str(entry.get("version", "")).split(".")[0])
    if hints.get("uaFullVersion"):
        found.add(str(hints["uaFullVersion"]).split(".")[0])
    return found


# --------------------------------------------------------------------------
# 1. The defect: a stored fingerprint outliving its engine
# --------------------------------------------------------------------------

def test_a_fingerprint_built_on_the_old_engine_announces_the_new_one(engine_at):
    engine_at(OLD)
    profile = {"id": "p1", "seed": 564121079}
    stored = build_fingerprint(profile)
    assert _majors(stored) == {"151"}, "precondition: built against 151"

    engine_at(NEW)
    profile["fingerprint"] = stored
    assert _majors(effective_fingerprint(profile)) == {"154"}


def test_every_surface_moves_together(engine_at):
    """A UA at 154 with hints at 151 would be worse than both at 151."""
    engine_at(OLD)
    stored = build_fingerprint({"id": "p1", "seed": 7})
    engine_at(NEW)
    fp = effective_fingerprint({"id": "p1", "seed": 7, "fingerprint": stored})

    assert "Chrome/154.0.0.0" in fp["userAgent"]
    assert [b["version"] for b in fp["ch_brands"] if not E.is_grease_brand(b["brand"])] == ["154", "154"]
    assert [b["version"] for b in fp["ch_full_version_list"]
            if not E.is_grease_brand(b["brand"])] == [NEW, NEW]
    assert fp["clientHints"]["uaFullVersion"] == NEW
    assert [b["version"] for b in fp["clientHints"]["brands"]
            if not E.is_grease_brand(b["brand"])] == ["154", "154"]


def test_the_ua_keeps_the_reduced_form_and_the_hints_the_full_build(engine_at):
    """Real Chrome publishes <major>.0.0.0 in the UA and the build in hints."""
    engine_at(OLD)
    stored = build_fingerprint({"id": "p1", "seed": 7})
    engine_at(NEW)
    fp = effective_fingerprint({"id": "p1", "seed": 7, "fingerprint": stored})
    assert NEW not in fp["userAgent"], "the real build must not reach the UA string"
    assert "154.0.0.0" in fp["userAgent"]


def test_the_rewrite_is_recorded_not_silent(engine_at):
    engine_at(OLD)
    stored = build_fingerprint({"id": "p1", "seed": 7})
    engine_at(NEW)
    fp = effective_fingerprint({"id": "p1", "seed": 7, "fingerprint": stored})
    assert fp["engineVersion"]["full"] == NEW
    assert fp["engineVersion"]["alignedFrom"] == OLD


def test_alignment_is_idempotent(engine_at):
    engine_at(NEW)
    stored = build_fingerprint({"id": "p1", "seed": 7})
    once = E.align_fingerprint(stored)
    twice = E.align_fingerprint(once)
    assert once == twice
    assert "alignedFrom" not in once["engineVersion"], "no drift, nothing to record"


def test_grease_brands_are_left_alone(engine_at):
    engine_at(OLD)
    stored = build_fingerprint({"id": "p1", "seed": 7})
    engine_at(NEW)
    fp = effective_fingerprint({"id": "p1", "seed": 7, "fingerprint": stored})
    grease = [b for b in fp["ch_brands"] if E.is_grease_brand(b["brand"])]
    assert grease and all(b["version"] == "99" for b in grease)


# --------------------------------------------------------------------------
# 2. The identity itself must not move
# --------------------------------------------------------------------------

ENTROPY = ("seed", "screen", "cores", "memory", "gpu", "mediaDeviceIds",
           "mediaDeviceGroupIds", "platform", "ch_platform", "languages",
           "timezone", "touch", "architecture", "bitness", "platformVersion")


def test_aligning_does_not_change_the_identity(engine_at):
    """The promise the product rests on: same seed, same machine."""
    engine_at(OLD)
    stored = build_fingerprint({"id": "p1", "seed": 564121079})
    engine_at(NEW)
    fp = effective_fingerprint({"id": "p1", "seed": 564121079, "fingerprint": stored})
    for key in ENTROPY:
        assert fp.get(key) == stored.get(key), f"{key} moved"


def test_an_unknown_engine_aligns_nothing(engine_at):
    engine_at(OLD)
    stored = build_fingerprint({"id": "p1", "seed": 7})
    engine_at(None)
    import multiprofile.engine as eng

    original = eng.find_browser_binary
    eng.find_browser_binary = lambda: None
    E.reset_engine_version_cache()
    try:
        fp = effective_fingerprint({"id": "p1", "seed": 7, "fingerprint": stored})
        assert _majors(fp) == {"151"}, "an invented number is worse than a stale one"
    finally:
        eng.find_browser_binary = original
        E.reset_engine_version_cache()


# --------------------------------------------------------------------------
# 3. Whose UA is it: derived, or pasted
# --------------------------------------------------------------------------

def test_a_catalogue_ua_is_recognised_across_an_upgrade(engine_at):
    """The comparison that decayed. This is the root cause, pinned."""
    engine_at(OLD)
    from multiprofile.api.user_agents import user_agent_catalog

    derived = next(e["ua"] for e in user_agent_catalog() if e["id"] == "linux_chrome")
    assert "151" in derived

    engine_at(NEW)
    assert not is_catalogue_user_agent(derived), "version-sensitive: no longer matches"
    assert is_catalogue_user_agent(derived, ignore_version=True), (
        "a derived UA must stay recognisable as derived after an upgrade"
    )


def test_a_picked_user_agent_is_ours_to_keep_current(engine_at):
    engine_at(OLD)
    from multiprofile.api.user_agents import user_agent_catalog

    derived = next(e["ua"] for e in user_agent_catalog() if e["id"] == "linux_chrome")
    profile = {"id": "p1", "seed": 7, "userAgentId": "linux_chrome", "userAgent": derived}
    stored = build_fingerprint(profile)
    engine_at(NEW)
    profile["fingerprint"] = stored
    assert not operator_pinned_user_agent(profile)
    assert _majors(effective_fingerprint(profile)) == {"154"}


def test_a_pasted_ua_is_never_renumbered(engine_at):
    engine_at(OLD)
    profile = {"id": "p1", "seed": 7, "userAgent": PASTED}
    stored = build_fingerprint(profile)
    engine_at(NEW)
    profile["fingerprint"] = stored
    assert operator_pinned_user_agent(profile)
    fp = effective_fingerprint(profile)
    assert fp["userAgent"] == PASTED
    assert "120" in _majors(fp), "their build must survive an engine upgrade"
    assert "154" not in _majors(fp), "and nothing may be aligned past it"


def test_a_profile_with_no_ua_of_its_own_is_not_pinned():
    assert not operator_pinned_user_agent({"id": "p1"})
    assert not operator_pinned_user_agent({"id": "p1", "userAgent": ""})


def test_stripping_versions_keeps_the_identity_of_the_string():
    stripped = E.strip_ua_versions(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    )
    assert "Chrome/*" in stripped
    assert "X11; Linux x86_64" in stripped
    assert "537.36" in stripped, "only Chromium version tokens move"


def test_a_pasted_ua_that_looks_like_a_catalogue_entry_is_still_theirs(engine_at):
    """Provenance is the recorded id, not the shape of the string.

    A plain Windows-Chrome UA is both what ``win_chrome`` resolves to and the
    most common UA on the web. Deciding provenance by matching the string
    would renumber one an operator pasted -- which is what
    ``test_explicit_ua_beside_a_preset_leaves_both_on_the_operators_version``
    in ``test_engine_version.py`` catches, and why that decision reads
    ``userAgentId`` instead.
    """
    engine_at(OLD)
    looks_derived = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    assert is_catalogue_user_agent(looks_derived, ignore_version=True), "the collision"

    profile = {"id": "p1", "seed": 7, "userAgent": looks_derived}
    stored = build_fingerprint(profile)
    engine_at(NEW)
    profile["fingerprint"] = stored
    assert operator_pinned_user_agent(profile), "no userAgentId means they typed it"
    assert effective_fingerprint(profile)["userAgent"] == looks_derived


def test_an_id_whose_ua_was_then_overwritten_is_theirs(engine_at):
    engine_at(OLD)
    profile = {"id": "p1", "seed": 7, "userAgentId": "linux_chrome", "userAgent": PASTED}
    stored = build_fingerprint(profile)
    engine_at(NEW)
    profile["fingerprint"] = stored
    assert operator_pinned_user_agent(profile)
    assert effective_fingerprint(profile)["userAgent"] == PASTED


def test_the_ua_picker_still_works_after_an_engine_upgrade(engine_at):
    """`_resolve_user_agent_id` had the same version-sensitive comparison.

    Its own docstring says treating a derived UA as operator-chosen "would
    make the picker work exactly once per profile and then jam". It jammed on
    an engine upgrade instead: found by setting `userAgentId: linux_chrome`
    on a profile whose UA the catalogue had written at 151, which stored the
    id and left the Windows UA in place.
    """
    engine_at(OLD)
    from multiprofile.api.user_agents import user_agent_catalog
    from multiprofile.storage.profiles import _resolve_user_agent_id

    win = next(e["ua"] for e in user_agent_catalog() if e["id"] == "win_chrome")
    assert "Windows" in win and "151" in win

    engine_at(NEW)
    profile = {"id": "p1", "seed": 7, "userAgent": win,
               "userAgentFromId": "win_chrome", "userAgentId": "linux_chrome"}
    _resolve_user_agent_id(profile)
    assert "X11; Linux" in profile["userAgent"], (
        "the picker must replace a UA it wrote itself, whatever engine wrote it"
    )
    assert profile["userAgentFromId"] == "linux_chrome", "provenance follows the pick"


def test_the_picker_still_refuses_to_touch_a_pasted_ua(engine_at):
    engine_at(NEW)
    from multiprofile.storage.profiles import _resolve_user_agent_id

    profile = {"id": "p1", "seed": 7, "userAgent": PASTED, "userAgentId": "linux_chrome"}
    _resolve_user_agent_id(profile)
    assert profile["userAgent"] == PASTED


def test_the_api_can_clear_a_user_agent():
    """Otherwise the OS picker has no way to unjam a profile.

    `_resolve_user_agent_id` only resolves a picked id when the UA is empty,
    and the validator required at least one character, so a profile whose UA
    the picker had written could never be moved to another OS through the
    API. Measured: 400 "must be at least 1 character(s)".
    """
    from multiprofile.api.validation import ValidationError, validate_profile_payload

    validate_profile_payload({"userAgent": "", "userAgentId": "linux_chrome"})
    with pytest.raises(ValidationError):
        validate_profile_payload({"userAgent": "x" * 5000})


# --------------------------------------------------------------------------
# 4. Fields whose empty value means "automatic" must be emptiable
# --------------------------------------------------------------------------

def test_the_api_can_clear_the_fields_that_geoauto_is_derived_from():
    """`geoAuto` is server-owned, so clearing is the only way back to auto.

    `_normalize_fields` sets `geoAuto` True when `timezone` or `language` is
    blank and False when the operator picked one, and the validator required
    at least one character on both. So a profile that had ever been given a
    timezone could never return to following its exit -- and the creation
    default is America/New_York. Measured on a real profile: it announced
    America/New_York while egressing from Asia/Kolkata.
    """
    from multiprofile.api.validation import ValidationError, validate_profile_payload

    validate_profile_payload({"timezone": ""})
    validate_profile_payload({"language": ""})
    with pytest.raises(ValidationError):
        validate_profile_payload({"timezone": "x" * 100})


def test_clearing_the_timezone_turns_auto_geo_back_on():
    from multiprofile.storage.profiles import _normalize_fields

    pinned = _normalize_fields({"id": "p1", "timezone": "America/New_York",
                                "language": "en-US"})
    assert pinned["geoAuto"] is False, "an operator's own choice is respected"

    freed = _normalize_fields({"id": "p1", "timezone": "", "language": "en-US",
                               "geoAuto": False})
    assert freed["geoAuto"] is True, "blank means follow the exit"
