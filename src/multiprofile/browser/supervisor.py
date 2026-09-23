"""Browser supervisor — control-plane watchdog for browser processes.

The control plane never assumes a browser is alive; this supervisor verifies
it. Every ``interval_s`` seconds it walks the durable runtime records in
state ``running`` and checks the recorded PID:

- PID alive  -> heartbeat recorded, nothing else happens.
- PID dead   -> audit entry, state ``running -> error`` (reason
  ``browser process died``), then a bounded restart:

  ``error -> starting`` -> relaunch through the existing launcher code path
  (``browser.launcher.launch_profile`` — no duplicated launch logic) ->
  on success state ``running`` and the **entire grid is re-tiled via CDP**
  (CDP is the only tiling authority; gap=0, margin=0 come from config).

Restart retries are bounded (``max_restart_attempts``, exponential backoff).
When retries are exhausted the profile stays in ``error`` with the last error
recorded — ``error`` requires an explicit restart/recover action to leave, so
the supervisor never spins forever and never silently forgets a dead browser.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from ..api import events
from ..audit import audit_log
from ..config import get_settings
from ..storage import db as _db
from ..storage.state import ProfileState

log = logging.getLogger("multiprofile.browser.supervisor")


def pid_alive(pid: int | None) -> bool:
    """True iff the OS reports ``pid`` as a live, non-zombie process."""
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(pid, 0) but is already dead.
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
            state = f.read().rsplit(")", 1)[-1].split()[0]
        return state != "Z"
    except OSError:
        return True  # /proc unreadable: trust kill() — fail closed toward alive


def _default_launch(profile_id: str) -> dict[str, Any]:
    from .launcher import launch_profile

    return launch_profile(profile_id)


def _default_tile() -> dict[str, Any]:
    """Re-tile the entire grid via CDP (sole tiling authority)."""
    from .layout_manager import get_layout_manager

    return get_layout_manager().tile_running()


class Supervisor:
    """Watches running browsers and bounded-restarts dead ones."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        interval_s: float | None = None,
        max_restart_attempts: int | None = None,
        backoff_base_s: float = 2.0,
        launch_fn: Callable[[str], dict[str, Any]] | None = None,
        tile_fn: Callable[[], dict[str, Any] | None] | None = None,
        pid_alive_fn: Callable[[int | None], bool] | None = None,
    ) -> None:
        settings = get_settings()
        self.enabled = settings.supervisor_enabled if enabled is None else enabled
        self.interval_s = float(
            settings.supervisor_interval_s if interval_s is None else interval_s
        )
        self.max_restart_attempts = (
            settings.supervisor_max_restart_attempts
            if max_restart_attempts is None
            else max_restart_attempts
        )
        self.backoff_base_s = backoff_base_s
        self._launch_fn = launch_fn or _default_launch
        self._tile_fn = tile_fn or _default_tile
        self._pid_alive = pid_alive_fn or pid_alive
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API

    def start(self) -> "Supervisor":
        """Start the supervision loop (no-op when disabled)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            if not self.enabled:
                log.info("supervisor disabled by config; not starting")
                return self
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop, name="mp-supervisor", daemon=True
            )
            self._thread.start()
        log.info(
            "supervisor started (interval=%.1fs, max_restart_attempts=%d)",
            self.interval_s,
            self.max_restart_attempts,
        )
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None
        log.info("supervisor stopped")

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # ----------------------------------------------------------------- loop

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.supervise_once()
            except Exception:  # noqa: BLE001 - supervisor must survive bugs
                log.exception("supervisor iteration failed")
            self._stop_event.wait(self.interval_s)

    def supervise_once(self) -> dict[str, Any]:
        """One watchdog pass over all ``running`` profiles. Returns a summary."""
        checked = 0
        alive = 0
        restarted: list[str] = []
        dead: list[str] = []
        for rt in _db.list_profile_runtimes(state=ProfileState.RUNNING.value):
            profile_id = rt["profile_id"]
            checked += 1
            if self._pid_alive(rt.get("pid")):
                alive += 1
                try:
                    _db.record_heartbeat(profile_id)
                except Exception:  # noqa: BLE001 - heartbeat is best effort
                    log.debug("heartbeat write failed for %s", profile_id, exc_info=True)
                continue
            dead.append(profile_id)
            if self._handle_dead(profile_id, rt):
                restarted.append(profile_id)
        return {
            "checked": checked,
            "alive": alive,
            "dead": dead,
            "restarted": restarted,
        }

    # -------------------------------------------------------------- restart

    def _handle_dead(self, profile_id: str, rt: dict[str, Any]) -> bool:
        """Mark dead, then bounded-restart. Returns True on successful restart.

        The supervisor may restart ONLY an unexpected exit from RUNNING. The
        runtime list was read earlier in this tick, so an explicit Stop/Park
        may have moved the profile out of RUNNING in the meantime — re-read
        the current state and never treat a user stop as a crash.
        """
        current: dict[str, Any] | None = None
        try:
            current = _db.get_profile_runtime(profile_id)
        except Exception:  # noqa: BLE001 - fall through to stale record
            current = None
        current_state = (current or {}).get("state")
        if current_state and current_state != ProfileState.RUNNING.value:
            log.info(
                "supervisor: %s is no longer RUNNING (now %s) — explicit "
                "stop/park/error; not restarting",
                profile_id,
                current_state,
            )
            return False
        pid = rt.get("pid")
        cdp_port = rt.get("cdp_port")
        audit_log(
            "supervisor_browser_died",
            profile_id=profile_id,
            ok=False,
            detail={"pid": pid, "cdp_port": cdp_port, "reason": "browser process died"},
        )
        self._set_state(profile_id, ProfileState.ERROR, note="browser process died")
        log.warning("supervisor: profile %s browser died (pid=%s)", profile_id, pid)

        last_error: str | None = None
        attempts = max(0, int(self.max_restart_attempts))
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                backoff = self.backoff_base_s * (2 ** (attempt - 2))
                log.info(
                    "supervisor: restart attempt %d/%d for %s after %.1fs backoff",
                    attempt,
                    attempts,
                    profile_id,
                    backoff,
                )
                if self._stop_event.wait(backoff):
                    break  # shutting down: stop retrying
            try:
                self._set_state(profile_id, ProfileState.STARTING)
                result = self._launch_fn(profile_id)
            except Exception as e:  # noqa: BLE001 - launcher must not kill the loop
                last_error = f"launcher raised: {e}"
                log.warning("supervisor: relaunch of %s raised: %s", profile_id, e)
                continue
            if isinstance(result, dict) and result.get("ok"):
                log.info("supervisor: relaunch of %s succeeded (attempt %d)", profile_id, attempt)
                self._retile_grid(profile_id)
                return True
            last_error = (
                result.get("error", "launch failed") if isinstance(result, dict) else "launch failed"
            )
            log.warning(
                "supervisor: relaunch of %s failed (attempt %d/%d): %s",
                profile_id,
                attempt,
                attempts,
                last_error,
            )

        # Retries exhausted — park the profile in error; explicit action needed.
        self._set_state(profile_id, ProfileState.ERROR, note=f"restart retries exhausted: {last_error}")
        audit_log(
            "supervisor_restart_exhausted",
            profile_id=profile_id,
            ok=False,
            detail={"attempts": attempts, "last_error": last_error},
        )
        return False

    def _retile_grid(self, profile_id: str) -> None:
        """Re-tile the entire grid via CDP after a successful restart."""
        try:
            tile_result = self._tile_fn()
        except Exception as e:  # noqa: BLE001 - tile failure must not kill the browser
            log.warning("supervisor: re-tile after restart of %s failed: %s", profile_id, e)
            audit_log(
                "supervisor_retile_failed",
                profile_id=profile_id,
                ok=False,
                detail={"error": str(e)},
            )
            return
        ok = bool(tile_result.get("ok")) if isinstance(tile_result, dict) else False
        audit_log(
            "supervisor_retiled",
            profile_id=profile_id,
            ok=ok,
            detail={"tile": tile_result if isinstance(tile_result, dict) else str(tile_result)},
        )

    def _set_state(self, profile_id: str, state: ProfileState, note: str | None = None) -> None:
        try:
            change = _db.set_profile_state(profile_id, state, note=note)
        except Exception:  # noqa: BLE001 - state write must not kill the loop
            log.exception("supervisor: state write failed for %s", profile_id)
            return
        events.publish(
            "profile.status",
            {
                "profile_id": profile_id,
                "old_state": change["old"],
                "new_state": change["new"],
                "note": note,
            },
        )


# Module-level singleton for the running process (started by app wiring).
_supervisor: Supervisor | None = None
_supervisor_lock = threading.Lock()


def start_supervisor(**kwargs: Any) -> Supervisor:
    global _supervisor
    with _supervisor_lock:
        if _supervisor is None or not _supervisor.running:
            _supervisor = Supervisor(**kwargs).start()
        return _supervisor


def stop_supervisor(timeout: float = 10.0) -> None:
    global _supervisor
    with _supervisor_lock:
        sup, _supervisor = _supervisor, None
    if sup is not None:
        sup.stop(timeout=timeout)


def get_supervisor() -> Supervisor | None:
    with _supervisor_lock:
        return _supervisor


__all__ = [
    "Supervisor",
    "pid_alive",
    "start_supervisor",
    "stop_supervisor",
    "get_supervisor",
]
