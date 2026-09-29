"""Desktop work-area detection (fail-loud — never invent 1920x1080).

Detectors are tried in order and the first usable answer wins. Historically
all of them were X11 tools -- ``xrandr``, ``_NET_WORKAREA`` via ``xprop``,
``xdpyinfo`` -- which meant that on a Wayland session with no X11 utilities
installed every one failed and window tiling silently stopped working (F13).
Measured on this project's own development host, Hyprland on Wayland::

    xrandr_primary: unavailable
    _NET_WORKAREA:  unavailable
    xdpyinfo:       unavailable

`xrandr` and `xdpyinfo` were not installed at all, so no amount of XWayland
would have helped. The Wayland detectors below are tried first when the
session is Wayland, because asking the compositor is both more accurate than
XWayland's view and the only thing that works when the X11 tools are absent.

Refusing to invent a fallback size stays the right behaviour: a wrong work
area tiles windows off-screen, which is worse than not tiling. What changed is
that the failure now explains itself.
"""
from __future__ import annotations

import json
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


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or (
        os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"
    )


def _logical(value: float, scale: float) -> int:
    """Physical pixels to logical pixels.

    Compositors report a monitor's pixel size and its scale separately, but a
    window is positioned in logical coordinates. Ignoring scale on a 2x
    display would place every window in the top-left quadrant.
    """
    if scale and scale > 0:
        return int(round(value / scale))
    return int(round(value))


def _apply_reserved(
    x: int, y: int, w: int, h: int, reserved: list[int] | None, source: str
) -> WorkArea | None:
    """Subtract the compositor's reserved edges (bars, docks).

    ``reserved`` is [left, top, right, bottom], the same order wlroots
    compositors report. This is the Wayland equivalent of ``_NET_WORKAREA``:
    without it a tiled window sits under the status bar.
    """
    left, top, right, bottom = (list(reserved) + [0, 0, 0, 0])[:4] if reserved else (0, 0, 0, 0)
    x2, y2 = x + int(left), y + int(top)
    w2, h2 = w - int(left) - int(right), h - int(top) - int(bottom)
    if w2 < 100 or h2 < 100:
        return None
    return WorkArea(x2, y2, w2, h2, source)


def _run_json(cmd: list[str]):
    try:
        out = subprocess.check_output(
            cmd, text=True, timeout=2, stderr=subprocess.DEVNULL
        )
    except (subprocess.SubprocessError, OSError):
        return None
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return None


def _from_hyprland() -> WorkArea | None:
    """Hyprland, via ``hyprctl monitors -j``.

    Reports x/y/width/height in physical pixels, ``scale``, and ``reserved``
    as [left, top, right, bottom]. The focused monitor is used, because that
    is where the operator is looking and where a launched window lands.
    """
    if not shutil.which("hyprctl"):
        return None
    monitors = _run_json(["hyprctl", "monitors", "-j"])
    if not isinstance(monitors, list) or not monitors:
        return None
    chosen = next((m for m in monitors if m.get("focused")), monitors[0])
    try:
        scale = float(chosen.get("scale") or 1) or 1.0
        x = _logical(float(chosen.get("x") or 0), scale)
        y = _logical(float(chosen.get("y") or 0), scale)
        w = _logical(float(chosen["width"]), scale)
        h = _logical(float(chosen["height"]), scale)
    except (KeyError, TypeError, ValueError):
        return None
    # `reserved` is already in logical pixels.
    return _apply_reserved(x, y, w, h, chosen.get("reserved"), "hyprctl")


def _from_sway() -> WorkArea | None:
    """Sway, via ``swaymsg -t get_outputs``.

    Reports a logical ``rect`` already, so no scale arithmetic, and it is the
    only one of these that does not expose reserved edges -- sway's bar is a
    layer-shell surface it does not report here. The full output rectangle is
    returned, which may sit under a bar; that is still far better than not
    tiling at all, and the source name says where it came from.
    """
    if not shutil.which("swaymsg"):
        return None
    outputs = _run_json(["swaymsg", "-t", "get_outputs", "-r"])
    if not isinstance(outputs, list) or not outputs:
        return None
    chosen = next((o for o in outputs if o.get("focused")), None) or next(
        (o for o in outputs if o.get("active")), outputs[0]
    )
    rect = chosen.get("rect") or {}
    try:
        x, y = int(rect["x"]), int(rect["y"])
        w, h = int(rect["width"]), int(rect["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if w < 100 or h < 100:
        return None
    return WorkArea(x, y, w, h, "swaymsg")


def _from_wlr_randr() -> WorkArea | None:
    """Generic wlroots compositors, via ``wlr-randr --json``."""
    if not shutil.which("wlr-randr"):
        return None
    outputs = _run_json(["wlr-randr", "--json"])
    if not isinstance(outputs, list) or not outputs:
        return None
    chosen = next((o for o in outputs if o.get("enabled")), outputs[0])
    mode = next(
        (m for m in (chosen.get("modes") or []) if m.get("current")),
        None,
    )
    if not mode:
        return None
    try:
        scale = float(chosen.get("scale") or 1) or 1.0
        pos = chosen.get("position") or {}
        x = _logical(float(pos.get("x") or 0), scale)
        y = _logical(float(pos.get("y") or 0), scale)
        w = _logical(float(mode["width"]), scale)
        h = _logical(float(mode["height"]), scale)
    except (KeyError, TypeError, ValueError):
        return None
    if w < 100 or h < 100:
        return None
    return WorkArea(x, y, w, h, "wlr-randr")


#: Wayland detectors, in order of how much they know. Hyprland and wlr-randr
#: report reserved edges or scale; sway reports neither but is still an answer.
_WAYLAND_DETECTORS = (
    ("hyprctl", _from_hyprland),
    ("swaymsg", _from_sway),
    ("wlr-randr", _from_wlr_randr),
)


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

        # Wayland first, when this is a Wayland session. The compositor's own
        # answer beats XWayland's view of it, and on a host with no X11
        # utilities installed it is the only answer there is (F13).
        if _is_wayland():
            for name, detector in _WAYLAND_DETECTORS:
                try:
                    area = detector()
                except Exception as e:  # noqa: BLE001 - try the next detector
                    reasons.append(f"{name}: {e}")
                    continue
                if area:
                    log.info("work area from %s: %s", name, area)
                    return area
                reasons.append(f"{name}: unavailable")

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

        # Name the actual situation rather than only the remedy. "Set this
        # variable" does not tell a Wayland user that the three things that
        # failed were X11 tools they do not have and do not want.
        if _is_wayland() and not any(
            shutil.which(tool) for tool, _ in _WAYLAND_DETECTORS
        ):
            reasons.append(
                "this is a Wayland session and none of "
                f"{', '.join(t for t, _ in _WAYLAND_DETECTORS)} is installed; "
                "the X11 detectors above cannot see a Wayland compositor"
            )
        reasons.append(
            "no reliable work area (set MULTIPROFILE_WORKAREA=x,y,w,h). "
            "Refusing silent 1920x1080 fallback -- a wrong work area tiles "
            "windows off-screen, which is worse than not tiling."
        )
        raise WorkAreaError(reasons)


def detect_work_area() -> WorkArea:
    """Convenience wrapper matching older import sites."""
    return WorkAreaProvider().detect()
