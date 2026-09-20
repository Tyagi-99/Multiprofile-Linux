"""Concurrency gate for Chrome profile launches (RAM safety)."""
from __future__ import annotations

import os
import threading
import time
from typing import Any

_lock = threading.Lock()
_cv = threading.Condition(_lock)
_active: set[str] = set()  # profile ids currently holding a slot


def max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("MAX_CONCURRENT_PROFILES", "3")))
    except (TypeError, ValueError):
        return 3


def active_count() -> int:
    with _lock:
        return len(_active)


def active_ids() -> list[str]:
    with _lock:
        return list(_active)


def acquire_slot(profile_id: str, timeout: float | None = 120.0) -> bool:
    """Block until a launch slot is free or timeout. Returns False on timeout."""
    deadline = None if timeout is None else time.monotonic() + timeout
    with _cv:
        while True:
            if profile_id in _active:
                # already holds slot (re-launch / same profile)
                return True
            if len(_active) < max_concurrent():
                _active.add(profile_id)
                return True
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                _cv.wait(timeout=remaining)
            else:
                _cv.wait(timeout=1.0)


def release_slot(profile_id: str) -> None:
    with _cv:
        _active.discard(profile_id)
        _cv.notify_all()


def mark_running(profile_id: str) -> None:
    """Ensure profile is counted as using a slot (e.g. after successful launch)."""
    with _cv:
        _active.add(profile_id)
        _cv.notify_all()


def slot_status() -> dict[str, Any]:
    with _lock:
        return {
            "max": max_concurrent(),
            "active": len(_active),
            "profileIds": list(_active),
        }
