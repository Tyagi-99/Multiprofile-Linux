"""Equal-card grid SlotPlan calculator (pure math — no Chrome)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .work_area import WorkArea
from ..config import get_settings


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "left": self.x, "top": self.y, "width": self.w, "height": self.h}


@dataclass(frozen=True)
class Slot:
    profile_id: str
    index: int
    rect: Rect


@dataclass(frozen=True)
class SlotPlan:
    work_area: WorkArea
    cols: int
    rows: int
    gap: int
    margin: int
    slots: tuple[Slot, ...]

    def rects(self) -> list[Rect]:
        return [s.rect for s in self.slots]


def slot_rect_map(plan: SlotPlan | None) -> dict[str, Rect]:
    if plan is None:
        return {}
    return {s.profile_id: s.rect for s in plan.slots}


def _overlap(a: Rect, b: Rect) -> bool:
    return not (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)


def validate_no_overlap(rects: list[Rect]) -> dict:
    overlaps = []
    for i, a in enumerate(rects):
        for j in range(i + 1, len(rects)):
            b = rects[j]
            if _overlap(a, b):
                overlaps.append({"a": i, "b": j})
    return {"valid": not overlaps, "overlaps": overlaps}


def validate_within_work_area(rects: list[Rect], area: WorkArea) -> dict:
    off = []
    for i, r in enumerate(rects):
        if r.x < area.x or r.y < area.y or r.x + r.w > area.x + area.w or r.y + r.h > area.y + area.h:
            off.append(i)
    return {"valid": not off, "offscreen": off}


def validate_equal_sizes(rects: list[Rect]) -> bool:
    if not rects:
        return True
    w0, h0 = rects[0].w, rects[0].h
    return all(r.w == w0 and r.h == h0 for r in rects)


def _score(cols: int, rows: int, n: int, area: WorkArea, cell_w: int, cell_h: int) -> float:
    """Lower is better. Priorities 4–6 only (1–3 already enforced by rejection)."""
    empty = cols * rows - n
    aspect = (area.w / area.h) if area.h else 1.6
    cell_aspect = (cell_w / cell_h) if cell_h else 1.0
    # Prefer browser-ish aspect ~1.35
    preferred = 1.35
    aspect_pen = abs(math.log((cell_aspect + 1e-6) / preferred))
    util = (cell_w * cell_h * n) / max(1, area.w * area.h)
    # Prefer grids whose cols/rows roughly match screen aspect
    grid_aspect = cols / rows
    grid_pen = abs(math.log((grid_aspect + 1e-6) / (aspect + 1e-6))) * 0.25
    return empty * 1.5 + aspect_pen + grid_pen - util


class GridCalculator:
    """Sole pure-math owner of slot rectangles for a given profile id list."""

    def plan(
        self,
        profile_ids: list[str],
        work_area: WorkArea,
        *,
        gap: int | None = None,
        margin: int | None = None,
    ) -> SlotPlan:
        settings = get_settings()
        gap = settings.window_tile_gap if gap is None else gap
        margin = settings.window_tile_margin if margin is None else margin
        n = len(profile_ids)
        if n <= 0:
            return SlotPlan(work_area, 0, 0, gap, margin, ())

        best: SlotPlan | None = None
        best_score = float("inf")

        for cols in range(1, n + 1):
            rows = math.ceil(n / cols)
            inner_w = work_area.w - 2 * margin - gap * (cols + 1)
            inner_h = work_area.h - 2 * margin - gap * (rows + 1)
            if inner_w < n or inner_h < 1:
                continue
            cell_w = inner_w // cols
            cell_h = inner_h // rows
            if cell_w < 80 or cell_h < 60:
                continue

            slots: list[Slot] = []
            ok = True
            for i, pid in enumerate(profile_ids):
                c = i % cols
                r = i // cols
                x = work_area.x + margin + gap + c * (cell_w + gap)
                y = work_area.y + margin + gap + r * (cell_h + gap)
                rect = Rect(x, y, cell_w, cell_h)
                # Priority 2: fully inside
                if (
                    rect.x < work_area.x
                    or rect.y < work_area.y
                    or rect.x + rect.w > work_area.x + work_area.w
                    or rect.y + rect.h > work_area.y + work_area.h
                ):
                    ok = False
                    break
                slots.append(Slot(pid, i, rect))
            if not ok:
                continue
            rects = [s.rect for s in slots]
            # Priority 1: no overlap
            if not validate_no_overlap(rects)["valid"]:
                continue
            # Priority 3: equal sizes (by construction)
            if not validate_equal_sizes(rects):
                continue
            score = _score(cols, rows, n, work_area, cell_w, cell_h)
            if score < best_score:
                best_score = score
                best = SlotPlan(work_area, cols, rows, gap, margin, tuple(slots))

        if best is None:
            raise ValueError(
                f"no valid equal-card grid for n={n} in work area "
                f"{work_area.w}x{work_area.h} (gap={gap}, margin={margin})"
            )
        return best
