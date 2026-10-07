"""Which claimed identities this host can actually back, and which it cannot.

The parity roadmap asks for a *supported identity matrix*, enforced, with
cross-OS emulation labelled experimental where it cannot pass. This module is
that matrix, and it is deliberately built from what the host can be measured
to provide rather than from what the product would like to claim.

**The decision this encodes.** A profile claiming Windows on a Linux host
fails font coherence in both directions at once, measured on this machine
against a stock Chromium control in the same session:

===========================  =======================  =========================
probe                        product (claims Windows)  stock Chromium (Linux)
===========================  =======================  =========================
Windows-exclusive faces      **0 of 10 present**      0 of 10
``Liberation Mono``          **present**              present
===========================  =======================  =========================

Both directions are wrong and only one of them is fixable here. The absent
Windows faces need the actual font files, which is a licensing question, not
an engineering one. The present Linux face is worse: *no Windows machine has
``Liberation Mono``*, so its presence beside a Windows claim is a
contradiction rather than mere sparseness -- and it cannot be hidden by
renaming, because Chrome reads the font file's internal name table and
fontconfig cannot rewrite it. ``browser/fontconfig.py`` records this as the
``font_backer_name_visible`` identity fault without being asked.

So: **the native platform is supported, and every cross-OS claim is
experimental.** That is not a statement that cross-OS profiles are useless --
they are coherent on every surface this product controls, which is most of
them -- it is a statement that one surface is known to contradict them and
that an operator moving a valuable account deserves to know which.

**What this module does not do.** It does not score, rank or predict whether
a site accepts the browser. A verdict of ``SUPPORTED`` means "no surface we
measure is known to contradict this claim on this host", which is a quality
gate and not a detection guarantee.
"""
from __future__ import annotations

import platform as _platform
import sys
from dataclasses import dataclass, field
from typing import Any

#: The claim is coherent on every surface this host can be measured for.
SUPPORTED = "supported"
#: The claim is coherent on the surfaces the product controls, and at least
#: one surface it does not control is known to contradict it.
EXPERIMENTAL = "experimental"
#: The claim cannot be made coherently here at all.
UNSUPPORTED = "unsupported"


@dataclass
class IdentityVerdict:
    """One claimed platform, judged against one host."""

    claim: str
    status: str
    native: bool
    reasons: list[str] = field(default_factory=list)
    #: Surfaces known to contradict the claim, named so an operator can look.
    contradicts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "status": self.status,
            "native": self.native,
            "reasons": list(self.reasons),
            "contradicts": list(self.contradicts),
        }


def host_platform_claim() -> str:
    """The claim that matches the machine this is running on.

    Returned as a ``POOL_MAP`` key so it can be used directly as a profile's
    platform selection.
    """
    system = sys.platform
    if system.startswith("linux"):
        return "linux"
    if system == "darwin":
        return "macos"
    if system.startswith("win"):
        return "windows"
    # An unknown host cannot claim to be itself coherently, and guessing
    # would be the F31 mistake -- a plausible wrong answer is worse than an
    # explicit unknown.
    return ""


#: Faces no Linux desktop has, so a Linux claim must not expose them, and a
#: Windows claim should. Kept short and uncontroversial on purpose.
WINDOWS_EXCLUSIVE_FACES = (
    "Calibri", "Cambria", "Consolas", "Georgia", "Segoe UI", "Tahoma",
    "Trebuchet MS", "Verdana", "Impact", "Microsoft Sans Serif",
)

#: Faces no Windows or macOS machine has. Their presence beside such a claim
#: is a contradiction, not sparseness.
LINUX_EXCLUSIVE_FACES = (
    "Liberation Sans", "Liberation Serif", "Liberation Mono",
    "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
)


def _font_reasons(claim: str) -> tuple[list[str], list[str]]:
    """Reasons and contradicting surfaces for a claim's font situation.

    Asks ``fontconfig`` what it can actually back rather than assuming, so a
    host that *does* have the Windows files gets a different answer.
    """
    reasons: list[str] = []
    contradicts: list[str] = []
    if claim == host_platform_claim():
        return reasons, contradicts
    try:
        from ..browser.fontconfig import plan_for_platform
    except Exception as e:  # noqa: BLE001 - an unknown font state is reported, not guessed
        reasons.append(f"font backing could not be determined: {e}")
        contradicts.append("fonts")
        return reasons, contradicts

    # plan_for_platform works from a fingerprint, so the claim is expressed
    # the way one would express it. Only the platform keys matter to
    # _platform_bucket.
    probe_fingerprint = {
        "windows": {"platform": "Win32", "ch_platform": "Windows"},
        "macos": {"platform": "MacIntel", "ch_platform": "macOS"},
        "linux": {"platform": "Linux x86_64", "ch_platform": "Linux"},
        "android": {"platform": "Linux armv8l", "ch_platform": "Android"},
        "iphone": {"platform": "iPhone", "ch_platform": "iOS"},
    }.get(claim, {})
    try:
        plan = plan_for_platform(probe_fingerprint, "identity-matrix")
    except Exception as e:  # noqa: BLE001 - same
        reasons.append(f"no font plan for {claim!r}: {e}")
        contradicts.append("fonts")
        return reasons, contradicts

    unbacked = [str(name) for name in (plan.get("unbacked") or ())]
    if unbacked:
        reasons.append(
            f"{len(unbacked)} face(s) the claimed platform has cannot be "
            f"backed by any file on this host and are absent: "
            f"{', '.join(unbacked)}"
        )
        contradicts.append("fonts")

    # The direction that is a contradiction rather than an absence. A renamed
    # backer stays enumerable under its own name because Chrome reads the
    # font file's name table, which fontconfig cannot rewrite -- so the claim
    # is accompanied by a face the claimed platform does not have.
    backers = sorted(plan.get("renames") or {})
    if backers:
        reasons.append(
            "metric backers remain enumerable under their own names, so the "
            f"claim is contradicted by the presence of {', '.join(backers)} "
            f"-- which no {claim} machine has. Chrome reads the font file's "
            "internal name table and fontconfig cannot rewrite it"
        )
        contradicts.append("fonts")
    return reasons, contradicts


def verdict_for(claim: str) -> IdentityVerdict:
    """Judge one claimed platform against this host."""
    claim = (claim or "").strip().lower()
    aliases = {"win": "windows", "mac": "macos", "ios": "iphone"}
    claim = aliases.get(claim, claim)
    host = host_platform_claim()
    native = bool(host) and claim == host

    if claim not in ("windows", "macos", "linux", "android", "iphone"):
        return IdentityVerdict(claim or "(unset)", UNSUPPORTED, False,
                               reasons=["not a platform this product models"])

    if native:
        return IdentityVerdict(
            claim, SUPPORTED, True,
            reasons=["the claim matches the host, so no surface has to be "
                     "emulated for it to be coherent"])

    reasons, contradicts = _font_reasons(claim)
    if claim in ("android", "iphone"):
        reasons.append(
            "mobile emulation changes the viewport, touch and device metrics "
            "but runs a desktop Linux engine build; the engine's own version "
            "and platform strings are overridden rather than native"
        )
        contradicts.append("engine build")
    if not host:
        reasons.append("the host platform could not be identified, so "
                       "'native' cannot be established either way")

    status = EXPERIMENTAL if contradicts else SUPPORTED
    return IdentityVerdict(claim, status, False, reasons=reasons,
                           contradicts=sorted(set(contradicts)))


def matrix() -> list[IdentityVerdict]:
    """Every claim this product models, judged against this host."""
    return [verdict_for(c) for c in
            ("linux", "windows", "macos", "android", "iphone")]


def host_description() -> dict[str, Any]:
    """What the matrix was judged against, for a report that outlives it."""
    return {
        "platformClaim": host_platform_claim() or None,
        "system": _platform.platform(),
        "machine": _platform.machine(),
    }
