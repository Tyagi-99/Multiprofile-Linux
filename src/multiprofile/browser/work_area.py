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



def _from_xrandr_primary() -> WorkArea | None:
    """Prefer the primary connected output geometry (multi-monitor safe)."""
    if not shutil.which("xrandr"):
        return None
    try:
        out = subprocess.check_output(["xrandr", "--current"], text=True, timeout=2, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        return None
    primary = None
    first = None
    for line in out.splitlines():
        # e.g. HDMI-1 connected primary 3440x1440+0+0
        m = re.search(
            r"^(\S+)\s+connected(?:\s+(primary))?\s+(\d+)x(\d+)\+(\d+)\+(\d+)",
            line,
        )
        if not m:
            continue
        w, h, x, y = int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6))
        area = WorkArea(x, y, w, h, f"xrandr:{m.group(1)}")
        if first is None:
            first = area
        if m.group(2):
            primary = area
            break
    chosen = primary or first
    if not chosen or chosen.w < 100 or chosen.h < 100:
        return None
    # Shrink a little for typical top panel if _NET_WORKAREA unavailable later
    return chosen


def intersect_work_areas(a: WorkArea, b: WorkArea, source: str) -> WorkArea | None:
    """Usable rectangle that is on the primary output AND outside panels/docks."""
    x = max(a.x, b.x)
    y = max(a.y, b.y)
    r = min(a.x + a.w, b.x + b.w)
    btm = min(a.y + a.h, b.y + b.h)
    w, h = r - x, btm - y
    if w < 100 or h < 100:
        return None
    return WorkArea(x, y, w, h, source)


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

        xrandr = None
        net = None
        try:
            xrandr = _from_xrandr_primary()
        except Exception as e:
            reasons.append(f"xrandr_primary: {e}")
        try:
            net = _from_net_workarea()
        except Exception as e:
            reasons.append(f"_NET_WORKAREA: {e}")

        if xrandr and net:
            clipped = intersect_work_areas(xrandr, net, f"{xrandr.source}+{net.source}")
            if clipped:
                log.info("work area from primary∩_NET_WORKAREA: %s", clipped)
                return clipped
            reasons.append("primary∩_NET_WORKAREA: empty")
        if xrandr:
            log.info("work area from xrandr_primary: %s", xrandr)
            return xrandr
        if net:
            log.info("work area from _NET_WORKAREA: %s", net)
            return net
        reasons.append("xrandr_primary: unavailable" if xrandr is None else "xrandr_primary: skipped")
        reasons.append("_NET_WORKAREA: unavailable" if net is None else "_NET_WORKAREA: skipped")

        try:
            area = _from_xdpyinfo()
            if area:
                log.info("work area from xdpyinfo: %s", area)
                return area
            reasons.append("xdpyinfo: unavailable")
        except Exception as e:
            reasons.append(f"xdpyinfo: {e}")

        reasons.append(
            "no reliable work area (set MULTIPROFILE_WORKAREA=x,y,w,h). "
            "Refusing silent 1920x1080 fallback."
        )
        raise WorkAreaError(reasons)


def detect_work_area() -> WorkArea:
    """Convenience wrapper matching older import sites."""
    return WorkAreaProvider().detect()
