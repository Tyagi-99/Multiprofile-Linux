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


def validate_equal_sizes(rects: list[Rect], *, slack: int = 1) -> bool:
    """Nearly equal cards. Remainder pixels may make sizes differ by 1px."""
    if not rects:
        return True
    ws = [r.w for r in rects]
    hs = [r.h for r in rects]
    return max(ws) - min(ws) <= slack and max(hs) - min(hs) <= slack


def split_axis(origin: int, length: int, count: int, gap: int = 0) -> list[tuple[int, int]]:
    """Split `length` into `count` touching (or gapped) segments.

    Remainder pixels are distributed by floor((i+1)*space/count) so the
    first edge is `origin` and the last edge is `origin + length`.
    `gap` is applied only *between* cells, never as outer margin.
    """
    if count <= 0 or length < count:
        return []
    gap = max(0, int(gap))
    cell_space = length - gap * (count - 1)
    if cell_space < count:
        return []
    out: list[tuple[int, int]] = []
    for i in range(count):
        start = origin + i * gap + (i * cell_space) // count
        end = origin + i * gap + ((i + 1) * cell_space) // count
        size = end - start
        if size < 1:
            return []
        out.append((start, size))
    return out


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


def _small_n_slots(profile_ids: list[str], work_area: WorkArea, gap: int, margin: int) -> tuple[int, int, tuple[Slot, ...]] | None:
    """Explicit layouts for n=1..3: fill the work area with no overlap and no
    empty strip. The generic scorer tends to produce stacked/uneven results
    for these small counts, so we own them directly.

    - 1 profile: one rectangle covering the whole work area.
    - 2 profiles: side by side when wider than tall, otherwise stacked.
    - 3 profiles: one row of three on a wide display, otherwise a column.
      Never a 2x2 with a blank cell.
    Returns (cols, rows, slots) or None if n > 3 / not applicable.
    """
    n = len(profile_ids)
    if n < 1 or n > 3:
        return None
    ox = work_area.x + margin
    oy = work_area.y + margin
    ow = work_area.w - 2 * margin
    oh = work_area.h - 2 * margin
    wide = ow >= oh
    if n == 1:
        cols, rows = 1, 1
    elif n == 2:
        cols, rows = (2, 1) if wide else (1, 2)
    else:  # n == 3
        cols, rows = (3, 1) if wide else (1, 3)
    col_parts = split_axis(ox, ow, cols, gap=gap)
    row_parts = split_axis(oy, oh, rows, gap=gap)
    if not col_parts or not row_parts:
        return None
    slots: list[Slot] = []
    for i, pid in enumerate(profile_ids):
        c = i % cols
        r = i // cols
        x, cw = col_parts[c]
        y, ch = row_parts[r]
        if cw < 80 or ch < 60:
            return None
        slots.append(Slot(pid, i, Rect(x, y, cw, ch)))
    return cols, rows, tuple(slots)


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

        # n=1..3 use the explicit full-fill layouts (no 2x2-with-blank-cell).
        if n <= 3:
            explicit = _small_n_slots(profile_ids, work_area, gap, margin)
            if explicit is not None:
                cols, rows, slots = explicit
                rects = [s.rect for s in slots]
                assert validate_no_overlap(rects)["valid"]
                assert validate_within_work_area(rects, work_area)["valid"]
                return SlotPlan(work_area, cols, rows, gap, margin, slots)
            # Fall through to the generic path if the explicit layout fails
            # (e.g. a work area too small for three readable cells).

        best: SlotPlan | None = None
        best_score = float("inf")

        ox = work_area.x + margin
        oy = work_area.y + margin
        ow = work_area.w - 2 * margin
        oh = work_area.h - 2 * margin

        for cols in range(1, n + 1):
            rows = math.ceil(n / cols)
            col_parts = split_axis(ox, ow, cols, gap=gap)
            row_parts = split_axis(oy, oh, rows, gap=gap)
            if not col_parts or not row_parts:
                continue
            cell_w = min(w for _, w in col_parts)
            cell_h = min(h for _, h in row_parts)
            if cell_w < 80 or cell_h < 60:
                continue

            slots: list[Slot] = []
            ok = True
            for i, pid in enumerate(profile_ids):
                c = i % cols
                r = i // cols
                x, cw = col_parts[c]
                y, ch = row_parts[r]
                rect = Rect(x, y, cw, ch)
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
            # Priority 3: nearly equal sizes (remainder may differ by 1px)
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
