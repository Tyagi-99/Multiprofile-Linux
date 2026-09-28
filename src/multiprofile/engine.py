"""What browser engine is actually installed, and what a profile may claim.

Every UA pool in ``fingerprint/_core.py``, all seven device presets and the
UA picker in ``api/user_agents.py`` were written against Chrome 131. The
engine those profiles actually run on is whatever Chromium the host has
installed -- 151 on the primary dev machine, roughly twenty stable releases
later. The claim and the engine are therefore two different browsers, and the
gap is detectable twice over: by probing an engine feature that shipped after
131, and by version cohort alone, because Chrome 131 has negligible
real-world share by now (F5).

This module is the one place that answers *what version may we claim*. It
asks the installed binary, caches the answer for the process, and offers the
rewrites that bring a UA string and a client-hints brand list into line with
it.

Nothing here invents a version. When the binary cannot be asked the answer is
"unknown", :meth:`EngineVersion.known` is False, and every caller keeps its
own literal rather than claiming a number nobody verified. A wrong guess and
a stale literal are both lies; only one of them is also unfalsifiable.

Two claims are deliberately different, because real Chrome makes them
differently:

* the **UA string** carries the reduced form ``<major>.0.0.0`` -- Chrome has
  frozen the last three components there since the UA-reduction rollout, so
  ``Chrome/151.0.0.0`` is what a real browser sends;
* ``Sec-CH-UA-Full-Version-List`` carries the **real** build, e.g.
  ``151.0.7654.32``. The old code put the reduced form in both, and no real
  Chrome build has a patch version of ``0.0`` -- that pairing was itself a
  tell.

Environment:
    ``MULTIPROFILE_ENGINE_VERSION``
        Pin the claimed version explicitly (``151.0.7654.32``). Wins over the
        binary, and lets an operator claim a version they know is current
        when the local engine is not.
    ``MULTIPROFILE_CLAIM_ENGINE_VERSION=0``
        Do no alignment at all: pools, presets and the UA picker keep their
        own literals. The escape hatch for a profile bought elsewhere whose
        whole identity is pinned to one version.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("multiprofile.engine")

# Same order as the launcher's own search: a real Chrome beats a Chromium,
# because a profile claiming "Google Chrome" on a Chrome binary is one fewer
# thing to explain.
BROWSER_BINARIES = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser")

_VERSION_TIMEOUT_S = 5.0

# "Chromium 151.0.7654.32 Arch Linux", "Google Chrome 141.0.7390.54",
# "Chromium 151.0.7654.32 snap" -- take the first dotted run of numbers.
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")

# Chromium version tokens in a UA string. Edge tracks the Chromium major
# exactly, so Edg/ moves with the rest; CriOS is Chrome-on-iOS.
_UA_VERSION_RE = re.compile(r"\b(Chrome|Chromium|CriOS|Edg|EdgA|EdgiOS|HeadlessChrome)/[\d.]+")

# UA reduction is a Blink change, and Chrome on iOS is not Blink -- it is
# WebKit under Apple's engine rule, and it never adopted the frozen
# "<major>.0.0.0" form. A real Chrome for iOS sends its whole build,
# "CriOS/151.0.7922.173"; the device presets were written that way, with the
# full version for CriOS and the reduced form for desktop and Android, and
# that distinction is correct. Reducing these two tokens with the rest would
# mint "CriOS/151.0.0.0" -- the same 0.0 patch pairing this module rejects
# elsewhere as a tell, and one no iOS build has ever shipped.
_UA_FULL_VERSION_TOKENS = frozenset({"CriOS", "EdgiOS"})

# GREASE brands say nothing about the identity and must not be rewritten --
# their version is deliberate noise. Chromium spells the brand differently on
# every build, hence the loose match.
_GREASE_RE = re.compile(r"not[^a-z0-9]*a[^a-z0-9]*brand", re.IGNORECASE)


def is_grease_brand(brand: Any) -> bool:
    """True for a GREASE entry in a client-hints brand list."""
    return bool(_GREASE_RE.search(str(brand or "")))


@dataclass(frozen=True)
class EngineVersion:
    """What the installed engine reports, or why it could not be asked.

    ``source`` is one of ``env`` (operator pin), ``binary`` (the engine
    answered), ``disabled`` (alignment turned off) or ``unavailable``.
    ``detail`` is the one-line reason, and it is what the identity path
    records when the answer is not usable.
    """

    full: str | None
    major: int | None
    source: str
    binary: str | None = None
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.full is not None and self.major is not None

    @property
    def ua_version(self) -> str | None:
        """The reduced form a real Chrome puts in its UA string."""
        return f"{self.major}.0.0.0" if self.major is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "full": self.full,
            "major": self.major,
            "uaVersion": self.ua_version,
            "source": self.source,
            "binary": self.binary,
            "detail": self.detail,
        }


def find_browser_binary() -> str | None:
    """First Chrome/Chromium on PATH, or None."""
    for name in BROWSER_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return None


def parse_version(text: str) -> str | None:
    """Pull a 4-part Chromium version out of ``--version`` output."""
    m = _VERSION_RE.search(text or "")
    if not m:
        return None
    major, minor, build, patch = m.group(1), m.group(2), m.group(3), m.group(4)
    return f"{major}.{minor}.{build}.{patch if patch is not None else '0'}"


def alignment_enabled() -> bool:
    """False when the operator has pinned everything to its own literals."""
    raw = (os.environ.get("MULTIPROFILE_CLAIM_ENGINE_VERSION") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _probe_binary(binary: str) -> tuple[str | None, str]:
    """Ask one binary for its version. Returns ``(version, detail)``."""
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return None, f"{binary} is not executable"
    except subprocess.TimeoutExpired:
        return None, f"{binary} --version timed out after {_VERSION_TIMEOUT_S:g}s"
    except OSError as e:
        return None, f"{binary} --version failed: {e}"

    out = f"{proc.stdout or ''} {proc.stderr or ''}".strip()
    if proc.returncode != 0:
        return None, f"{binary} --version exited {proc.returncode}: {out[:120]!r}"
    version = parse_version(out)
    if not version:
        return None, f"{binary} --version said {out[:120]!r}, no version in it"
    return version, out[:120]


_cache: dict[str | None, EngineVersion] = {}
_cache_lock = threading.Lock()


def reset_engine_version_cache() -> None:
    """Forget the detected version. For tests, and after an engine upgrade."""
    with _cache_lock:
        _cache.clear()


def detect_engine_version(binary: str | None = None, *, refresh: bool = False) -> EngineVersion:
    """What version may this host's profiles claim to be?

    Cached per binary for the life of the process: a launch must not pay for
    a subprocess, and the engine does not change underneath a running app.
    """
    if not refresh:
        with _cache_lock:
            hit = _cache.get(binary)
        if hit is not None:
            return hit

    result = _detect_uncached(binary)
    with _cache_lock:
        _cache[binary] = result
    return result


def _detect_uncached(binary: str | None) -> EngineVersion:
    if not alignment_enabled():
        return EngineVersion(
            None, None, "disabled", binary,
            "MULTIPROFILE_CLAIM_ENGINE_VERSION=0: claimed versions left as written",
        )

    pinned = (os.environ.get("MULTIPROFILE_ENGINE_VERSION") or "").strip()
    if pinned:
        version = parse_version(pinned)
        if version:
            return EngineVersion(
                version, int(version.split(".")[0]), "env", binary,
                f"MULTIPROFILE_ENGINE_VERSION={pinned}",
            )
        log.warning("MULTIPROFILE_ENGINE_VERSION=%r is not a version; asking the binary instead", pinned)

    resolved = binary or find_browser_binary()
    if not resolved:
        return EngineVersion(
            None, None, "unavailable", None,
            "no Chrome/Chromium on PATH, so the claimed version cannot be checked",
        )

    version, detail = _probe_binary(resolved)
    if not version:
        return EngineVersion(None, None, "unavailable", resolved, detail)
    return EngineVersion(version, int(version.split(".")[0]), "binary", resolved, detail)


# ---------------------------------------------------------------------------
# Rewrites -- pure, and no-ops when the engine version is unknown
# ---------------------------------------------------------------------------

def align_ua_string(ua: str, engine: EngineVersion | None = None) -> str:
    """Rewrite the Chromium version tokens in ``ua`` to the engine's version.

    Only the version numerals move. The platform, the model and the token
    names are the profile's identity and are left exactly as they are, so a
    Windows UA stays a Windows UA. A UA with no Chromium token (a Safari
    string, say) comes back unchanged.

    Which *form* of the version a token gets is per token, because real
    browsers differ: the reduced ``<major>.0.0.0`` for the Blink tokens that
    took part in UA reduction, and the full build for the iOS tokens that did
    not (see ``_UA_FULL_VERSION_TOKENS``).
    """
    engine = engine or detect_engine_version()
    reduced = engine.ua_version
    if not ua or not reduced:
        return ua

    def _replace(m: re.Match[str]) -> str:
        token = m.group(1)
        if token in _UA_FULL_VERSION_TOKENS:
            return f"{token}/{engine.full}"
        return f"{token}/{reduced}"

    return _UA_VERSION_RE.sub(_replace, ua)


def align_brands(brands: Any, engine: EngineVersion | None = None, *, full: bool = False) -> list[dict[str, Any]]:
    """Rewrite the versions in a client-hints brand list.

    ``full`` picks the real build (``151.0.7654.32``, for
    ``fullVersionList``) over the reduced form (``151``, for ``brands``).
    GREASE entries keep their own version, and each entry keeps whichever
    key it arrived with -- CDP spells it ``version``, the preset catalogue
    spells it ``fullVersion``.
    """
    engine = engine or detect_engine_version()
    if not isinstance(brands, list):
        return []
    if not engine.known:
        return [dict(b) for b in brands if isinstance(b, dict)]

    claim = (engine.full if full else str(engine.major)) or ""
    out: list[dict[str, Any]] = []
    for entry in brands:
        if not isinstance(entry, dict):
            continue
        new = dict(entry)
        if not is_grease_brand(new.get("brand")):
            for key in ("version", "fullVersion"):
                if key in new:
                    new[key] = claim
        out.append(new)
    return out


def align_ua_metadata(metadata: Any, engine: EngineVersion | None = None) -> Any:
    """Rewrite both brand lists inside a CDP ``userAgentMetadata`` block."""
    engine = engine or detect_engine_version()
    if not isinstance(metadata, dict) or not engine.known:
        return metadata
    out = dict(metadata)
    if "brands" in out:
        out["brands"] = align_brands(out.get("brands"), engine)
    for key in ("fullVersionList", "full_version_list"):
        if key in out:
            out[key] = align_brands(out.get(key), engine, full=True)
    return out
