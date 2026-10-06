"""Pin, identify, verify, record — the layer a maintained core plugs into.

Phase 4, option A. A maintained Chromium-based core is not primarily a patch;
it is the obligation to run *one known build* and to keep knowing which build
that is. Before this, the product ran whatever `find_browser_binary()` turned
up on `PATH`, which meant the identity it presented was decided by whoever
last updated the host's package manager, and a profile created under 151 and
relaunched under 153 presented a different browser behind the same stored
seed with nothing recording it.

So the slice under test is the mechanism, not the core: naming a build,
identifying it, judging it against a declared policy, and recording which one
ran. The day a managed build exists, `engine_binary` names it and everything
here already enforces it.

The tests use a **synthetic engine** -- a script that prints a version string
-- so the policy can be driven to every verdict without installing five
browsers. That is deliberate: the thing being tested is the policy, and a
test that can only assert "this host's Chromium is fine" asserts almost
nothing.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.delenv("MULTIPROFILE_ENGINE_BINARY", raising=False)
    monkeypatch.delenv("MULTIPROFILE_ENGINE_REQUIRE_PINNED", raising=False)
    # `get_settings()` reads the environment on every call, so there is no
    # settings cache to reset -- only the engine caches.
    from multiprofile import engine, engine_policy

    engine.reset_engine_version_cache()
    engine_policy.reset_cache()
    yield tmp_path
    engine.reset_engine_version_cache()
    engine_policy.reset_cache()


def _fake_engine(tmp_path: Path, version: str, *, name: str = "fake-chrome",
                 age_days: float = 0.0) -> Path:
    """A binary that answers --version, and nothing else."""
    path = tmp_path / name
    path.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo "Chromium {version}"; fi\n'
    )
    path.chmod(0o755)
    if age_days:
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
    return path


# --------------------------------------------------------------------------
# 1. Identify
# --------------------------------------------------------------------------

def test_the_host_engine_is_identified(tmp_path):
    """On this machine, with no pin, the system engine is found and named."""
    from multiprofile.browser.launcher import find_chrome
    from multiprofile.engine_policy import ORIGIN_SYSTEM, identify_engine

    try:
        find_chrome()
    except Exception:
        pytest.skip("no browser on this host")
    record = identify_engine()
    assert record.binary, record.to_dict()
    assert record.origin == ORIGIN_SYSTEM
    assert record.version_major, record.to_dict()
    assert record.digest, "the build was not identified by content"


def test_a_pinned_binary_is_preferred_over_the_host(tmp_path, monkeypatch):
    """The whole of 'use our maintained core' is naming it here."""
    from multiprofile.engine_policy import ORIGIN_PINNED, identify_engine, reset_cache

    fake = _fake_engine(tmp_path, "151.0.7922.173")
    monkeypatch.setenv("MULTIPROFILE_ENGINE_BINARY", str(fake))
    reset_cache()

    record = identify_engine(refresh=True)
    assert record.binary == str(fake)
    assert record.origin == ORIGIN_PINNED
    assert record.version_major == 151


def test_two_builds_of_one_version_are_distinguishable(tmp_path):
    """A version string is not an identity: a rebuild reuses it. The digest
    is what makes 'the same engine' checkable."""
    from multiprofile.engine_policy import identify_engine

    first = _fake_engine(tmp_path, "151.0.7922.173", name="a")
    second = tmp_path / "b"
    second.write_text(first.read_text() + "# a different build\n")
    second.chmod(0o755)

    a = identify_engine(str(first), refresh=True)
    b = identify_engine(str(second), refresh=True)
    assert a.version_full == b.version_full
    assert a.digest != b.digest, "two different builds hashed the same"


def test_a_missing_binary_is_reported_not_guessed(tmp_path, monkeypatch):
    from multiprofile.engine_policy import identify_engine, reset_cache

    monkeypatch.setenv("MULTIPROFILE_ENGINE_BINARY", str(tmp_path / "nope"))
    reset_cache()
    record = identify_engine(refresh=True)
    assert record.version_major is None
    assert "does not exist" in record.detail, record.detail


# --------------------------------------------------------------------------
# 2. Verify against the declared policy
# --------------------------------------------------------------------------

def test_a_supported_version_passes(tmp_path):
    from multiprofile.engine_policy import (
        SUPPORTED_MAJOR_MIN,
        VERDICT_SUPPORTED,
        identify_engine,
        verify_engine,
    )

    fake = _fake_engine(tmp_path, f"{SUPPORTED_MAJOR_MIN}.0.1.2")
    verdict = verify_engine(identify_engine(str(fake), refresh=True))
    assert verdict.status == VERDICT_SUPPORTED, verdict.to_dict()
    assert verdict.ok


def test_a_version_below_the_minimum_is_unsupported_and_says_why(tmp_path):
    """The minimum is chosen from measurement, so the reason names the
    measurements: WebGL on a GPU-less host (F46) and ClientHello extension
    order randomisation."""
    from multiprofile.engine_policy import (
        SUPPORTED_MAJOR_MIN,
        VERDICT_UNSUPPORTED,
        identify_engine,
        verify_engine,
    )

    fake = _fake_engine(tmp_path, f"{SUPPORTED_MAJOR_MIN - 1}.0.1.2")
    verdict = verify_engine(identify_engine(str(fake), refresh=True))
    assert verdict.status == VERDICT_UNSUPPORTED, verdict.to_dict()
    joined = " ".join(verdict.reasons).lower()
    assert "webgl" in joined and "clienthello" in joined, verdict.reasons


def test_a_stale_build_is_unverified_rather_than_supported(tmp_path):
    """Phase 5's security-update process, as a verdict. Not 'unsupported':
    the build is in range, it is just old enough that the operator should be
    deciding about it rather than discovering it."""
    from multiprofile.engine_policy import (
        STALE_AFTER_DAYS,
        VERDICT_UNVERIFIED,
        identify_engine,
        verify_engine,
    )

    fake = _fake_engine(tmp_path, "151.0.1.2", age_days=STALE_AFTER_DAYS + 10)
    verdict = verify_engine(identify_engine(str(fake), refresh=True))
    assert verdict.status == VERDICT_UNVERIFIED, verdict.to_dict()
    assert "days old" in " ".join(verdict.reasons), verdict.reasons


def test_a_fresh_build_in_range_is_not_called_stale(tmp_path):
    """The other direction, so the staleness rule cannot drift into
    flagging everything."""
    from multiprofile.engine_policy import (
        VERDICT_SUPPORTED,
        identify_engine,
        verify_engine,
    )

    fake = _fake_engine(tmp_path, "151.0.1.2", age_days=1)
    assert verify_engine(
        identify_engine(str(fake), refresh=True)).status == VERDICT_SUPPORTED


def test_an_engine_that_will_not_say_its_version_is_unverified(tmp_path):
    """Not 'unsupported'. An unknown version is an unknown, and calling it a
    failure would be the F31 mistake -- a plausible wrong answer is worse
    than an explicit unknown."""
    from multiprofile.engine_policy import (
        VERDICT_UNVERIFIED,
        identify_engine,
        verify_engine,
    )

    silent = tmp_path / "silent"
    silent.write_text("#!/bin/sh\nexit 0\n")
    silent.chmod(0o755)
    verdict = verify_engine(identify_engine(str(silent), refresh=True))
    assert verdict.status == VERDICT_UNVERIFIED, verdict.to_dict()


# --------------------------------------------------------------------------
# 3. The pin is enforceable
# --------------------------------------------------------------------------

def test_requiring_a_pin_rejects_the_host_engine(tmp_path, monkeypatch):
    """What a maintained core needs: a way to say 'only our build'."""
    from multiprofile.engine_policy import (
        VERDICT_UNSUPPORTED,
        identify_engine,
        reset_cache,
        verify_engine,
    )

    monkeypatch.setenv("MULTIPROFILE_ENGINE_REQUIRE_PINNED", "1")
    reset_cache()
    host = _fake_engine(tmp_path, "151.0.1.2", name="host-chrome")
    verdict = verify_engine(identify_engine(str(host), refresh=True))
    assert verdict.status == VERDICT_UNSUPPORTED, verdict.to_dict()
    assert verdict.pin_violated is True
    assert "engine_require_pinned" in " ".join(verdict.reasons)


def test_requiring_a_pin_accepts_the_pinned_build(tmp_path, monkeypatch):
    from multiprofile.engine_policy import (
        VERDICT_SUPPORTED,
        identify_engine,
        reset_cache,
        verify_engine,
    )

    managed = _fake_engine(tmp_path, "151.0.1.2", name="managed-core")
    monkeypatch.setenv("MULTIPROFILE_ENGINE_BINARY", str(managed))
    monkeypatch.setenv("MULTIPROFILE_ENGINE_REQUIRE_PINNED", "1")
    reset_cache()
    verdict = verify_engine(identify_engine(refresh=True))
    assert verdict.status == VERDICT_SUPPORTED, verdict.to_dict()
    assert verdict.pin_violated is False


def test_the_default_does_not_require_a_pin(tmp_path):
    """A product that cannot launch because a pin is unset is worse than one
    that tells the operator which engine it used."""
    from multiprofile.config import get_settings

    assert get_settings().engine_require_pinned is False
    assert get_settings().engine_binary == ""


# --------------------------------------------------------------------------
# 4. The operator can see it
# --------------------------------------------------------------------------

def test_health_reports_the_engine_verdict():
    from multiprofile.api.server import create_app

    body = create_app().test_client().get("/api/health").get_json()
    block = body.get("enginePolicy") or {}
    assert block.get("status"), block
    assert block.get("policy", {}).get("supportedMajorMin"), block
    assert "engine" in block, block


def test_a_launch_records_which_build_ran(tmp_path, monkeypatch):
    """A profile's identity is only reproducible against the engine that
    produced it, so the note is the record."""
    from multiprofile.browser.launcher import _note_engine_policy

    notes: list[str] = []
    _note_engine_policy(notes, "p1")
    assert notes, "a launch recorded nothing about its engine"
    assert notes[0].startswith("Engine: "), notes


def test_a_pin_violation_is_a_fault_not_a_note(tmp_path, monkeypatch):
    """The operator asked for one build and got another. That is a broken
    expectation, not a choice, so it reaches /api/health as a fault."""
    from multiprofile.browser import identity_faults
    from multiprofile.browser.launcher import _note_engine_policy
    from multiprofile.engine_policy import reset_cache

    monkeypatch.setenv("MULTIPROFILE_ENGINE_REQUIRE_PINNED", "1")
    reset_cache()
    identity_faults.reset()
    notes: list[str] = []
    _note_engine_policy(notes, "p-pin")
    codes = [f.code for f in identity_faults.faults_for("p-pin")]
    assert "engine_pin_violated" in codes, (codes, notes)
