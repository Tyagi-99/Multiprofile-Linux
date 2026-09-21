"""Desktop work-area detection (fail-loud — never invent 1920x1080)."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger("multiprofile.browser.work_area")


@dataclass(frozen=True)
class WorkArea:
    x: int
    y: int
    w: int
    h: int
    source: str

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "source": self.source}


class WorkAreaError(Exception):
    code = "work_area_unavailable"

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons) if reasons else "work area unavailable")


def _from_env() -> WorkArea | None:
    raw = os.environ.get("MULTIPROFILE_WORKAREA", "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace("x", ",").split(",")]
    if len(parts) != 4:
        raise WorkAreaError([f"MULTIPROFILE_WORKAREA invalid (need x,y,w,h): {raw!r}"])
    try:
        x, y, w, h = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError as e:
        raise WorkAreaError([f"MULTIPROFILE_WORKAREA parse error: {e}"]) from e
    if w < 100 or h < 100:
        raise WorkAreaError([f"MULTIPROFILE_WORKAREA too small: {w}x{h}"])
    return WorkArea(x, y, w, h, "env:MULTIPROFILE_WORKAREA")


def _from_net_workarea() -> WorkArea | None:
    if not shutil.which("xprop"):
        return None
    try:
        out = subprocess.check_output(
            ["xprop", "-root", "_NET_WORKAREA"],
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if "=" not in out:
        return None
    nums = [int(t) for t in re.findall(r"-?\d+", out.split("=", 1)[1])]
    if len(nums) < 4:
        return None
    x, y, w, h = nums[0], nums[1], nums[2], nums[3]
    if w < 100 or h < 100:
        return None
    return WorkArea(x, y, w, h, "xprop:_NET_WORKAREA")


def _from_xdpyinfo() -> WorkArea | None:
    if not shutil.which("xdpyinfo"):
        return None
    try:
        out = subprocess.check_output(["xdpyinfo"], text=True, timeout=2, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("dimensions:"):
            m = re.search(r"(\d+)x(\d+)", line)
            if not m:
                return None
            w, h = int(m.group(1)), int(m.group(2))
            if w < 100 or h < 100:
                return None
            return WorkArea(0, 0, w, h, "xdpyinfo")
    return None


class WorkAreaProvider:
    """Try providers in order; raise WorkAreaError if none succeed."""

    def detect(self) -> WorkArea:
        reasons: list[str] = []
        # Env override first (operator-supplied)
        try:
            env = _from_env()
            if env:
                log.info("work area from env: %s", env)
                return env
        except WorkAreaError:
            raise
        except Exception as e:
            reasons.append(f"env: {e}")

        for name, fn in (
            ("_NET_WORKAREA", _from_net_workarea),
            ("xdpyinfo", _from_xdpyinfo),
        ):
            try:
                area = fn()
                if area:
                    log.info("work area from %s: %s", name, area)
                    return area
                reasons.append(f"{name}: unavailable")
            except Exception as e:
                reasons.append(f"{name}: {e}")

        reasons.append(
            "no reliable work area (set MULTIPROFILE_WORKAREA=x,y,w,h). "
            "Refusing silent 1920x1080 fallback."
        )
        raise WorkAreaError(reasons)


def detect_work_area() -> WorkArea:
    """Convenience wrapper matching older import sites."""
    return WorkAreaProvider().detect()
