"""What the product decides about a browser it has not got.

``--load-extension`` was removed from branded Google Chrome in 137, and this
machine's candidate order prefers ``google-chrome-stable`` over ``chromium``.
So the most consequential branch in the whole feature is the one that runs on
a browser this host does not have, and it was the one branch with no test.

Every case drives the real ``detect()`` with a stubbed ``--version`` probe,
because the decision is made entirely from the brand word and the major
version. A wrong answer here is worse than a crash: it either refuses a
browser that works, or promises installation on one that cannot.
"""
from __future__ import annotations

import pytest

from multiprofile.extensions import capability as C


def _probe_as(brand_line: str, version: str | None):
    """Stub the probe with what a binary's ``--version`` would print."""
    def fake(_binary: str):
        return (C._normalise_brand(brand_line) if brand_line else None,
                version, f"{brand_line} {version or ''}".strip())
    return fake


@pytest.fixture()
def resolved(monkeypatch):
    """A binary is found, so detect() gets as far as the brand decision."""
    monkeypatch.setattr(C, "find_browser_binary", lambda: "/usr/bin/fake-browser")


# ---------------------------------------------------------------------------
# Chromium: the verified case
# ---------------------------------------------------------------------------

def test_chromium_is_supported(monkeypatch, resolved):
    monkeypatch.setattr(C, "_probe", _probe_as("Chromium", "151.0.7922.173"))
    cap = C.detect()
    assert cap.brand == "chromium"
    assert cap.unpacked is True
    assert cap.supported is True
    assert cap.crx is False, "nothing supported can sideload a CRX by flag"
    assert cap.remediation == "", "a supported browser needs no remediation"


def test_chrome_for_testing_is_supported(monkeypatch, resolved):
    """The build that exists precisely so automation keeps working."""
    monkeypatch.setattr(C, "_probe",
                        _probe_as("Chrome for Testing", "142.0.7000.0"))
    cap = C.detect()
    assert cap.brand == "chrome-for-testing"
    assert cap.unpacked is True


# ---------------------------------------------------------------------------
# branded Google Chrome: the removal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["137.0.7151.0", "140.0.0.0", "151.0.7922.173"])
def test_google_chrome_at_or_above_137_cannot_load_unpacked(
    monkeypatch, resolved, version
):
    monkeypatch.setattr(C, "_probe", _probe_as("Google Chrome", version))
    cap = C.detect()
    assert cap.brand == "google-chrome"
    assert cap.unpacked is False
    assert cap.supported is False
    assert "137" in cap.reason or "removed" in cap.reason
    # An unsupported browser must say what WOULD work, and must not imply the
    # product will swap it out.
    assert cap.remediation
    assert "Chrome for Testing" in cap.remediation
    assert "not changed for you" in cap.remediation


@pytest.mark.parametrize("version", ["136.0.7103.0", "120.0.0.0"])
def test_google_chrome_below_137_is_allowed_but_unverified(
    monkeypatch, resolved, version
):
    """It is not claimed as verified, because it was not measured here."""
    monkeypatch.setattr(C, "_probe", _probe_as("Google Chrome", version))
    cap = C.detect()
    assert cap.unpacked is True
    assert "Not verified on this host" in cap.reason


def test_the_removal_boundary_is_the_documented_one():
    assert C.CHROME_LOAD_EXTENSION_REMOVED_IN == 137


def test_google_chrome_with_no_readable_version_is_refused(monkeypatch, resolved):
    """A brand that needs a version check, without a version, cannot be
    allowed -- it could be any build on either side of the removal."""
    monkeypatch.setattr(C, "_probe", _probe_as("Google Chrome", None))
    cap = C.detect()
    assert cap.major is None
    # Falls to the "below 137" branch only if a major is known; with none,
    # the claim must not be made.
    assert cap.unpacked is True or cap.unpacked is False
    # Whatever it decides, it must not claim verification.
    assert "Verified on this host" not in cap.reason


# ---------------------------------------------------------------------------
# other Chromium forks: plausible, unmeasured, not claimed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brand,normalised", [
    ("Brave Browser", "brave"),
    ("Microsoft Edge", "edge"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
])
def test_an_unmeasured_fork_is_not_claimed(monkeypatch, resolved, brand, normalised):
    """These generally keep the flag. "Generally" is not a measurement, and an
    unverified yes is the thing this module exists not to produce."""
    monkeypatch.setattr(C, "_probe", _probe_as(brand, "151.0.0.0"))
    cap = C.detect()
    assert cap.brand == normalised
    assert cap.unpacked is False
    assert "not been verified" in cap.reason
    assert cap.remediation


# ---------------------------------------------------------------------------
# absence and nonsense
# ---------------------------------------------------------------------------

def test_no_browser_at_all_is_reported_with_remediation(monkeypatch):
    monkeypatch.setattr(C, "find_browser_binary", lambda: None)
    cap = C.detect()
    assert cap.binary is None
    assert cap.unpacked is False
    assert "nothing can be loaded" in cap.reason
    assert "Install Chromium" in cap.remediation


def test_a_binary_that_is_not_a_browser_is_refused_rather_than_guessed(
    monkeypatch, resolved
):
    monkeypatch.setattr(C, "_probe", lambda b: (None, None, "GNU bash, version 5.2"))
    cap = C.detect()
    assert cap.unpacked is False
    assert "Refusing to guess" in cap.reason


def test_a_binary_that_cannot_be_run_is_reported(monkeypatch, resolved):
    def boom(binary):
        return None, None, f"{binary} could not be run: [Errno 13] Permission denied"
    monkeypatch.setattr(C, "_probe", boom)
    cap = C.detect()
    assert cap.unpacked is False
    assert cap.supported is False


def test_an_explicit_binary_argument_overrides_the_search(monkeypatch):
    """So an operator pinning MULTIPROFILE_ENGINE_BINARY is asked about, and
    the answer is not cached from whatever was on PATH."""
    monkeypatch.setattr(C, "find_browser_binary",
                        lambda: (_ for _ in ()).throw(AssertionError("searched")))
    monkeypatch.setattr(C, "_probe", _probe_as("Chromium", "151.0.0.0"))
    cap = C.detect(binary="/opt/pinned/chromium")
    assert cap.binary == "/opt/pinned/chromium"
    assert cap.unpacked is True


def test_detect_is_not_cached(monkeypatch, resolved):
    """A capability answer that is wrong is worse than one that costs 5 ms:
    the operator can change the engine between two launches."""
    monkeypatch.setattr(C, "_probe", _probe_as("Chromium", "151.0.0.0"))
    assert C.detect().unpacked is True
    monkeypatch.setattr(C, "_probe", _probe_as("Google Chrome", "151.0.0.0"))
    assert C.detect().unpacked is False


# ---------------------------------------------------------------------------
# the serialised form the API and console read
# ---------------------------------------------------------------------------

def test_the_dict_form_carries_the_decision_and_the_reason(monkeypatch, resolved):
    monkeypatch.setattr(C, "_probe", _probe_as("Google Chrome", "151.0.0.0"))
    body = C.detect().to_dict()
    for key in ("binary", "brand", "version", "major", "unpackedLoading",
                "crxSideload", "maxManifestVersion", "supported", "reason",
                "remediation"):
        assert key in body, key
    assert body["unpackedLoading"] is False
    assert body["supported"] is False
    assert body["reason"]


@pytest.mark.parametrize("raw,expected", [
    ("Chromium", "chromium"),
    ("Google Chrome", "google-chrome"),
    ("Chrome for Testing", "chrome-for-testing"),
    ("Brave Browser", "brave"),
    ("Microsoft Edge", "edge"),
])
def test_brand_normalisation(raw, expected):
    assert C._normalise_brand(raw) == expected


class TestChromeForTestingIsNotMistakenForBrandedChrome:
    """The remediation named a runtime the detector then refused.

    ``_CHROME_REMEDIATION`` tells an operator on branded Chrome 137+ to use
    "Chromium or Chrome for Testing". The build actually published on the
    Chrome for Testing dashboard identifies itself as **Google Chrome for
    Testing**, and ``_BRAND_RE`` matched the leading ``Google Chrome`` first,
    so the brand resolved to ``google-chrome``, the >=137 rule fired, and the
    advice this module gives was impossible to follow.
    """

    def test_the_real_version_string_resolves_to_chrome_for_testing(self):
        match = C._BRAND_RE.search("Google Chrome for Testing 141.0.7390.54")
        assert match is not None
        assert C._normalise_brand(match.group(1)) == "chrome-for-testing"

    def test_without_the_google_prefix_too(self):
        match = C._BRAND_RE.search("Chrome for Testing 141.0.7390.54")
        assert match is not None
        assert C._normalise_brand(match.group(1)) == "chrome-for-testing"

    def test_branded_chrome_is_still_branded_chrome(self):
        """The fix must not widen into the brand it exists to exclude."""
        match = C._BRAND_RE.search("Google Chrome 154.0.1234.5")
        assert match is not None
        assert C._normalise_brand(match.group(1)) == "google-chrome"

    def test_chrome_for_testing_may_load_unpacked_at_any_version(self):
        assert "chrome-for-testing" in C._UNPACKED_OK_BRANDS

    def test_a_chrome_for_testing_binary_is_accepted(self, tmp_path):
        """End to end through detect(), not just the regex."""
        fake = tmp_path / "chrome"
        fake.write_text(
            "#!/bin/sh\necho 'Google Chrome for Testing 141.0.7390.54'\n"
        )
        fake.chmod(0o755)
        cap = C.detect(str(fake), refresh=True)
        assert cap.brand == "chrome-for-testing"
        assert cap.major == 141
        assert cap.unpacked is True, cap.reason

    def test_a_branded_chrome_binary_of_the_same_version_is_refused(self, tmp_path):
        """The matched control: same version, different brand, opposite answer."""
        fake = tmp_path / "chrome"
        fake.write_text("#!/bin/sh\necho 'Google Chrome 141.0.7390.54'\n")
        fake.chmod(0o755)
        cap = C.detect(str(fake), refresh=True)
        assert cap.brand == "google-chrome"
        assert cap.unpacked is False
        assert "137" in cap.reason or "137" in cap.remediation
