"""Which browser executable this install uses, decided once and remembered.

Before this module there were three answers to "which browser?" and they did
not agree. Measured on this host with ``MULTIPROFILE_ENGINE_BINARY=/usr/bin/true``:

    launcher find_chrome()   -> /usr/bin/chromium      <- what actually ran
    engine_policy pinned     -> /usr/bin/true          <- what the doctor said

``find_chrome()`` called ``find_browser_binary()``, which walks
``BROWSER_BINARIES`` over ``PATH`` and has never looked at the configured
engine. Its own comment said *"the binary we launch and the binary we ask for
a version are the same one"*, and ``docs/09_ENGINE_PROGRAM.md`` said naming a
build in ``engine_binary`` was "the whole of" using a maintained core. Neither
was true: the pin reached the policy, the doctor and the capability detector,
and not the process that starts browsers.

That matters more than a cosmetic inconsistency because ``BROWSER_BINARIES``
puts ``google-chrome-stable`` ahead of ``chromium``, and **branded Google
Chrome 137+ cannot load unpacked extensions at all**. Installing Chrome on a
working Chromium install would therefore have moved every profile onto a
runtime where the extension manager silently stops working, with nothing said.

Resolution order, highest first:

1. ``MULTIPROFILE_ENGINE_BINARY`` -- an operator pinning per process.
2. The **persisted choice** in ``engine.json``, set explicitly through the
   API. This is what makes a verified setup survive installing another
   browser.
3. The ``PATH`` search, as before. The binary that resolves this way is
   recorded as *observed* so that a later change of answer is **detectable**
   rather than silent.

Nothing here substitutes a browser for another. When the resolved one cannot
do what is being asked, the caller is told which binary, which brand, which
version, and what would work.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .storage.xdg import config_dir, secure_write_json

log = logging.getLogger("multiprofile.engine_choice")

ORIGIN_ENV = "env"
ORIGIN_PERSISTED = "persisted"
ORIGIN_PATH = "path"
ORIGIN_MISSING = "missing"

_ENV_VAR = "MULTIPROFILE_ENGINE_BINARY"


def choice_path() -> Path:
    """Where the explicit choice and the observed runtime are kept."""
    # config_dir() already ends in the application segment.
    return config_dir() / "engine.json"


def _read() -> dict[str, Any]:
    path = choice_path()
    try:
        if not path.is_file():
            return {}
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("engine choice file unreadable (%s); ignoring it", e)
        return {}
    return got if isinstance(got, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = choice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_write_json(path, data)


@dataclass
class EngineChoice:
    """The resolved runtime, where the answer came from, and what it can do."""

    binary: str | None
    origin: str
    brand: str | None = None
    version: str | None = None
    major: int | None = None
    unpacked: bool = False
    reason: str = ""
    remediation: str = ""
    #: Set when the PATH search now answers differently than last time.
    drifted_from: str | None = None
    #: True when the drift moves to a runtime that cannot load extensions.
    drift_breaks_extensions: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def pinned(self) -> bool:
        return self.origin in (ORIGIN_ENV, ORIGIN_PERSISTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary": self.binary,
            "origin": self.origin,
            "pinned": self.pinned,
            "brand": self.brand,
            "version": self.version,
            "major": self.major,
            "unpackedLoading": self.unpacked,
            "reason": self.reason,
            "remediation": self.remediation,
            "driftedFrom": self.drifted_from,
            "driftBreaksExtensions": self.drift_breaks_extensions,
            "notes": list(self.notes),
            "choiceFile": str(choice_path()),
        }


def get_persisted() -> str | None:
    value = str(_read().get("binary") or "").strip()
    return value or None


def get_observed() -> str | None:
    value = str(_read().get("observed") or "").strip()
    return value or None


def set_choice(binary: str) -> EngineChoice:
    """Pin a browser explicitly. Refuses a path that is not executable.

    Deliberately does not validate that the browser *supports* extensions:
    an operator may well want to pin a runtime that cannot, and being told
    about it is more useful than being prevented.
    """
    path = Path(binary).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{path} is not a file")
    if not os.access(path, os.X_OK):
        raise PermissionError(f"{path} is not executable")
    data = _read()
    data["binary"] = str(path)
    _write(data)
    return resolve(refresh=True)


def clear_choice() -> EngineChoice:
    """Stop pinning, and fall back to the PATH search."""
    data = _read()
    data.pop("binary", None)
    _write(data)
    return resolve(refresh=True)


def record_observed(binary: str) -> None:
    """Remember the runtime that was actually used.

    Called after a launch resolves a binary from ``PATH``. It is what turns
    "the answer changed" from invisible into reportable: without it, a browser
    installed later just quietly becomes the runtime.
    """
    if not binary:
        return
    data = _read()
    if data.get("observed") == binary:
        return
    data["observed"] = binary
    try:
        _write(data)
    except OSError as e:  # noqa: BLE001 - never fail a launch over bookkeeping
        log.warning("could not record the observed engine: %s", e)


def resolve(*, refresh: bool = False) -> EngineChoice:
    """Decide which browser to use, and describe it.

    ``refresh`` is accepted for symmetry with the capability detector; nothing
    here is cached, because a cached answer that is wrong is how the three
    disagreeing answers above happened in the first place.
    """
    from .extensions.capability import detect as detect_capability

    env = (os.environ.get(_ENV_VAR) or "").strip()
    persisted = get_persisted()
    observed = get_observed()
    notes: list[str] = []

    if env:
        binary, origin = env, ORIGIN_ENV
        notes.append(f"{_ENV_VAR} is set, so it wins over any persisted choice.")
    elif persisted:
        binary, origin = persisted, ORIGIN_PERSISTED
        notes.append("Using the persisted engine choice.")
    else:
        from .engine import find_browser_binary

        found = find_browser_binary()
        binary, origin = (found or None), (ORIGIN_PATH if found else ORIGIN_MISSING)
        if found:
            notes.append(
                "No engine is pinned, so this came from the PATH search. "
                "Pin it to stop another browser becoming the runtime when it "
                "is installed."
            )

    if not binary:
        return EngineChoice(
            None, ORIGIN_MISSING,
            reason="No Chrome or Chromium executable was found on PATH.",
            remediation=("Install Chromium, or pin a binary with "
                         f"{_ENV_VAR} or the engine choice API."),
            notes=notes,
        )

    if not Path(binary).is_file():
        return EngineChoice(
            binary, origin,
            reason=f"{binary} does not exist.",
            remediation=("Pin an existing browser binary, or clear the choice "
                         "to fall back to the PATH search."),
            notes=notes,
        )

    cap = detect_capability(binary)
    choice = EngineChoice(
        binary=binary, origin=origin, brand=cap.brand, version=cap.version,
        major=cap.major, unpacked=cap.unpacked, reason=cap.reason,
        remediation=cap.remediation, notes=notes,
    )

    # Drift: the PATH search now answers differently than the runtime this
    # install has actually been using. Only meaningful when nothing is pinned,
    # because a pin is precisely the instruction to ignore PATH.
    if origin == ORIGIN_PATH and observed and observed != binary:
        choice.drifted_from = observed
        previous = detect_capability(observed)
        choice.drift_breaks_extensions = previous.unpacked and not cap.unpacked
        choice.notes.append(
            f"The runtime changed: this install previously used {observed} "
            f"and the PATH search now resolves {binary}."
        )
        if choice.drift_breaks_extensions:
            choice.notes.append(
                f"{observed} can load unpacked extensions and {binary} cannot, "
                f"so every extension assignment would stop working on it."
            )
    return choice


def extension_runtime_block(profile_has_extensions: bool) -> str | None:
    """Why a launch with extensions assigned must not proceed, or None.

    Returns a message rather than raising, because the launcher reports
    refusals as data. A profile with no extensions is unaffected: this feature
    must not be able to stop ordinary profiles launching.
    """
    if not profile_has_extensions:
        return None
    choice = resolve()
    if choice.binary is None:
        return (f"This profile has extensions assigned and no browser was "
                f"found. {choice.remediation}")
    # Drift first. ``drift_breaks_extensions`` already means the current
    # runtime cannot load, so the generic message below would always win and
    # the drift explanation -- which names what this install was using and
    # says nothing was switched -- would be unreachable. A test caught that.
    if choice.drift_breaks_extensions and choice.drifted_from:
        return (
            f"The browser this install was using ({choice.drifted_from}) can "
            f"load extensions and the one PATH now resolves ({choice.binary}) "
            f"cannot, so the extensions assigned to this profile would "
            f"silently stop working. Pin the runtime you want with "
            f"{_ENV_VAR} or the engine choice API, then launch again. "
            f"Nothing has been switched for you."
        )
    if not choice.unpacked:
        return (
            f"This profile has extensions assigned, and the selected browser "
            f"cannot load them: {choice.reason} "
            f"{choice.remediation} "
            f"Selected binary: {choice.binary} (origin: {choice.origin}). "
            f"No other browser is substituted for you."
        )
    return None
