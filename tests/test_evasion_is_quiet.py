"""evasion.js must not announce itself on the pages it protects.

It used to, unconditionally, on every page and in every frame::

    console.log("[Evasion] evasion.js active on:", location.href);

A page cannot read past console output, so this was never directly
exploitable. It was still a plain-text confession: visible in any DevTools
session, in a screen share, in a demo, in a support screenshot, and
harvestable by a page that installs a ``console`` proxy before the script runs
(which the extension route made possible, and which is why the file's own
comment already said "No console.log — logging extension activity is a
detection signal" twelve lines below the console.log).

The line survives behind a flag rather than being deleted, because the number
of times it appears for one document is the only honest way to count how many
times the script ran — the measurement that catches double injection. The flag
is off unless ``MULTIPROFILE_EVASION_DEBUG`` is set, and the live test in
``test_evasion_single_hook.py`` is the one thing that turns it on.
"""
from __future__ import annotations

import re
from pathlib import Path

EVASION = Path(__file__).resolve().parent.parent / "src" / "extension" / "evasion.js"


def _code() -> str:
    """The script with // and /* */ comments stripped.

    The explanation above the guard quotes the old line verbatim, so a naive
    substring search would find the confession in the comment that documents
    its removal.
    """
    source = EVASION.read_text(encoding="utf-8")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


def _console_calls() -> list[str]:
    return re.findall(r"console\.\w+\s*\(", _code())


def test_there_is_exactly_one_console_call_left():
    calls = _console_calls()
    assert len(calls) == 1, (
        f"evasion.js makes {len(calls)} console calls; each one is a plain-text "
        f"confession in any DevTools session or screen share"
    )


def test_that_call_is_behind_the_debug_flag():
    code = _code()
    guard = "if (MP_CFG && MP_CFG.debug === true) {"
    assert guard in code, "the console.log is no longer gated"
    # The only console call must sit inside the guard, not before it.
    assert code.index(guard) < code.index("console.log(")


def test_the_log_runs_after_the_config_is_parsed():
    """Gating on MP_CFG before MP_CFG exists would gate on undefined — silent
    by accident rather than by design, and impossible to turn on."""
    code = _code()
    assert code.index("let MP_CFG = null;") < code.index("MP_CFG.debug === true")


def test_debug_is_off_by_default():
    from multiprofile.config import get_settings

    assert get_settings().evasion_debug is False


def test_debug_is_opt_in_by_environment(monkeypatch):
    from multiprofile.config import get_settings

    monkeypatch.setenv("MULTIPROFILE_EVASION_DEBUG", "1")
    assert get_settings().evasion_debug is True


def test_the_injected_config_carries_no_debug_key_by_default(monkeypatch, tmp_path):
    """Off means the page config gains nothing at all, not debug:false."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_EVASION_DEBUG", raising=False)

    import multiprofile.browser.launcher as L

    captured: dict = {}

    def _capture(profile_id, port, **kw):
        captured.update(kw)
        from multiprofile.browser.cdp_inject import InjectionStatus

        return InjectionStatus(ok=True, script_installed=True)

    monkeypatch.setattr("multiprofile.browser.cdp_inject.start_injection", _capture)
    L._start_identity_injection(
        "p1", 9222, {"id": "p1", "seed": 3}, {"seed": 3, "platform": "Win32"}, None
    )

    assert '"debug"' not in (captured.get("script") or "")


def test_the_injected_config_carries_debug_when_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_EVASION_DEBUG", "1")

    import multiprofile.browser.launcher as L

    captured: dict = {}

    def _capture(profile_id, port, **kw):
        captured.update(kw)
        from multiprofile.browser.cdp_inject import InjectionStatus

        return InjectionStatus(ok=True, script_installed=True)

    monkeypatch.setattr("multiprofile.browser.cdp_inject.start_injection", _capture)
    notes = L._start_identity_injection(
        "p1", 9222, {"id": "p1", "seed": 3}, {"seed": 3, "platform": "Win32"}, None
    )

    assert '"debug":true' in (captured.get("script") or "")
    assert any("DEBUG logging is ON" in n for n in notes)
