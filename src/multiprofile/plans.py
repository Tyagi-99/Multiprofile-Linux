"""What this installation is allowed to do, in one place.

The limits existed before this; they were just scattered and anonymous.
``MAX_PROFILES`` lived in the profile store, ``MAX_CONCURRENT_PROFILES`` in
the browser manager, and neither was reachable from the other or visible to
the operator. Asking "what am I allowed to run?" meant reading two modules and
knowing which environment variables the process happened to start with.

That is a problem before it is a commercial one. An operator who hits a limit
gets a refusal naming a number they never chose and cannot find. Naming the
set of limits -- a plan -- makes the answer one request instead of an
archaeology exercise, and it is the same object a hosted product would sell.

**Plans do not authorise anything.** This module reports and enforces limits;
it does not check a licence, phone home, or decide whether the operator paid.
A local install runs the ``self-hosted`` plan, which is deliberately the most
generous one, because a tool someone runs on their own machine has no business
refusing to work. Enforcement that matters commercially belongs on a server
that issues the plan, and that server does not exist yet -- see
``docs/08_COMMERCIAL_ARCHITECTURE.md``.

**Environment variables still win.** ``MAX_PROFILES=400`` overrides whatever
the plan says. This is not a hole in a security control -- there is no
security control here -- it is the acknowledgement that on a local install the
operator owns the machine, and pretending otherwise would be theatre.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from .config import get_settings

#: The environment variable that selects a plan.
PLAN_ENV = "MULTIPROFILE_PLAN"

#: The plan a local install runs unless told otherwise.
DEFAULT_PLAN = "self-hosted"


@dataclass(frozen=True)
class Plan:
    """A named set of limits.

    ``max_profiles`` and ``max_concurrent`` are the two that are actually
    enforced today, because they are the two the product already had. The
    others are declared so the shape is honest about what a plan would carry;
    each is marked in :func:`limits` with whether anything enforces it, so a
    reader is never left guessing which of these are real.
    """

    id: str
    name: str
    max_profiles: int
    max_concurrent: int
    #: Workspaces (``clients``). 0 means unlimited.
    max_workspaces: int = 0
    #: API tokens that may exist at once. 0 means unlimited.
    max_api_tokens: int = 0
    #: Kept for the report; nothing rations bandwidth locally.
    included_runtime_hours: int = 0


#: The catalogue. `self-hosted` is what this build runs; the others exist so
#: the limits are expressed once, in the vocabulary a hosted product would
#: use, rather than being invented later in a billing service that has no
#: idea what the desktop app enforces.
PLANS: dict[str, Plan] = {
    "self-hosted": Plan(
        id="self-hosted",
        name="Self-hosted",
        max_profiles=150,
        max_concurrent=20,
    ),
    "solo": Plan(
        id="solo",
        name="Solo",
        max_profiles=50,
        max_concurrent=5,
        max_workspaces=1,
        max_api_tokens=2,
        included_runtime_hours=200,
    ),
    "team": Plan(
        id="team",
        name="Team",
        max_profiles=500,
        max_concurrent=30,
        max_workspaces=10,
        max_api_tokens=20,
        included_runtime_hours=2000,
    ),
}


def active_plan() -> Plan:
    """The plan this process is running under.

    An unrecognised name falls back to the default rather than raising: a
    typo in an environment variable must not stop the application booting,
    and :func:`describe` reports the fallback so it is visible rather than
    silent.
    """
    return PLANS.get(_requested_plan_id(), PLANS[DEFAULT_PLAN])


def _requested_plan_id() -> str:
    return (os.environ.get(PLAN_ENV) or DEFAULT_PLAN).strip().lower()


def plan_is_recognised() -> bool:
    return _requested_plan_id() in PLANS


def limits() -> dict[str, Any]:
    """Every limit, its source, and whether anything enforces it.

    ``source`` distinguishes a number the plan chose from one the environment
    overrode, because "you may store 150" and "you may store 150 because
    MAX_PROFILES says so" send an operator to different places when they want
    it changed.

    ``enforced`` is the part worth being pedantic about. Reporting a limit
    that nothing checks is the failure this whole audit has been about: a
    control that exists in the interface and not on the path.
    """
    plan = active_plan()
    settings = get_settings()
    return {
        "maxProfiles": {
            "value": settings.max_profiles,
            "planValue": plan.max_profiles,
            "source": "env" if _overridden("MAX_PROFILES") else "plan",
            "enforced": True,
        },
        "maxConcurrent": {
            "value": settings.max_concurrent_profiles,
            "planValue": plan.max_concurrent,
            "source": "env" if _overridden("MAX_CONCURRENT_PROFILES") else "plan",
            "enforced": True,
        },
        "maxWorkspaces": {
            "value": plan.max_workspaces or None,
            "planValue": plan.max_workspaces or None,
            "source": "plan",
            "enforced": False,
        },
        "maxApiTokens": {
            "value": plan.max_api_tokens or None,
            "planValue": plan.max_api_tokens or None,
            "source": "plan",
            "enforced": False,
        },
        "includedRuntimeHours": {
            "value": plan.included_runtime_hours or None,
            "planValue": plan.included_runtime_hours or None,
            "source": "plan",
            "enforced": False,
        },
    }


def _overridden(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def describe() -> dict[str, Any]:
    """The plan, its limits, and what is currently used against them."""
    plan = active_plan()
    body: dict[str, Any] = {
        "plan": asdict(plan),
        "limits": limits(),
        "recognised": plan_is_recognised(),
    }
    if not plan_is_recognised():
        body["warning"] = (
            f"{PLAN_ENV}={_requested_plan_id()!r} is not a plan this build knows; "
            f"running {DEFAULT_PLAN!r}"
        )
    body["usage"] = _current_usage()
    return body


def _current_usage() -> dict[str, Any]:
    """Measured, never estimated. A field that cannot be read says so.

    Returning zero for something unreadable would be the wrong lie: an
    operator would read "0 of 150 stored" as an empty fleet rather than as a
    broken query.
    """
    out: dict[str, Any] = {}
    try:
        from .storage.profiles import fleet_capacity

        fleet = fleet_capacity()
        out["profiles"] = fleet["stored"]
        out["profilesFree"] = fleet["free"]
    except Exception:  # noqa: BLE001 - a report must not fail on one field
        out["profiles"] = None
    try:
        from .storage import db

        out["running"] = len(db.list_profile_runtimes("running"))
    except Exception:  # noqa: BLE001
        out["running"] = None
    try:
        from . import usage as usage_mod

        month = usage_mod.summary("month")
        out["runtimeHoursThisMonth"] = round(month["total"]["seconds"] / 3600, 2)
        out["sessionsThisMonth"] = month["total"]["sessions"]
    except Exception:  # noqa: BLE001
        out["runtimeHoursThisMonth"] = None
    return out
