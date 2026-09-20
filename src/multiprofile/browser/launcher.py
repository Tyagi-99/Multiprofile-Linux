from __future__ import annotations
import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ..storage.profiles import get_profile, set_runtime
from ..storage.xdg import chrome_profile_dir, data_dir

log = logging.getLogger("multiprofile.browser")

# package-relative extension shipped with the app
def extension_src() -> Path:
    # src/extension when running from source; /usr/share/multiprofile/extension when installed
    candidates = [
        Path(__file__).resolve().parents[2] / "extension",
        Path("/usr/share/multiprofile/extension"),
        Path(__file__).resolve().parents[3] / "src" / "extension",
    ]
    for c in candidates:
        if (c / "manifest.json").exists():
            return c
    raise FileNotFoundError(
        "Anti-detect extension not found. Expected at src/extension or /usr/share/multiprofile/extension"
    )


def find_chrome() -> str:
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    raise FileNotFoundError(
        "Unable to start browser process.\n"
        "Reason: Chromium/Chrome executable was not found on PATH.\n"
        "Suggested action: sudo apt install chromium-browser  OR  install Google Chrome."
    )


def _prepare_extension(profile: dict[str, Any]) -> Path:
    src = extension_src()
    dest = chrome_profile_dir(profile["id"]) / "extension"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    evasion = dest / "evasion.js"
    if evasion.exists():
        text = evasion.read_text(encoding="utf-8", errors="ignore")
        tz = profile.get("timezone") or "America/New_York"
        seed = str(abs(hash(profile["id"])) % 10_000_000)
        ip = profile.get("allocatedIp") or ""
        text = text.replace("__PROFILE_TIMEZONE__", tz)
        text = text.replace("__PROFILE_ALLOCATED_IP__", ip)
        text = text.replace("__PROFILE_SEED__", seed)
        evasion.write_text(text, encoding="utf-8")
    return dest


def launch_profile(profile_id: str, url: str | None = None, direct: bool = True) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        return {"error": f"Profile not found: {profile_id}", "errors": ["profile_not_found"]}

    try:
        chrome = find_chrome()
        ext = _prepare_extension(profile)
    except FileNotFoundError as e:
        return {"error": str(e), "errors": ["dependency_missing"]}

    user_dir = chrome_profile_dir(profile_id) / "user-data"
    user_dir.mkdir(parents=True, exist_ok=True)
    start = url or profile.get("startUrl") or "https://example.com"
    ua = profile.get("userAgent") or ""

    notes = []
    if profile.get("proxyType") not in (None, "", "none") and not direct:
        notes.append(
            f"Proxy type '{profile.get('proxyType')}' is not implemented in v1 native launcher; using direct connection."
        )

    cmd = [
        chrome,
        f"--user-data-dir={user_dir}",
        f"--disable-extensions-except={ext}",
        f"--load-extension={ext}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=ChromeWhatsNewUI",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--new-window",
    ]
    if ua:
        cmd.append(f"--user-agent={ua}")
    cmd.append(start)

    env = os.environ.copy()
    # Prefer existing DISPLAY; do not invent Wayland/X11
    try:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        return {"error": f"Failed to spawn Chrome: {e}", "errors": ["spawn_failed"], "cmd": cmd}

    time.sleep(1.2)
    rc = proc.poll()
    if rc is not None:
        err = (proc.stderr.read() or b"").decode("utf-8", "ignore")[-2500:]
        set_runtime(profile_id, "idle", None)
        return {
            "error": f"Chrome exited immediately with code {rc}",
            "errors": ["chrome_exit_immediate"],
            "stderr": err,
            "cmd": cmd,
            "notes": notes,
        }

    set_runtime(profile_id, "running", proc.pid)
    log.info("launched profile %s pid=%s url=%s", profile_id, proc.pid, start)
    # detach stderr so process can outlive
    try:
        if proc.stderr:
            proc.stderr.close()
    except Exception:
        pass
    return {
        "ok": True,
        "pid": proc.pid,
        "url": start,
        "userDataDir": str(user_dir),
        "extension": str(ext),
        "cmd": cmd,
        "notes": notes,
        "errors": [],
    }


def stop_profile(profile_id: str) -> dict[str, Any]:
    profile = get_profile(profile_id)
    pid = (profile or {}).get("pid")
    # also check runtime file via get_profile merge
    if not pid:
        set_runtime(profile_id, "idle", None)
        return {"ok": True, "stopped": None, "note": "No running pid recorded"}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        return {"error": f"Cannot stop pid {pid}: {e}", "errors": ["permission"]}
    set_runtime(profile_id, "idle", None)
    return {"ok": True, "stopped": pid}
