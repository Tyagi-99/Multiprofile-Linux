"""Linux Chromium sandbox diagnostics and policy.

Do not silently force --no-sandbox. Probe the host and only fall back when
the SUID sandbox / user namespaces are unavailable.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("multiprofile.browser.sandbox")


@dataclass
class SandboxStatus:
    ok: bool
    use_no_sandbox: bool
    reason: str
    chrome_sandbox_path: str | None
    chrome_sandbox_suid: bool
    unprivileged_userns: bool | None
    running_as_root: bool


def _read_userns_sysctl() -> bool | None:
    for path in (
        "/proc/sys/kernel/unprivileged_userns_clone",
        "/proc/sys/kernel/userns_restrict",  # inverted semantics on some kernels; ignore if present alone
    ):
        p = Path(path)
        if not p.exists():
            continue
        try:
            val = p.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if path.endswith("unprivileged_userns_clone"):
            return val == "1"
    return None


def _find_chrome_sandbox(chrome_bin: str) -> Path | None:
    """Locate chrome-sandbox next to the real Chrome binary."""
    try:
        real = Path(chrome_bin).resolve()
    except OSError:
        real = Path(chrome_bin)
    candidates = [
        real.parent / "chrome-sandbox",
        Path("/opt/google/chrome/chrome-sandbox"),
        Path("/usr/lib/chromium/chrome-sandbox"),
        Path("/usr/lib/chromium-browser/chrome-sandbox"),
    ]
    which = shutil.which("chrome-sandbox")
    if which:
        candidates.insert(0, Path(which))
    for c in candidates:
        if c.is_file():
            return c
    return None


def _is_suid_root(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    # SUID bit and owned by root
    return bool(st.st_mode & 0o4000) and st.st_uid == 0


def probe_sandbox(chrome_bin: str) -> SandboxStatus:
    settings = get_settings()
    running_as_root = os.geteuid() == 0
    sandbox_path = _find_chrome_sandbox(chrome_bin)
    suid = bool(sandbox_path and _is_suid_root(sandbox_path))
    userns = _read_userns_sysctl()

    if settings.force_no_sandbox:
        return SandboxStatus(
            ok=False,
            use_no_sandbox=True,
            reason="MULTIPROFILE_FORCE_NO_SANDBOX=1",
            chrome_sandbox_path=str(sandbox_path) if sandbox_path else None,
            chrome_sandbox_suid=suid,
            unprivileged_userns=userns,
            running_as_root=running_as_root,
        )

    if running_as_root:
        return SandboxStatus(
            ok=False,
            use_no_sandbox=True,
            reason="running as root — Chromium sandbox cannot protect root sessions; refuse silent bypass: use a normal user",
            chrome_sandbox_path=str(sandbox_path) if sandbox_path else None,
            chrome_sandbox_suid=suid,
            unprivileged_userns=userns,
            running_as_root=True,
        )

    # Happy path: SUID sandbox present OR user namespaces enabled
    if suid or userns is True:
        return SandboxStatus(
            ok=True,
            use_no_sandbox=False,
            reason="SUID chrome-sandbox present" if suid else "unprivileged user namespaces enabled",
            chrome_sandbox_path=str(sandbox_path) if sandbox_path else None,
            chrome_sandbox_suid=suid,
            unprivileged_userns=userns,
            running_as_root=False,
        )

    return SandboxStatus(
        ok=False,
        use_no_sandbox=True,
        reason=(
            "Chromium sandbox unavailable (missing SUID chrome-sandbox and userns disabled). "
            "Falling back to --no-sandbox. Fix: ensure chrome-sandbox is root:root mode 4755, "
            "or enable kernel.unprivileged_userns_clone=1"
        ),
        chrome_sandbox_path=str(sandbox_path) if sandbox_path else None,
        chrome_sandbox_suid=suid,
        unprivileged_userns=userns,
        running_as_root=False,
    )


def sandbox_chrome_args(chrome_bin: str) -> tuple[list[str], SandboxStatus]:
    """Return extra Chrome args (possibly including --no-sandbox) and status."""
    status = probe_sandbox(chrome_bin)
    args: list[str] = []
    if status.use_no_sandbox:
        args.append("--no-sandbox")
        log.warning("sandbox fallback: %s", status.reason)
    else:
        log.info("sandbox enabled: %s", status.reason)
    return args, status
