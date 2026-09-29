"""The consistency score, and what it deliberately refuses to say.

The engine already reported PASS/WARNING/ERROR per domain. The score exists so
the console can rank profiles and an operator can see one getting worse without
reading six messages.

It is **not** a claim about being undetected. It says how coherent the signals
we can see are, and nothing about surfaces nobody has checked. Two rules keep
that honest, and both are asserted here:

* a domain that could not be evaluated is excluded from the denominator, not
  scored as a pass or a fail -- otherwise a profile with nothing configured
  outranks one with a real setup;
* any ERROR caps the total, because one flat contradiction is not something
  four passes elsewhere should average away.
"""
from __future__ import annotations

from multiprofile.consistency import (
    ERROR,
    PASS,
    WARNING,
    ProfileConsistencyEngine,
    score_checks,
)
from multiprofile.fingerprint import build_fingerprint


def _c(domain, status, message="ok"):
    return {"domain": domain, "status": status, "message": message}


def _insufficient(domain):
    return {"domain": domain, "status": WARNING,
            "message": "insufficient data: nothing to compare"}


# --------------------------------------------------------------------------
# 1. The two honesty rules
# --------------------------------------------------------------------------

def test_a_domain_with_nothing_to_compare_is_excluded():
    """Not a pass and not a fail. A profile with no proxy must not be scored
    as if its geolocation had been verified."""
    full = score_checks([_c("geo", PASS), _c("platform", PASS)])
    partial = score_checks([_insufficient("geo"), _c("platform", PASS)])
    assert full["score"] == 100
    assert partial["score"] == 100
    assert partial["skipped"] == ["geo"]
    assert "geo" not in partial["evaluated"]


def test_an_unconfigured_profile_cannot_outrank_a_configured_one():
    """The failure mode the exclusion rule exists to prevent."""
    nothing_set = score_checks([_insufficient(d) for d in
                                ("geo", "platform", "device", "browser")])
    real_setup = score_checks([_c("geo", PASS), _c("platform", PASS),
                               _c("device", PASS), _c("browser", WARNING)])
    assert nothing_set["score"] is None
    assert real_setup["score"] is not None


def test_one_contradiction_caps_the_score():
    """Four passes must not average away a flat contradiction."""
    scored = score_checks([
        _c("geo", PASS), _c("platform", PASS), _c("device", PASS),
        _c("browser", PASS), _c("webgpu", ERROR, "two different graphics cards"),
    ])
    assert scored["score"] <= 40, scored


def test_a_warning_costs_less_than_an_error():
    warned = score_checks([_c("geo", WARNING), _c("platform", PASS)])
    errored = score_checks([_c("geo", ERROR), _c("platform", PASS)])
    assert warned["score"] > errored["score"]


def test_a_warning_still_costs_something():
    assert score_checks([_c("geo", WARNING)])["score"] < 100


def test_nothing_evaluable_scores_none_rather_than_a_number():
    """100 would be a lie and 0 would be a different one."""
    scored = score_checks([_insufficient("geo")])
    assert scored["score"] is None
    assert scored["reason"] == "nothing could be evaluated"


def test_the_penalised_domains_are_named():
    """A low score must be traceable to a check, not appear as a bare
    number."""
    scored = score_checks([_c("geo", ERROR), _c("platform", PASS)])
    assert scored["penalised"] == ["geo"]


def test_geo_and_platform_outweigh_the_rest():
    """They are the contradictions a site checks first and the hardest to
    explain away.

    Compared with WARNING rather than ERROR on purpose: an ERROR anywhere
    caps the total, so two capped scores would both read 40 and say nothing
    about the weights.
    """
    geo_bad = score_checks([_c("geo", WARNING), _c("platform", PASS),
                            _c("gpu_webgl", PASS)])
    gpu_bad = score_checks([_c("geo", PASS), _c("platform", PASS),
                            _c("gpu_webgl", WARNING)])
    assert geo_bad["score"] < gpu_bad["score"]


def test_the_cap_applies_whichever_domain_contradicts():
    """The other half of the same behaviour: once something is an ERROR, the
    weighting stops mattering."""
    for domain in ("geo", "gpu_webgl"):
        scored = score_checks([_c(domain, ERROR), _c("platform", PASS),
                               _c("device", PASS)])
        assert scored["score"] <= 40, (domain, scored)


def test_the_score_is_deterministic():
    checks = [_c("geo", PASS), _c("platform", WARNING), _c("device", ERROR)]
    assert score_checks(checks) == score_checks(checks)


# --------------------------------------------------------------------------
# 2. The WebGPU domain
# --------------------------------------------------------------------------

def _fp_with(renderer, webgpu):
    fp = build_fingerprint({"id": "p1"})
    fp["gpu"] = {"vendor": "Google Inc. (NVIDIA)", "renderer": renderer,
                 "webgpu": webgpu}
    return fp


def test_a_matching_adapter_passes():
    engine = ProfileConsistencyEngine()
    fp = _fp_with("ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0, D3D11)",
                  {"vendor": "nvidia", "architecture": "ada"})
    check = next(c for c in engine.check({"id": "p"}, fingerprint_expected=fp)["checks"]
                 if c["domain"] == "webgpu")
    assert check["status"] == PASS, check


def test_two_different_graphics_cards_in_one_page_is_an_error():
    """The F26 contradiction, caught before a launch rather than after."""
    engine = ProfileConsistencyEngine()
    fp = _fp_with("ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0, D3D11)",
                  {"vendor": "intel", "architecture": "gen-12lp"})
    report = engine.check({"id": "p"}, fingerprint_expected=fp)
    check = next(c for c in report["checks"] if c["domain"] == "webgpu")
    assert check["status"] == ERROR, check
    assert report["score"] <= 40, report["score"]


def test_a_missing_adapter_identity_is_flagged():
    """Claiming a card in WebGL with no WebGPU identity is how the two
    surfaces end up disagreeing in the page."""
    engine = ProfileConsistencyEngine()
    fp = _fp_with("ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0, D3D11)", {})
    check = next(c for c in engine.check({"id": "p"}, fingerprint_expected=fp)["checks"]
                 if c["domain"] == "webgpu")
    assert check["status"] == WARNING, check
    assert "insufficient data" not in check["message"]


def test_no_claimed_renderer_is_insufficient_not_a_failure():
    engine = ProfileConsistencyEngine()
    fp = build_fingerprint({"id": "p1"})
    fp["gpu"] = {}
    check = next(c for c in engine.check({"id": "p"}, fingerprint_expected=fp)["checks"]
                 if c["domain"] == "webgpu")
    assert check["insufficient"] is True


# --------------------------------------------------------------------------
# 3. The report carries it
# --------------------------------------------------------------------------

def test_a_real_profile_gets_a_score_and_an_explanation():
    engine = ProfileConsistencyEngine()
    fp = build_fingerprint({"id": "p1"})
    report = engine.check(
        {"id": "p1", "timezone": "America/New_York", "language": "en-US"},
        fingerprint_expected=fp,
    )
    assert isinstance(report["score"], int)
    assert 0 <= report["score"] <= 100
    detail = report["score_detail"]
    assert set(detail) == {"score", "evaluated", "skipped", "penalised", "reason"}
    assert detail["evaluated"], detail


def test_the_existing_status_is_unchanged():
    """The score is additive; nothing that read `status` should move."""
    engine = ProfileConsistencyEngine()
    report = engine.check({"id": "p1"}, fingerprint_expected=build_fingerprint({"id": "p1"}))
    assert report["status"] in (PASS, WARNING, ERROR)
    assert "checks" in report
