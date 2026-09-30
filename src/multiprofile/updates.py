"""Automatic update checking, and the decision about automatic installing.

**The requirement and the tension.** `03`'s matrix row is "signed delta
*auto*-update", and everything F12 built so far is something the operator
starts by hand. But **F14** established the opposite principle for this
product: it makes no network requests of its own, and that is a selling point
rather than an oversight. An updater that polls a server on every start is a
network request of its own, and it tells that server an install exists, what
version it is, and roughly when it is used.

Both are right, and the resolution is not a compromise between them:

* **No channel configured, no requests.** That is the default and it is what
  a freshly installed package does. `multiprofile-update` already refuses to
  do anything without `/etc/multiprofile/update.conf`, so an install that was
  handed a `.deb` and nothing else is silent, forever, by construction.
* **Configuring a channel is the opt-in.** Writing that file is a deliberate
  act that says where this install should look. Once it exists, checking on a
  schedule is what the operator asked for, and asking them to also flip a
  second switch would be ceremony.
* **Checking is not installing.** The check reports; `/api/update` and
  `/api/health` carry the answer; nothing is replaced. That is the whole of
  the default behaviour.

**Automatic installing is off by default, deliberately.** This product's job
is to keep browser sessions alive and separate. Replacing the binary under a
running fleet risks exactly what the operator uses it to avoid, and "your
accounts were logged out because the app updated itself at 3am" is not a
trade anyone offered them. When `update.auto_install` is turned on it is still
refused while any profile is running: an update that waits is an update, and
one that interrupts a session is an incident.

**One implementation of the trust chain.** Everything here shells out to
`multiprofile-update`, which verifies the signature, the indices and the
package hash. Re-implementing any of that in-process would mean two things to
keep correct and one of them eventually being wrong; the one that ships in the
package is the one that decides.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import get_settings

log = logging.getLogger("multiprofile.updates")

#: `multiprofile-update --check` exits 10 when a newer version exists. 0 means
#: up to date, 2 means it could not tell (no channel, bad signature, no
#: network). Three outcomes, because "no update" and "could not ask" are
#: different answers and collapsing them is how a stale install looks healthy.
EXIT_UPDATE_AVAILABLE = 10

_lock = threading.RLock()
_state: dict[str, Any] = {
    "checkedAt": None,
    "available": None,
    "installed": None,
    "latest": None,
    "error": None,
    "channelConfigured": None,
}


def _config_path() -> Path:
    return Path(os.environ.get("MULTIPROFILE_UPDATE_CONF", "/etc/multiprofile/update.conf"))


def channel_configured() -> bool:
    """Whether this install has been told where to look for updates."""
    try:
        return _config_path().is_file()
    except OSError:
        return False


def _updater() -> str | None:
    """The updater binary, or None when this is not a packaged install."""
    found = shutil.which("multiprofile-update")
    if found:
        return found
    # Running from a source tree: use the script that would have been shipped,
    # so a developer sees the same behaviour as an installed operator.
    local = Path(__file__).resolve().parents[2] / "packaging" / "update" / "multiprofile-update"
    return str(local) if local.is_file() else None


def check_now(timeout: float = 45.0) -> dict[str, Any]:
    """Ask the channel whether a newer version exists. Never raises."""
    result: dict[str, Any] = {
        "checkedAt": time.time(),
        "available": None,
        "installed": None,
        "latest": None,
        "error": None,
        "channelConfigured": channel_configured(),
    }
    if not result["channelConfigured"]:
        # Not an error: it is the default state, and the one that makes no
        # network requests. Saying so plainly stops it reading as a failure.
        result["error"] = "no update channel configured"
        _store(result)
        return result

    updater = _updater()
    if updater is None:
        result["error"] = "multiprofile-update is not installed"
        _store(result)
        return result

    try:
        proc = subprocess.run(
            [updater, "--check"], capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result["error"] = f"the update check did not answer within {timeout:g}s"
        _store(result)
        return result
    except Exception as e:  # noqa: BLE001 - a check must never take the app down
        result["error"] = str(e)
        _store(result)
        return result

    for line in proc.stdout.splitlines():
        if line.startswith("installed:"):
            value = line.split(":", 1)[1].strip()
            result["installed"] = None if value == "not installed" else value
        elif line.startswith("available:"):
            result["latest"] = line.split(":", 1)[1].strip()

    if proc.returncode == EXIT_UPDATE_AVAILABLE:
        result["available"] = True
    elif proc.returncode == 0:
        result["available"] = False
    else:
        # available stays None: "could not tell" is not "up to date".
        result["error"] = (proc.stderr.strip() or proc.stdout.strip()
                           or f"update check failed (exit {proc.returncode})")
    _store(result)
    return result


def _store(result: dict[str, Any]) -> None:
    with _lock:
        _state.update(result)


def status() -> dict[str, Any]:
    """The last known answer, without asking again."""
    with _lock:
        out = dict(_state)
    out["channelConfigured"] = channel_configured()
    out["updaterPresent"] = _updater() is not None
    settings = get_settings()
    out["checkEnabled"] = bool(settings.update_check_enabled)
    out["autoInstall"] = bool(settings.update_auto_install)
    return out


def download_now(timeout: float = 600.0) -> dict[str, Any]:
    """Fetch and verify the update without installing it. Never raises."""
    updater = _updater()
    if not channel_configured():
        return {"ok": False, "reason": "no_channel"}
    if updater is None:
        return {"ok": False, "reason": "updater_missing"}
    try:
        proc = subprocess.run([updater, "--download"], capture_output=True,
                              text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - a download must never take the app down
        return {"ok": False, "reason": "download_failed", "detail": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "reason": "download_failed",
                "detail": (proc.stderr.strip() or proc.stdout.strip())}
    return {"ok": True, "detail": proc.stdout.strip()}


def install_now(*, require_opt_in: bool = False) -> dict[str, Any]:
    """Install the available update, if that is permitted right now.

    ``require_opt_in`` is True for the scheduled path, where the operator has
    to have turned auto-install on. An explicit POST to /api/update/apply is
    itself the request, so it does not need the setting as well -- refusing a
    button the operator just pressed because a config flag is off would be a
    control that reaches nothing, which is the shape this endpoint used to be.
    """
    settings = get_settings()
    if require_opt_in and not settings.update_auto_install:
        return {"ok": False, "reason": "auto_install_disabled"}
    if not channel_configured():
        return {"ok": False, "reason": "no_channel"}

    # Never under a running fleet. The whole product exists to keep sessions
    # alive; replacing the binary beneath them is the one failure it must not
    # cause itself.
    try:
        from .storage.db import list_profile_runtimes

        running = [r for r in list_profile_runtimes()
                   if str(r.get("state") or "").lower() == "running"]
    except Exception as e:  # noqa: BLE001 - if we cannot tell, we do not install
        log.warning("auto-install: could not determine running profiles: %s", e)
        return {"ok": False, "reason": "runtime_unknown"}
    if running:
        log.info("auto-install deferred: %d profile(s) running", len(running))
        return {"ok": False, "reason": "profiles_running", "running": len(running)}

    updater = _updater()
    if updater is None:
        return {"ok": False, "reason": "updater_missing"}
    if os.geteuid() != 0:
        # dpkg needs root. Rather than fail repeatedly, say so once: the
        # operator has to run the service as root or install by hand.
        return {"ok": False, "reason": "not_root"}

    proc = subprocess.run([updater, "--install", "--yes"],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        log.warning("auto-install failed: %s", proc.stderr.strip())
        return {"ok": False, "reason": "install_failed", "detail": proc.stderr.strip()}
    log.info("auto-install completed")
    return {"ok": True}


def start_update_checker() -> threading.Thread | None:
    """Check on a schedule, in the background. Returns None when it will not.

    Silent by default: without a configured channel there is nothing to ask
    and no request is made, which is the state a freshly installed package is
    in and the one F14's principle requires.
    """
    settings = get_settings()
    if not settings.update_check_enabled:
        log.info("update checking disabled by configuration")
        return None
    if not channel_configured():
        log.info("no update channel configured; not checking (and making no "
                 "network requests)")
        return None

    interval = max(3600, int(settings.update_check_interval_s))

    def _loop() -> None:
        # A short initial delay rather than checking at import: startup is
        # busy, and an update is never urgent to the second.
        time.sleep(30)
        while True:
            try:
                result = check_now()
                if result.get("available"):
                    log.info("update available: %s -> %s",
                             result.get("installed"), result.get("latest"))
                    if get_settings().update_auto_install:
                        install_now(require_opt_in=True)
                elif result.get("error"):
                    log.info("update check: %s", result["error"])
            except Exception as e:  # noqa: BLE001 - the checker must outlive its own bugs
                log.warning("update check failed: %s", e, exc_info=True)
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name="mp-update-check", daemon=True)
    thread.start()
    log.info("update checking enabled: every %ds against the configured channel",
             interval)
    return thread


__all__ = [
    "channel_configured",
    "check_now",
    "download_now",
    "install_now",
    "start_update_checker",
    "status",
]
