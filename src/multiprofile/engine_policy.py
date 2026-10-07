"""Which browser build this product is allowed to run, and whether it is that.

This is the plug-in point for **option A — a maintained Chromium-based core**.
A maintained core is not primarily a patch; it is the obligation to run *one
known build* and to keep knowing which build that is. Today the product runs
whatever ``find_browser_binary()`` happens to turn up on ``PATH``, which means:

* the identity it presents is decided by whoever updated the host's package
  manager last;
* a profile created under Chromium 151 and relaunched under 153 presents a
  different browser with the same stored seed, and nothing records that;
* and there is nowhere for a maintained build to *be* -- no configuration
  that says "use this one and refuse others".

So the vertical slice is the layer a core plugs into: **pin, identify, verify,
and record**. When a maintained build exists, it is named by ``engine_binary``
and everything below already enforces it. Until one exists, the same machinery
answers the question that matters now -- *is the engine on this host one we
support, and how old is it?* -- which is also Phase 5's browser-version policy
and security-update process.

**What this is not.** It is not a browser core, it does not patch anything,
and it makes no detection claim. A ``supported`` verdict means the build is
one this product declares support for and the operator can be told which.
That is a supply-chain property, not a fingerprint property.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The oldest Chromium major this product will vouch for.
#:
#: Chosen from measurement rather than taste. Below 121 a GPU-less host has no
#: WebGL at all unless ``--enable-unsafe-swiftshader`` is passed (F46), and
#: below 110 Chromium did not randomise ClientHello extension order, so a
#: profile would present a *stable* TLS fingerprint while the rest of the
#: fleet presents a randomised one -- which is itself a distinguisher.
SUPPORTED_MAJOR_MIN = 121

#: How old a build may be before the operator is told. Chrome ships a stable
#: refresh roughly every four weeks and security fixes inside that, so a build
#: more than two cycles behind is one an operator should be deciding about
#: deliberately rather than discovering.
STALE_AFTER_DAYS = 56

VERDICT_SUPPORTED = "supported"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_UNVERIFIED = "unverified"

#: Where the engine came from, which is the part a maintained core changes.
ORIGIN_PINNED = "pinned"      # named by configuration: a build we chose
ORIGIN_SYSTEM = "system"      # whatever was on PATH
ORIGIN_MISSING = "missing"


@dataclass
class EngineRecord:
    """One identified browser build.

    ``digest`` is over the binary itself. It is what makes "the same engine"
    a checkable statement rather than a version string that a rebuild can
    reuse -- two 151.0.7922.173 builds from different vendors are different
    software.
    """

    binary: str | None
    origin: str
    version_full: str | None = None
    version_major: int | None = None
    digest: str | None = None
    size: int | None = None
    mtime: float | None = None
    detail: str = ""

    @property
    def age_days(self) -> float | None:
        if self.mtime is None:
            return None
        return max(0.0, (time.time() - self.mtime) / 86400.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary": self.binary,
            "origin": self.origin,
            "versionFull": self.version_full,
            "versionMajor": self.version_major,
            # Short form: enough to compare builds, too short to be mistaken
            # for a supply-chain attestation.
            "digest": self.digest[:16] if self.digest else None,
            "size": self.size,
            "ageDays": round(self.age_days, 1) if self.age_days is not None else None,
            "detail": self.detail,
        }


@dataclass
class EngineVerdict:
    """Whether the identified build is one this product supports."""

    status: str
    record: EngineRecord
    reasons: list[str] = field(default_factory=list)
    #: Set when the operator asked for a pinned build and got something else.
    pin_violated: bool = False

    @property
    def ok(self) -> bool:
        return self.status == VERDICT_SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pinViolated": self.pin_violated,
            "reasons": list(self.reasons),
            "engine": self.record.to_dict(),
            "policy": {
                "supportedMajorMin": SUPPORTED_MAJOR_MIN,
                "staleAfterDays": STALE_AFTER_DAYS,
            },
        }


def _digest(path: Path, *, limit: int = 64 * 1024 * 1024) -> tuple[str, int]:
    """SHA-256 of the binary, and its size.

    Bounded on purpose. A browser binary can be hundreds of megabytes and
    this runs on a launch path; the first 64 MiB plus the exact size
    distinguishes builds without turning every launch into a full read. It is
    an identity check, not an integrity attestation -- a signed build would be
    the latter, and that is named as a remaining gate in
    ``docs/09_ENGINE_PROGRAM.md``.
    """
    size = path.stat().st_size
    hasher = hashlib.sha256()
    hasher.update(str(size).encode())
    with path.open("rb") as handle:
        remaining = limit
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            hasher.update(chunk)
            remaining -= len(chunk)
    return hasher.hexdigest(), size


_cache: dict[str, EngineRecord] = {}


def identify_engine(binary: str | None = None, *, refresh: bool = False) -> EngineRecord:
    """Identify the build this product would run.

    Prefers a configured pin over whatever is on ``PATH``, because that
    preference *is* the maintained-core mechanism: the day a managed build
    exists, naming it is the whole integration.
    """
    from .config import get_settings
    from .engine import detect_engine_version, find_browser_binary

    pinned = ""
    try:
        pinned = (get_settings().engine_binary or "").strip()
    except Exception as e:  # noqa: BLE001 - reported, never fatal to a launch
        return EngineRecord(None, ORIGIN_MISSING,
                            detail=f"settings unavailable: {e}")

    if binary:
        path_text, origin = binary, ORIGIN_PINNED if pinned == binary else ORIGIN_SYSTEM
    elif pinned:
        path_text, origin = pinned, ORIGIN_PINNED
    else:
        found = find_browser_binary()
        path_text, origin = (found or ""), ORIGIN_SYSTEM

    if not path_text:
        return EngineRecord(None, ORIGIN_MISSING,
                            detail="no Chrome or Chromium binary found")

    cache_key = f"{path_text}:{origin}"
    if not refresh and cache_key in _cache:
        return _cache[cache_key]

    path = Path(path_text)
    if not path.is_file():
        record = EngineRecord(
            path_text, origin,
            detail=("the configured engine_binary does not exist"
                    if origin == ORIGIN_PINNED else "binary not found"))
        _cache[cache_key] = record
        return record

    version = detect_engine_version(path_text, refresh=refresh)
    try:
        digest, size = _digest(path)
    except OSError as e:
        digest, size = None, None
        detail = f"could not read the binary to identify it: {e}"
    else:
        detail = version.detail or ""

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    record = EngineRecord(
        binary=path_text, origin=origin,
        version_full=version.full, version_major=version.major,
        digest=digest, size=size, mtime=mtime, detail=detail,
    )
    _cache[cache_key] = record
    return record


def reset_cache() -> None:
    _cache.clear()


def verify_engine(record: EngineRecord | None = None) -> EngineVerdict:
    """Judge an identified build against the declared policy."""
    from .config import get_settings

    record = record or identify_engine()
    reasons: list[str] = []
    pin_violated = False

    try:
        settings = get_settings()
        pinned = (settings.engine_binary or "").strip()
        require_pin = bool(settings.engine_require_pinned)
    except Exception:  # noqa: BLE001 - policy without settings is still useful
        pinned, require_pin = "", False

    if record.binary is None:
        return EngineVerdict(VERDICT_UNSUPPORTED, record,
                             reasons=["no browser binary was found"])

    if require_pin and record.origin != ORIGIN_PINNED:
        pin_violated = True
        reasons.append(
            "engine_require_pinned is set, so only the build named by "
            "engine_binary may be used, and this is the host's own "
            f"({record.binary})"
        )
    if pinned and record.origin == ORIGIN_PINNED and not Path(pinned).is_file():
        pin_violated = True
        reasons.append(f"engine_binary names a file that does not exist: {pinned}")

    if record.version_major is None:
        reasons.append(
            "the engine would not report its version, so it cannot be "
            f"checked against the supported range ({record.detail or 'no detail'})"
        )
        return EngineVerdict(VERDICT_UNVERIFIED, record, reasons=reasons,
                             pin_violated=pin_violated)

    if record.version_major < SUPPORTED_MAJOR_MIN:
        reasons.append(
            f"Chromium {record.version_major} is older than the supported "
            f"minimum {SUPPORTED_MAJOR_MIN}. Below 121 a GPU-less host has no "
            "WebGL at all without --enable-unsafe-swiftshader (F46), and "
            "below 110 the ClientHello extension order is not randomised, so "
            "the profile presents a stable TLS fingerprint while the rest of "
            "the fleet presents a randomised one"
        )
        return EngineVerdict(VERDICT_UNSUPPORTED, record, reasons=reasons,
                             pin_violated=pin_violated)

    age = record.age_days
    if age is not None and age > STALE_AFTER_DAYS:
        reasons.append(
            f"this build is {age:.0f} days old, more than the "
            f"{STALE_AFTER_DAYS}-day policy. Chrome ships a stable refresh "
            "roughly every four weeks with security fixes inside that, so a "
            "build this far behind is one to decide about deliberately"
        )
        return EngineVerdict(VERDICT_UNVERIFIED, record, reasons=reasons,
                             pin_violated=pin_violated)

    if pin_violated:
        return EngineVerdict(VERDICT_UNSUPPORTED, record, reasons=reasons,
                             pin_violated=True)

    reasons.append(
        f"Chromium {record.version_major} is within the supported range and "
        f"the build is {age:.0f} days old" if age is not None else
        f"Chromium {record.version_major} is within the supported range"
    )
    return EngineVerdict(VERDICT_SUPPORTED, record, reasons=reasons)


def health_block() -> dict[str, Any]:
    """The ``/api/health`` block. Never raises."""
    try:
        return verify_engine().to_dict()
    except Exception as e:  # noqa: BLE001 - a diagnostic must not break health
        return {"status": VERDICT_UNVERIFIED, "reasons": [f"unavailable: {e}"]}
