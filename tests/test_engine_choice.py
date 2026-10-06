"""Which browser launches, and whether installing another can change it.

Before this module the install had three answers to "which browser?" and they
did not agree. Measured on this host with
``MULTIPROFILE_ENGINE_BINARY=/usr/bin/true``::

    launcher find_chrome()         -> /usr/bin/chromium   (what actually ran)
    engine_policy.identify_engine  -> /usr/bin/true       (what was reported)

``find_chrome()`` walked PATH and had never looked at the configured engine,
while its own comment claimed the two were the same binary. The consequence
was not cosmetic: ``BROWSER_BINARIES`` puts ``google-chrome-stable`` ahead of
``chromium``, and branded Chrome 137+ cannot load unpacked extensions, so
installing Chrome moved every profile onto a runtime where the extension
manager stops working -- silently, and with a pin unable to prevent it.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from multiprofile import engine_choice as EC
from multiprofile.extensions.capability import BrowserCapability


def _cap(brand: str, version: str, unpacked: bool) -> BrowserCapability:
    return BrowserCapability(
        binary="/x", brand=brand, version=version,
        major=int(version.split(".")[0]), unpacked=unpacked, crx=False,
        reason=f"{brand} {version}",
        remediation="" if unpacked else "Use Chromium or Chrome for Testing.",
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("MULTIPROFILE_ENGINE_BINARY", raising=False)
    yield


def _fake_binary(tmp_path, name="chromium") -> str:
    p = tmp_path / name
    p.write_text("#!/bin/sh\necho fake\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _stub(monkeypatch, *, path_answer: str | None, caps: dict[str, BrowserCapability]):
    """Stub the PATH search and the per-binary capability probe."""
    monkeypatch.setattr(EC, "find_browser_binary", lambda: path_answer,
                        raising=False)
    import multiprofile.engine as E

    monkeypatch.setattr(E, "find_browser_binary", lambda: path_answer)
    import multiprofile.extensions.capability as CAP

    monkeypatch.setattr(
        CAP, "detect",
        lambda binary=None: caps.get(binary, _cap("unknown", "0.0.0.0", False)))


# ---------------------------------------------------------------------------
# resolution order
# ---------------------------------------------------------------------------

def test_the_path_search_is_used_when_nothing_is_pinned(tmp_path, monkeypatch):
    chromium = _fake_binary(tmp_path)
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True)})
    got = EC.resolve()
    assert got.binary == chromium
    assert got.origin == EC.ORIGIN_PATH
    assert got.pinned is False
    assert got.unpacked is True
    # And it says the runtime is not protected, which is the actionable part.
    assert any("No engine is pinned" in n for n in got.notes), got.notes


def test_the_environment_pin_wins(tmp_path, monkeypatch):
    chromium = _fake_binary(tmp_path, "chromium")
    pinned = _fake_binary(tmp_path, "pinned-browser")
    monkeypatch.setenv("MULTIPROFILE_ENGINE_BINARY", pinned)
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True),
                pinned: _cap("chromium", "150.0.0.0", True)})
    got = EC.resolve()
    assert got.binary == pinned
    assert got.origin == EC.ORIGIN_ENV
    assert got.pinned is True


def test_a_persisted_choice_beats_the_path_search(tmp_path, monkeypatch):
    """The whole point: an apt install must not change the runtime."""
    chromium = _fake_binary(tmp_path, "chromium")
    chrome = _fake_binary(tmp_path, "google-chrome-stable")
    _stub(monkeypatch, path_answer=chrome,
          caps={chromium: _cap("chromium", "151.0.0.0", True),
                chrome: _cap("google-chrome", "151.0.0.0", False)})
    EC.set_choice(chromium)
    got = EC.resolve()
    assert got.binary == chromium
    assert got.origin == EC.ORIGIN_PERSISTED
    assert got.unpacked is True, "the pin did not survive Chrome appearing on PATH"


def test_the_environment_pin_beats_the_persisted_one(tmp_path, monkeypatch):
    a = _fake_binary(tmp_path, "a")
    b = _fake_binary(tmp_path, "b")
    _stub(monkeypatch, path_answer=None,
          caps={a: _cap("chromium", "151.0.0.0", True),
                b: _cap("chromium", "150.0.0.0", True)})
    EC.set_choice(a)
    monkeypatch.setenv("MULTIPROFILE_ENGINE_BINARY", b)
    got = EC.resolve()
    assert got.binary == b
    assert got.origin == EC.ORIGIN_ENV


def test_clearing_the_choice_returns_to_the_path_search(tmp_path, monkeypatch):
    chromium = _fake_binary(tmp_path, "chromium")
    other = _fake_binary(tmp_path, "other")
    _stub(monkeypatch, path_answer=other,
          caps={chromium: _cap("chromium", "151.0.0.0", True),
                other: _cap("chromium", "152.0.0.0", True)})
    EC.set_choice(chromium)
    assert EC.resolve().binary == chromium
    EC.clear_choice()
    assert EC.resolve().binary == other


# ---------------------------------------------------------------------------
# the choice file
# ---------------------------------------------------------------------------

def test_a_choice_must_exist_and_be_executable(tmp_path, monkeypatch):
    _stub(monkeypatch, path_answer=None, caps={})
    with pytest.raises(FileNotFoundError):
        EC.set_choice(str(tmp_path / "nope"))
    plain = tmp_path / "not-executable"
    plain.write_text("x")
    with pytest.raises(PermissionError):
        EC.set_choice(str(plain))


def test_the_choice_file_is_owner_only(tmp_path, monkeypatch):
    """It names a path on this machine; it goes through the same writer as
    every other operator file."""
    chromium = _fake_binary(tmp_path)
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True)})
    EC.set_choice(chromium)
    mode = stat.S_IMODE(os.stat(EC.choice_path()).st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, oct(mode)


def test_an_unreadable_choice_file_is_ignored_not_fatal(tmp_path, monkeypatch):
    chromium = _fake_binary(tmp_path)
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True)})
    EC.choice_path().parent.mkdir(parents=True, exist_ok=True)
    EC.choice_path().write_text("{not json")
    got = EC.resolve()
    assert got.binary == chromium, "a corrupt choice file broke resolution"


def test_a_pinned_binary_that_vanished_is_reported_not_substituted(
    tmp_path, monkeypatch
):
    """Silently falling back to another browser is the behaviour this module
    exists to prevent."""
    chromium = _fake_binary(tmp_path, "chromium")
    gone = _fake_binary(tmp_path, "gone")
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True)})
    EC.set_choice(gone)
    os.unlink(gone)
    got = EC.resolve()
    assert got.binary == gone
    assert got.unpacked is False
    assert "does not exist" in got.reason
    assert got.binary != chromium, "it substituted another browser"


# ---------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------

def test_a_changed_path_answer_is_reported_as_drift(tmp_path, monkeypatch):
    chromium = _fake_binary(tmp_path, "chromium")
    chrome = _fake_binary(tmp_path, "google-chrome-stable")
    caps = {chromium: _cap("chromium", "151.0.0.0", True),
            chrome: _cap("google-chrome", "151.0.0.0", False)}
    _stub(monkeypatch, path_answer=chromium, caps=caps)
    EC.record_observed(chromium)
    # Now Chrome is installed and wins the PATH search.
    _stub(monkeypatch, path_answer=chrome, caps=caps)
    got = EC.resolve()
    assert got.drifted_from == chromium
    assert got.drift_breaks_extensions is True
    assert any("runtime changed" in n for n in got.notes)


def test_drift_to_an_equally_capable_runtime_is_not_flagged_as_breaking(
    tmp_path, monkeypatch
):
    a = _fake_binary(tmp_path, "chromium")
    b = _fake_binary(tmp_path, "chromium-next")
    caps = {a: _cap("chromium", "151.0.0.0", True),
            b: _cap("chromium", "152.0.0.0", True)}
    _stub(monkeypatch, path_answer=a, caps=caps)
    EC.record_observed(a)
    _stub(monkeypatch, path_answer=b, caps=caps)
    got = EC.resolve()
    assert got.drifted_from == a
    assert got.drift_breaks_extensions is False


def test_a_pin_is_not_drift(tmp_path, monkeypatch):
    """A pin is the instruction to ignore PATH, so PATH disagreeing with it is
    the expected state rather than a warning."""
    chromium = _fake_binary(tmp_path, "chromium")
    chrome = _fake_binary(tmp_path, "google-chrome-stable")
    caps = {chromium: _cap("chromium", "151.0.0.0", True),
            chrome: _cap("google-chrome", "151.0.0.0", False)}
    _stub(monkeypatch, path_answer=chrome, caps=caps)
    EC.record_observed(chrome)
    EC.set_choice(chromium)
    got = EC.resolve()
    assert got.drifted_from is None
    assert got.drift_breaks_extensions is False


# ---------------------------------------------------------------------------
# the launch gate
# ---------------------------------------------------------------------------

def test_a_profile_without_extensions_is_never_blocked(tmp_path, monkeypatch):
    """This feature must not be able to stop an ordinary profile launching."""
    chrome = _fake_binary(tmp_path, "google-chrome-stable")
    _stub(monkeypatch, path_answer=chrome,
          caps={chrome: _cap("google-chrome", "151.0.0.0", False)})
    assert EC.extension_runtime_block(False) is None


def test_extensions_on_an_incapable_runtime_are_refused_with_remediation(
    tmp_path, monkeypatch
):
    chrome = _fake_binary(tmp_path, "google-chrome-stable")
    _stub(monkeypatch, path_answer=chrome,
          caps={chrome: _cap("google-chrome", "151.0.0.0", False)})
    message = EC.extension_runtime_block(True)
    assert message
    assert "cannot load them" in message
    assert "Chrome for Testing" in message
    assert chrome in message
    assert "No other browser is substituted" in message


def test_extensions_on_a_capable_runtime_are_allowed(tmp_path, monkeypatch):
    chromium = _fake_binary(tmp_path, "chromium")
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True)})
    assert EC.extension_runtime_block(True) is None


def test_a_breaking_drift_blocks_a_launch_with_extensions(tmp_path, monkeypatch):
    """The scenario in this module's docstring, as a refusal."""
    chromium = _fake_binary(tmp_path, "chromium")
    chrome = _fake_binary(tmp_path, "google-chrome-stable")
    caps = {chromium: _cap("chromium", "151.0.0.0", True),
            chrome: _cap("google-chrome", "151.0.0.0", False)}
    _stub(monkeypatch, path_answer=chromium, caps=caps)
    EC.record_observed(chromium)
    _stub(monkeypatch, path_answer=chrome, caps=caps)
    message = EC.extension_runtime_block(True)
    assert message
    assert "silently stop working" in message
    assert "Nothing has been switched for you" in message


def test_no_browser_at_all_blocks_only_profiles_with_extensions(monkeypatch):
    _stub(monkeypatch, path_answer=None, caps={})
    assert EC.extension_runtime_block(False) is None
    assert EC.extension_runtime_block(True)


# ---------------------------------------------------------------------------
# the serialised form
# ---------------------------------------------------------------------------

def test_the_dict_form_names_the_file_so_an_operator_can_find_it(
    tmp_path, monkeypatch
):
    chromium = _fake_binary(tmp_path)
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True)})
    body = EC.resolve().to_dict()
    for key in ("binary", "origin", "pinned", "brand", "version",
                "unpackedLoading", "reason", "remediation", "driftedFrom",
                "driftBreaksExtensions", "choiceFile"):
        assert key in body, key
    assert body["choiceFile"].endswith("engine.json")


def test_recording_the_observed_runtime_is_idempotent(tmp_path, monkeypatch):
    chromium = _fake_binary(tmp_path)
    _stub(monkeypatch, path_answer=chromium,
          caps={chromium: _cap("chromium", "151.0.0.0", True)})
    EC.record_observed(chromium)
    EC.record_observed(chromium)
    data = json.loads(EC.choice_path().read_text())
    assert data["observed"] == chromium
