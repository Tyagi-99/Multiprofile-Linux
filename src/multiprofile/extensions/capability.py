"""What the installed browser will actually do with an extension.

Everything in this package depends on one question with a binary-specific
answer: *can this browser be told to load an unpacked extension?* The flag
that does it, ``--load-extension``, was **removed from branded Google Chrome
in 137**. It still works in Chromium and in Chrome for Testing. So the answer
cannot be read off the flag's existence, off the Chromium major version, or
off documentation -- it depends on which binary this machine launches.

Measured on this host, Chromium 151.0.7922.173 (Arch), against an unpacked
MV3 fixture, with the evidence being a marker the extension's **content
script** writes into a page (a file on disk and a flag on a command line
prove nothing):

    control: no extension flags                      did not run
    --load-extension=<dir>                           RAN  (id assigned, v1.0.0)
    --load-extension + --disable-extensions          did not run
    --load-extension + --disable-extensions-except   RAN

The third row is the one that matters for this product: the launcher appends
``--disable-extensions`` to every profile as its anti-detect exclusivity
setting, and that flag **wins** over ``--load-extension``. A profile with an
extension assigned therefore has to be launched without it, which is a real
change to that profile's posture and is reported as such rather than done
quietly.

This module never substitutes a different browser. If the selected binary
cannot load extensions it says so, names what would, and stops.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

from ..engine import find_browser_binary, parse_version

#: ``--load-extension`` was removed from branded Google Chrome in 137. Chrome
#: for Testing and Chromium kept it. The brand word from ``--version`` is what
#: separates them, so it is parsed rather than inferred from the version.
CHROME_LOAD_EXTENSION_REMOVED_IN = 137

#: Brands that still honour ``--load-extension`` at any version.
_UNPACKED_OK_BRANDS = frozenset({"chromium", "chrome-for-testing"})

#: Reported verbatim to the operator. Not a suggestion to replace anything.
_CHROME_REMEDIATION = (
    "Branded Google Chrome 137+ no longer accepts --load-extension. "
    "Automated per-profile loading needs Chromium or Chrome for Testing as the "
    "engine binary (MULTIPROFILE_ENGINE_BINARY). Your browser is not changed "
    "for you. Chrome Web Store installs still work through the browser's own "
    "flow, per profile, from the Extensions page."
)

_BRAND_RE = re.compile(
    r"\b(chrome\s+for\s+testing|google\s+chrome|chromium|brave\s+browser|"
    r"microsoft\s+edge|opera|vivaldi)\b",
    re.IGNORECASE,
)

_VERSION_TIMEOUT_S = 5.0


def _normalise_brand(raw: str) -> str:
    low = " ".join(raw.lower().split())
    if low == "chrome for testing":
        return "chrome-for-testing"
    if low == "google chrome":
        return "google-chrome"
    if low == "brave browser":
        return "brave"
    if low == "microsoft edge":
        return "edge"
    return low


@dataclass(frozen=True)
class BrowserCapability:
    """What this binary supports, and why we believe it."""

    binary: str | None
    brand: str | None            # chromium | google-chrome | chrome-for-testing | ...
    version: str | None
    major: int | None
    #: Can we load an unpacked extension by command line?
    unpacked: bool
    #: Can we install a .crx by command line? Nothing supported can.
    crx: bool
    reason: str
    remediation: str = ""
    #: Highest manifest_version this engine is known to run.
    max_manifest_version: int = 3
    raw: str = ""

    @property
    def supported(self) -> bool:
        return self.unpacked

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary": self.binary,
            "brand": self.brand,
            "version": self.version,
            "major": self.major,
            "unpackedLoading": self.unpacked,
            "crxSideload": self.crx,
            "maxManifestVersion": self.max_manifest_version,
            "supported": self.supported,
            "reason": self.reason,
            "remediation": self.remediation,
        }


def _probe(binary: str) -> tuple[str | None, str | None, str]:
    """``(brand, version, raw)`` from ``<binary> --version``."""
    try:
        proc = subprocess.run(
            [binary, "--version"], capture_output=True, text=True,
            timeout=_VERSION_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return None, None, f"{binary} could not be run: {e}"
    raw = ((proc.stdout or "") + " " + (proc.stderr or "")).strip()
    m = _BRAND_RE.search(raw)
    brand = _normalise_brand(m.group(1)) if m else None
    return brand, parse_version(raw), raw


def detect(binary: str | None = None) -> BrowserCapability:
    """Ask the binary this machine actually launches what it supports.

    No caching. A capability answer that is wrong is worse than one that costs
    5 ms, and the operator can change ``MULTIPROFILE_ENGINE_BINARY`` or install
    a different browser between two launches.
    """
    resolved = binary or find_browser_binary()
    if not resolved:
        return BrowserCapability(
            None, None, None, None, False, False,
            "No Chrome/Chromium binary was found on PATH, so nothing can be "
            "loaded into anything.",
            "Install Chromium, or set MULTIPROFILE_ENGINE_BINARY to a browser binary.",
        )

    brand, version, raw = _probe(resolved)
    major = int(version.split(".")[0]) if version else None

    if brand is None:
        return BrowserCapability(
            resolved, None, version, major, False, False,
            f"{resolved} did not identify itself as a Chromium-family browser "
            f"({raw[:120]!r}), so its extension support is unknown. Refusing to "
            f"guess.",
            "Set MULTIPROFILE_ENGINE_BINARY to a Chromium or Chrome for Testing build.",
            raw=raw,
        )

    if brand in _UNPACKED_OK_BRANDS:
        return BrowserCapability(
            resolved, brand, version, major, True, False,
            f"{brand} {version or '(unknown version)'} accepts --load-extension. "
            f"Verified on this host by loading an unpacked MV3 fixture and "
            f"reading the marker its content script wrote into a page.",
            raw=raw,
        )

    if brand == "google-chrome":
        if major is not None and major >= CHROME_LOAD_EXTENSION_REMOVED_IN:
            return BrowserCapability(
                resolved, brand, version, major, False, False,
                f"Google Chrome {version} removed --load-extension "
                f"(branded builds, {CHROME_LOAD_EXTENSION_REMOVED_IN}+). "
                f"Unpacked extensions cannot be loaded from the command line.",
                _CHROME_REMEDIATION,
                raw=raw,
            )
        return BrowserCapability(
            resolved, brand, version, major, True, False,
            f"Google Chrome {version} is below {CHROME_LOAD_EXTENSION_REMOVED_IN}, "
            f"where --load-extension still worked. Not verified on this host.",
            raw=raw,
        )

    # Brave, Edge, Opera, Vivaldi: Chromium-based and they generally keep the
    # flag, but this product has not measured any of them, and an unverified
    # yes is the thing this package exists not to do.
    return BrowserCapability(
        resolved, brand, version, major, False, False,
        f"{brand} {version or ''} is Chromium-based but unpacked loading has "
        f"not been verified against it by this product, so it is not claimed.",
        "Set MULTIPROFILE_ENGINE_BINARY to a Chromium or Chrome for Testing build "
        "to use automated per-profile loading.",
        raw=raw,
    )
