"""Equal-card grid SlotPlan calculator (pure math — no Chrome)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import get_settings
from .work_area import WorkArea


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


# Chrome on this machine grows any window narrower than 500px up to 500,
# which makes neighbors overlap. Never request a full-row cell that narrow
# when the screen can hold fewer, wider columns instead.
CHROME_MIN_WINDOW_W = 500
# Mutter reports the client area ~37px below the top we request, and keeps
# the requested height, so a window placed on the work-area top spills into
# the row below. Leave this strip for that title bar.
TOP_CHROME_INSET = 40
# Client rectangles from CDP do not include the frame. Flush clients make
# title bars and borders paint on top of the next window.
FRAME_GAP_X = 1
FRAME_GAP_Y = 1
# Prefer a browser-like cell. Last-row leftovers are stretched, not left blank.
_TARGET_CELL_ASPECT = 1.45


def _choose_cols(n: int, ow: int, oh: int) -> int:
    """Column count for ``n`` windows on a ``ow``×``oh`` content area.

    A full row uses this many equal windows. A short last row uses fewer
    windows stretched across the same width. Columns are capped so a full-row
    cell stays at least ``CHROME_MIN_WINDOW_W`` wide when the screen allows it.
    """
    if n <= 1 or ow <= 0 or oh <= 0:
        return 1
    max_cols = max(1, min(n, ow // CHROME_MIN_WINDOW_W))
    best_cols = 1
    best_score = float("inf")
    for cols in range(1, max_cols + 1):
        rows = math.ceil(n / cols)
        cw = ow / cols
        ch = oh / rows
        if cw < CHROME_MIN_WINDOW_W - 1 or ch < 28:
            continue
        aspect = cw / ch if ch else 1.0
        aspect_pen = abs(math.log((aspect + 1e-6) / _TARGET_CELL_ASPECT))
        last = n - cols * (rows - 1)
        leftover_pen = 0.0
        if rows > 1 and last < cols:
            leftover_pen = (1 - last / cols) * 0.35
            if last == 1 and n > 3:
                leftover_pen += 0.8
        short_pen = 1.5 if ch < 160 else 0.0
        score = aspect_pen + leftover_pen + short_pen
        if score < best_score:
            best_score = score
            best_cols = cols
    return best_cols


def _row_slots(
    profile_ids: list[str],
    work_area: WorkArea,
    cols: int,
    gap: int,
    margin: int,
) -> tuple[Slot, ...] | None:
    """Place windows in ``cols``-wide rows. The last row stretches to the edges."""
    n = len(profile_ids)
    if n <= 0 or cols <= 0:
        return None
    rows = math.ceil(n / cols)
    ox = work_area.x + margin
    oy = work_area.y + margin + TOP_CHROME_INSET
    ow = work_area.w - 2 * margin
    oh = work_area.h - 2 * margin - TOP_CHROME_INSET
    row_parts = split_axis(oy, oh, rows, gap=gap + FRAME_GAP_Y)
    if not row_parts:
        return None
    slots: list[Slot] = []
    cursor = 0
    for r in range(rows):
        count = cols if r < rows - 1 else n - cols * (rows - 1)
        col_parts = split_axis(ox, ow, count, gap=gap + FRAME_GAP_X)
        if not col_parts or len(col_parts) != count:
            return None
        y, ch = row_parts[r]
        if ch < 28:
            return None
        for c in range(count):
            x, cw = col_parts[c]
            if cw < 80:
                return None
            rect = Rect(x, y, cw, ch)
            if (
                rect.x < work_area.x
                or rect.y < work_area.y
                or rect.x + rect.w > work_area.x + work_area.w
                or rect.y + rect.h > work_area.y + work_area.h
            ):
                return None
            slots.append(Slot(profile_ids[cursor], cursor, rect))
            cursor += 1
    if cursor != n:
        return None
    rects = [s.rect for s in slots]
    if not validate_no_overlap(rects)["valid"]:
        return None
    if not validate_within_work_area(rects, work_area)["valid"]:
        return None
    return tuple(slots)


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
    oy = work_area.y + margin + TOP_CHROME_INSET
    ow = work_area.w - 2 * margin
    oh = work_area.h - 2 * margin - TOP_CHROME_INSET
    wide = ow >= oh
    if n == 1:
        cols, rows = 1, 1
    elif n == 2:
        cols, rows = (2, 1) if wide else (1, 2)
    else:  # n == 3
        cols, rows = (3, 1) if wide else (1, 3)
    col_parts = split_axis(ox, ow, cols, gap=gap + FRAME_GAP_X)
    row_parts = split_axis(oy, oh, rows, gap=gap + FRAME_GAP_Y)
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

        ow = work_area.w - 2 * margin
        # 1–3 stay one row (or one column on a tall screen) when each window
        # is still wide enough for Chrome. Otherwise fall through so a short
        # last row can stretch instead of overlapping.
        if n <= 3:
            explicit = _small_n_slots(profile_ids, work_area, gap, margin)
            if explicit is not None:
                cols, rows, slots = explicit
                narrow = ow >= CHROME_MIN_WINDOW_W and any(s.rect.w < CHROME_MIN_WINDOW_W for s in slots)
                if not narrow:
                    return SlotPlan(work_area, cols, rows, gap, margin, slots)

        cols = _choose_cols(n, ow, work_area.h - 2 * margin - TOP_CHROME_INSET)
        slots = _row_slots(profile_ids, work_area, cols, gap, margin)
        if slots is None:
            raise ValueError(
                f"no valid grid for n={n} in work area "
                f"{work_area.w}x{work_area.h} (gap={gap}, margin={margin})"
            )
        rows = math.ceil(n / cols)
        return SlotPlan(work_area, cols, rows, gap, margin, slots)
