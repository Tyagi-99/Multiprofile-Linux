"""Control-plane state machine for browser profiles.

Single source of truth for profile lifecycle states. The control plane never
assumes a browser is alive — it consults supervisor state, and every state
change flows through this machine so invalid transitions fail loudly instead
of silently corrupting the runtime record.

States::

    stopped -> starting -> running -> stopping -> stopped
                              |          |
                              v          v
                             error -----> starting   (explicit restart/recover)
                             error -----> stopped    (explicit recover)

``error`` is terminal-ish: it can only be left via an explicit
restart/recover action (``error -> starting``) or an explicit operator
acknowledgement (``error -> stopped``). There is no automatic path out of
``error`` so a dead browser cannot be silently forgotten.

``running -> starting`` is the explicit relaunch path (used by the
supervisor's bounded restart logic and the restart job).

Same-state transitions are no-ops and are always allowed (idempotent writes).
"""
from __future__ import annotations

from enum import Enum


class ProfileState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class InvalidStateTransition(ValueError):
    """Raised when a profile state change is not allowed by the state machine."""

    def __init__(self, profile_id: str, from_state: str, to_state: str) -> None:
        self.profile_id = profile_id
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Invalid state transition for profile '{profile_id}': "
            f"'{from_state}' -> '{to_state}'. "
            f"Allowed from '{from_state}': {sorted(_ALLOWED.get(from_state, ())) or ['(none)']}."
        )


# Allowed next states for each state. Absent/no-op transitions are always fine.
_ALLOWED: dict[str, set[str]] = {
    ProfileState.STOPPED.value: {ProfileState.STARTING.value},
    ProfileState.STARTING.value: {
        ProfileState.RUNNING.value,
        ProfileState.ERROR.value,
        ProfileState.STOPPED.value,
    },
    ProfileState.RUNNING.value: {
        ProfileState.STOPPING.value,
        ProfileState.ERROR.value,
        ProfileState.STARTING.value,  # explicit relaunch
    },
    ProfileState.STOPPING.value: {
        ProfileState.STOPPED.value,
        ProfileState.ERROR.value,
    },
    ProfileState.ERROR.value: {
        ProfileState.STARTING.value,  # explicit restart/recover only
        ProfileState.STOPPED.value,   # explicit operator acknowledgement
    },
}


def normalize(state: ProfileState | str) -> str:
    """Return the canonical string value for a state."""
    if isinstance(state, ProfileState):
        return state.value
    value = str(state).strip().lower()
    if value not in _ALLOWED:
        raise ValueError(f"Unknown profile state: {state!r}")
    return value


def allowed_transitions(state: ProfileState | str) -> frozenset[str]:
    """Return the set of states reachable from ``state`` (excluding itself)."""
    return frozenset(_ALLOWED[normalize(state)])


def validate_transition(
    profile_id: str, from_state: ProfileState | str, to_state: ProfileState | str
) -> str:
    """Validate ``from_state -> to_state``; return the normalized target.

    Raises:
        InvalidStateTransition: if the transition is not allowed.
        ValueError: if either state name is unknown.
    """
    src = normalize(from_state)
    dst = normalize(to_state)
    if src == dst:
        return dst  # no-op writes are idempotent
    if dst not in _ALLOWED[src]:
        raise InvalidStateTransition(profile_id, src, dst)
    return dst
