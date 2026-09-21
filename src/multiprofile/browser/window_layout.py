"""Deterministic non-overlapping window tiling for multi-profile launches."""
from __future__ import annotations

import logging
import math
import os
import shutil
import subprocess
from dataclasses import dataclass

from ..config import get_settings

log = logging.getLogger("multiprofile.browser.window_layout")


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class WorkArea:
    x: int
    y: int
    w: int
    h: int
    source: str


def detect_work_area() -> WorkArea:
    """Compatibility wrapper — delegates to WorkAreaProvider (fail-loud, no silent 1920x1080)."""
    from .work_area import WorkAreaProvider

    area = WorkAreaProvider().detect()
    return WorkArea(area.x, area.y, area.w, area.h, area.source)


def choose_grid(n: int, area: WorkArea) -> tuple[int, int]:
    """Pick (cols, rows) minimizing waste while keeping reasonable cell aspect."""
    n = max(1, n)
    if n == 1:
        return 1, 1
    aspect = (area.w / area.h) if area.h else 1.6
    best = (n, 1)
    best_score = float("inf")
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cell_w = area.w / cols
        cell_h = area.h / rows
        cell_aspect = cell_w / cell_h if cell_h else 1.0
        # Prefer grids whose cell aspect is near screen aspect; penalize empty slots
        empty = cols * rows - n
        score = abs(math.log((cell_aspect + 1e-6) / (aspect + 1e-6))) + empty * 0.15
        # Mild preference for fewer rows on wide screens
        score += abs(cols / rows - aspect) * 0.05
        if score < best_score:
            best_score = score
            best = (cols, rows)
    return best


def compute_layout(n: int, area: WorkArea | None = None, gap: int | None = None) -> list[Rect]:
    """Deprecated for tiling authority — prefer WindowLayoutManager / GridCalculator.
    Still used only as a pure math helper; detect_work_area() is fail-loud.
    """
    settings = get_settings()
    area = area or detect_work_area()
    gap = settings.window_tile_gap if gap is None else gap
    n = max(1, n)
    cols, rows = choose_grid(n, area)
    usable_w = max(100, area.w - gap * (cols + 1))
    usable_h = max(100, area.h - gap * (rows + 1))
    cell_w = usable_w // cols
    cell_h = usable_h // rows
    rects: list[Rect] = []
    for i in range(n):
        c = i % cols
        r = i // cols
        x = area.x + gap + c * (cell_w + gap)
        y = area.y + gap + r * (cell_h + gap)
        rects.append(Rect(x, y, cell_w, cell_h))
    return rects


def chrome_geometry_args(rect: Rect) -> list[str]:
    return [
        f"--window-position={rect.x},{rect.y}",
        f"--window-size={rect.w},{rect.h}",
    ]


def place_window_by_pid(pid: int, rect: Rect) -> dict:
    """UNUSED by launch/tile path (CDP-only Approach A). Kept for emergency diagnostics only."""
    result = {"ok": False, "method": None, "error": None, "pid": pid, "rect": rect.__dict__}
    # Prefer xdotool search by pid
    if shutil.which("xdotool"):
        try:
            out = subprocess.check_output(
                ["xdotool", "search", "--pid", str(pid)],
                text=True,
                timeout=3,
                stderr=subprocess.DEVNULL,
            )
            wids = [w.strip() for w in out.splitlines() if w.strip()]
            if wids:
                wid = wids[-1]
                subprocess.check_call(
                    ["xdotool", "windowactivate", "--sync", wid],
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.check_call(
                    ["xdotool", "windowmove", wid, str(rect.x), str(rect.y)],
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.check_call(
                    ["xdotool", "windowsize", wid, str(rect.w), str(rect.h)],
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                result["ok"] = True
                result["method"] = "xdotool"
                return result
        except (subprocess.SubprocessError, OSError) as e:
            result["error"] = f"xdotool: {e}"

    if shutil.which("wmctrl"):
        try:
            # wmctrl -l -p → window_id desktop pid host title
            out = subprocess.check_output(["wmctrl", "-l", "-p"], text=True, timeout=3)
            wid = None
            for line in out.splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 3 and parts[2] == str(pid):
                    wid = parts[0]
                    break
            if wid:
                # gravity,x,y,w,h
                spec = f"0,{rect.x},{rect.y},{rect.w},{rect.h}"
                subprocess.check_call(
                    ["wmctrl", "-i", "-r", wid, "-e", spec],
                    timeout=3,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                result["ok"] = True
                result["method"] = "wmctrl"
                return result
            result["error"] = "wmctrl: pid window not found"
        except (subprocess.SubprocessError, OSError) as e:
            result["error"] = f"wmctrl: {e}"

    if not result["ok"] and not result["error"]:
        result["error"] = "no xdotool/wmctrl available; relying on Chrome --window-position/--window-size only"
    return result
