"""In-memory launch state machine for browser profiles."""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class LaunchState(str, Enum):
    CREATED = "CREATED"
    LAUNCHING = "LAUNCHING"
    CDP_READY = "CDP_READY"
    WINDOW_FOUND = "WINDOW_FOUND"
    POSITIONING = "POSITIONING"
    VERIFIED = "VERIFIED"
    RUNNING = "RUNNING"
    LAUNCH_FAILED = "LAUNCH_FAILED"
    CDP_FAILED = "CDP_FAILED"
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    POSITION_FAILED = "POSITION_FAILED"
    VERIFY_FAILED = "VERIFY_FAILED"


@dataclass
class ProfileRuntime:
    profile_id: str
    launch_state: LaunchState = LaunchState.CREATED
    pid: int | None = None
    cdp_port: int | None = None
    target_id: str | None = None
    window_id: int | None = None
    slot_index: int | None = None
    desired_rect: dict[str, int] | None = None
    actual_rect: dict[str, int] | None = None
    error: str | None = None
    error_code: str | None = None
    updated_at: float = field(default_factory=time.time)
    # Network health fields — populated after proxy probe
    proxy_ok: bool | None = None
    egress_ip: str | None = None
    last_proxy_check: float | None = None
    # Identity injection (cdp_inject): did the fingerprint script, proxy
    # auth and cookies actually reach the browser? False here means the
    # profile is browsing unmasked and the operator must be told.
    inject_ok: bool | None = None
    inject_detail: dict[str, Any] | None = None
    # Launch-time wire-identity assertion (identity_assert): did the headers
    # the browser actually sent match the identity it was given? True =
    # verified, False = proven mismatch (the launch was refused), None = the
    # probe could not run, which is NOT a pass.
    identity_ok: bool | None = None
    identity_detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["launch_state"] = self.launch_state.value
        d["launchState"] = self.launch_state.value
        d["profileId"] = self.profile_id
        d["cdpPort"] = self.cdp_port
        return d


_lock = threading.RLock()
_registry: dict[str, ProfileRuntime] = {}


def _clone(rt: ProfileRuntime) -> ProfileRuntime:
    return ProfileRuntime(**{**asdict(rt), "launch_state": rt.launch_state})


def get_runtime(profile_id: str) -> ProfileRuntime | None:
    with _lock:
        rt = _registry.get(profile_id)
        return _clone(rt) if rt else None


def list_runtimes() -> list[ProfileRuntime]:
    with _lock:
        return [_clone(rt) for rt in _registry.values()]


def upsert_runtime(profile_id: str, **fields: Any) -> ProfileRuntime:
    with _lock:
        rt = _registry.get(profile_id)
        if rt is None:
            rt = ProfileRuntime(profile_id=profile_id)
            _registry[profile_id] = rt
        for k, v in fields.items():
            if k == "launch_state" and isinstance(v, str):
                v = LaunchState(v)
            if hasattr(rt, k) and k != "profile_id":
                setattr(rt, k, v)
        rt.updated_at = time.time()
        return _clone(rt)


def set_launch_state(
    profile_id: str,
    state: LaunchState | str,
    *,
    error: str | None = None,
    error_code: str | None = None,
    **fields: Any,
) -> ProfileRuntime:
    if isinstance(state, str):
        state = LaunchState(state)
    with _lock:
        rt = _registry.get(profile_id)
        if rt is None:
            rt = ProfileRuntime(profile_id=profile_id)
            _registry[profile_id] = rt
        rt.launch_state = state
        if error is not None:
            rt.error = error
        if error_code is not None:
            rt.error_code = error_code
        if state == LaunchState.RUNNING and error is None:
            rt.error = None
            rt.error_code = None
        for k, v in fields.items():
            if hasattr(rt, k) and k not in {"profile_id", "launch_state"}:
                setattr(rt, k, v)
        rt.updated_at = time.time()
        return _clone(rt)


def clear_runtime(profile_id: str) -> None:
    with _lock:
        _registry.pop(profile_id, None)


def clear_all_runtimes() -> None:
    with _lock:
        _registry.clear()
