"""Faults on the identity path, recorded instead of swallowed (F11).

``pyproject.toml`` deliberately deselects ``BLE001`` and ``S110``, and the
reason it gives is sound: a launch path must not crash a browser because some
optional step failed. The cost is that this product's value *is* surfacing
failures, and F1 was precisely a failure that was swallowed and reported as a
success. The exception policy and the silent-failure bug are the same problem
seen twice.

So this is not a blanket cleanup. Handlers on cosmetic paths keep their
``pass`` and a comment saying why. Handlers on the **identity** path -- the
ones that decide whether a profile browses as the identity it claims -- record
a fault here, and it reaches the operator in two places: the launch notes for
that profile, and ``/api/health`` for the process.

The shape follows ``engine.py``'s "unavailable" record, which already does this
for one case: a short machine-readable ``code``, a one-line human ``detail``,
and the place it happened. A fault is a statement that something which should
have shaped the identity did not, and that the profile may therefore be
claiming something it cannot back up. It is never an error the caller has to
handle -- recording is cheap and must not itself throw, because a logging
failure that breaks a launch would be a worse bug than the one it reports.

Deliberately in-memory and bounded. These are launch-time diagnostics whose
audience is an operator looking at the console now; the durable record of a
launch going wrong is the audit log, which already exists.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("multiprofile.browser.identity_faults")

# Enough to explain a handful of launches without growing without bound. The
# per-profile view is filtered from this, so a very chatty process can push an
# older profile's faults out -- acceptable for a live diagnostic, and the
# reason the audit log stays the durable record.
_MAX_FAULTS = 200

_lock = threading.RLock()
_faults: deque[IdentityFault] = deque(maxlen=_MAX_FAULTS)


@dataclass(frozen=True)
class IdentityFault:
    """One thing on the identity path that did not happen.

    ``code`` is stable and machine-readable so the console can group on it;
    ``detail`` is the one line an operator reads. ``where`` names the function
    that swallowed it, because the whole point is that the failure used to be
    invisible from outside that function.
    """

    code: str
    detail: str
    where: str
    profile_id: str | None = None
    at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "where": self.where,
            "profileId": self.profile_id,
            "at": self.at,
        }

    def note(self) -> str:
        """How this reads in a profile's launch notes."""
        return f"IDENTITY FAULT [{self.code}] {self.detail} (at {self.where})"


def record(
    code: str,
    detail: str,
    *,
    where: str,
    profile_id: str | None = None,
) -> IdentityFault | None:
    """Record a fault and return it. Never raises, whatever it is handed.

    Called from inside ``except`` blocks, so it must not be able to turn a
    swallowed failure into a crash -- that would make the cure worse than the
    disease and guarantee the policy gets reverted. Returns None only when it
    could not build the record at all, so a caller wanting the note must cope
    with that too.
    """
    try:
        fault = IdentityFault(
            code=str(code),
            detail=str(detail)[:500],
            where=str(where),
            profile_id=str(profile_id) if profile_id else None,
            at=time.time(),
        )
        with _lock:
            _faults.append(fault)
        # WARNING, not DEBUG: an identity step that did not happen is not a
        # detail. This is the level the old handlers should have used.
        log.warning("identity fault [%s] at %s: %s", fault.code, fault.where, fault.detail)
        return fault
    except Exception:  # noqa: BLE001 - recording must not break a launch
        return None


def faults_for(profile_id: str) -> list[IdentityFault]:
    """Faults recorded against one profile, oldest first."""
    if not profile_id:
        return []
    with _lock:
        return [f for f in _faults if f.profile_id == profile_id]


def clear_for(profile_id: str) -> None:
    """Drop a profile's faults, so a fresh launch reports only its own."""
    if not profile_id:
        return
    with _lock:
        keep = [f for f in _faults if f.profile_id != profile_id]
        _faults.clear()
        _faults.extend(keep)


def recent(limit: int = 25) -> list[IdentityFault]:
    """The newest faults across the process, newest first."""
    with _lock:
        items = list(_faults)
    items.reverse()
    return items[: max(0, int(limit))]


def health_summary(limit: int = 25) -> dict[str, Any]:
    """The ``identity`` block in ``/api/health``.

    ``faults: []`` is a real statement -- nothing on the identity path failed
    silently since this process started -- which is why the block is always
    present rather than appearing only on trouble.
    """
    with _lock:
        total = len(_faults)
        counts = Counter(f.code for f in _faults)
    return {
        "faults": [f.to_dict() for f in recent(limit)],
        "faultCount": total,
        "byCode": dict(counts),
        "truncated": total >= _MAX_FAULTS,
    }


def reset() -> None:
    """Drop every fault. For tests and for a fresh process view."""
    with _lock:
        _faults.clear()
