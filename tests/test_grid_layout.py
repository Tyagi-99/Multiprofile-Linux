"""Geometry unit tests for equal-card SlotPlan (N=1..20, several work areas)."""
from __future__ import annotations

import pytest

from multiprofile.browser.grid import (
    FRAME_GAP_X,
    FRAME_GAP_Y,
    TOP_CHROME_INSET,
    GridCalculator,
    split_axis,
    validate_equal_sizes,
    validate_no_overlap,
    validate_within_work_area,
)
from multiprofile.browser.work_area import WorkArea
from multiprofile.config import get_settings

NS = [1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 20]
EDGE_NS = [4, 6, 9, 12, 16, 20]
AREAS = [
    WorkArea(0, 0, 1366, 768, "test"),
    WorkArea(0, 0, 1920, 1080, "test"),
    WorkArea(0, 0, 2560, 1440, "test"),
    WorkArea(0, 0, 3440, 1440, "test"),
    WorkArea(0, 32, 1920, 1048, "test-panel"),
]


def _by_pos(plan):
    out = {}
    for s in plan.slots:
        out[(s.index // plan.cols, s.index % plan.cols)] = s.rect
    return out


def assert_rows_fill(plan, area, *, gap=0, margin=0):
    """Every row spans the work area. Windows in one row share a size.

    A short last row is wider than the rows above it and still meets both edges.
    Row heights match. Nothing overlaps or leaves the work area.
    """
    rects = [s.rect for s in plan.slots]
    assert len(rects) == len(plan.slots)
    assert validate_no_overlap(rects)["valid"]
    assert validate_within_work_area(rects, area)["valid"]
    ox = area.x + margin
    right = area.x + area.w - margin
    top = area.y + margin + TOP_CHROME_INSET
    bottom = area.y + area.h - margin
    rows: dict[int, list] = {}
    for s in plan.slots:
        rows.setdefault(s.index // plan.cols, []).append(s.rect)
    assert len(rows) == plan.rows
    heights = set()
    prev_bottom = None
    for r in range(plan.rows):
        row = rows[r]
        row.sort(key=lambda rect: rect.x)
        assert row[0].x == ox
        assert row[-1].x + row[-1].w == right
        widths = [rect.w for rect in row]
        assert max(widths) - min(widths) <= 1
        hs = {rect.h for rect in row}
        assert len(hs) == 1
        heights.add(next(iter(hs)))
        for a, b in zip(row, row[1:]):
            assert a.x + a.w + gap + FRAME_GAP_X == b.x
            assert a.y == b.y
        if prev_bottom is None:
            assert row[0].y == top
        else:
            assert row[0].y == prev_bottom + gap + FRAME_GAP_Y
        prev_bottom = row[0].y + row[0].h
    assert max(heights) - min(heights) <= 1
    assert prev_bottom == bottom


@pytest.mark.parametrize("n", NS)
@pytest.mark.parametrize("area", AREAS, ids=lambda a: f"{a.w}x{a.h}+{a.x}+{a.y}")
def test_grid_equal_no_overlap_inside_no_phantoms(n, area):
    calc = GridCalculator()
    ids = [f"p{i}" for i in range(n)]
    plan = calc.plan(ids, area, gap=0, margin=0)
    assert len(plan.slots) == n
    assert plan.cols * plan.rows >= n
    assert_rows_fill(plan, area, gap=0, margin=0)
    if n >= 2 and plan.cols >= 2:
        a, b = plan.slots[0].rect, plan.slots[1].rect
        assert b.x == a.x + a.w + FRAME_GAP_X


@pytest.mark.parametrize("n", EDGE_NS)
@pytest.mark.parametrize("area", AREAS, ids=lambda a: f"{a.w}x{a.h}+{a.x}+{a.y}")
def test_edge_to_edge_consumes_entire_work_area(n, area):
    plan = GridCalculator().plan([f"p{i}" for i in range(n)], area)
    assert plan.gap == 0 and plan.margin == 0
    assert_rows_fill(plan, area)
    occupied = sum(s.rect.w * s.rect.h for s in plan.slots)
    # Frame gaps and the title-bar inset stay empty so borders do not stack.
    assert 0 < occupied < area.w * area.h


def test_split_axis_distributes_remainder_and_touches_edges():
    # 1920 / 7 = 274 remainder 2 → widths 275,275,274,274,274,274,274 or floor-based equivalent
    parts = split_axis(0, 1920, 7, gap=0)
    assert len(parts) == 7
    assert parts[0][0] == 0
    assert parts[-1][0] + parts[-1][1] == 1920
    for i in range(6):
        assert parts[i][0] + parts[i][1] == parts[i + 1][0]
    assert sum(w for _, w in parts) == 1920
    widths = {w for _, w in parts}
    assert widths <= {1920 // 7, 1920 // 7 + 1}


def test_offset_work_area_starts_at_panel_not_origin():
    area = WorkArea(0, 32, 1920, 1048, "test-panel")
    plan = GridCalculator().plan([f"p{i}" for i in range(4)], area)
    assert plan.cols == 2 and plan.rows == 2
    assert_rows_fill(plan, area)
    assert plan.slots[0].rect.y == 32 + TOP_CHROME_INSET
    assert plan.slots[-1].rect.y + plan.slots[-1].rect.h == 1080


def test_gap_still_configurable_between_cells_only():
    area = WorkArea(0, 0, 1920, 1080, "t")
    plan = GridCalculator().plan(["a", "b", "c", "d"], area, gap=6, margin=0)
    assert_rows_fill(plan, area, gap=6, margin=0)


def test_defaults_are_zero_gap_and_margin():
    s = get_settings()
    assert s.window_tile_gap == 0
    assert s.window_tile_margin == 0


def test_sensible_grid_shapes():
    area = WorkArea(0, 0, 1920, 1080, "t")
    calc = GridCalculator()
    # 1920px cannot hold 4 columns at Chrome's 500px minimum, so 12 and 16
    # use 3 columns instead of a 4-across grid that would overlap.
    shapes = {
        4: (2, 2),
        6: (3, 2),
        9: (3, 3),
        12: (3, 4),
        16: (3, 6),
    }
    for n, expected in shapes.items():
        plan = calc.plan([f"p{i}" for i in range(n)], area)
        assert (plan.cols, plan.rows) == expected, (n, plan.cols, plan.rows)


def test_grid_rejects_tiny_area():
    calc = GridCalculator()
    with pytest.raises(ValueError):
        calc.plan(["a", "b", "c"], WorkArea(0, 0, 50, 50, "tiny"), gap=0, margin=0)


# ---------------- v1.8.0: explicit small-count layouts ----------------

WIDE_AREAS = [
    WorkArea(0, 0, 3373, 1408, "wide-3373"),
    WorkArea(0, 0, 1853, 1048, "wide-1853"),
]
TALL_AREAS = [
    WorkArea(0, 0, 1048, 1853, "tall-1048"),
]


@pytest.mark.parametrize("area", WIDE_AREAS + TALL_AREAS, ids=lambda a: f"{a.w}x{a.h}")
def test_one_profile_covers_whole_work_area(area):
    plan = GridCalculator().plan(["solo"], area, gap=0, margin=0)
    assert (plan.cols, plan.rows) == (1, 1)
    r = plan.slots[0].rect
    assert (r.x, r.y, r.w, r.h) == (area.x, area.y + TOP_CHROME_INSET, area.w, area.h - TOP_CHROME_INSET)


@pytest.mark.parametrize("area", WIDE_AREAS, ids=lambda a: f"{a.w}x{a.h}")
def test_two_profiles_side_by_side_on_wide(area):
    plan = GridCalculator().plan(["a", "b"], area, gap=0, margin=0)
    assert (plan.cols, plan.rows) == (2, 1)
    assert_rows_fill(plan, area)
    a, b = plan.slots[0].rect, plan.slots[1].rect
    assert b.x == a.x + a.w + FRAME_GAP_X
    assert a.y == b.y and a.h == b.h


@pytest.mark.parametrize("area", TALL_AREAS, ids=lambda a: f"{a.w}x{a.h}")
def test_two_profiles_stacked_on_tall(area):
    plan = GridCalculator().plan(["a", "b"], area, gap=0, margin=0)
    assert (plan.cols, plan.rows) == (1, 2)
    assert_rows_fill(plan, area)


@pytest.mark.parametrize("area", WIDE_AREAS, ids=lambda a: f"{a.w}x{a.h}")
def test_three_profiles_one_row_on_wide(area):
    plan = GridCalculator().plan(["a", "b", "c"], area, gap=0, margin=0)
    assert (plan.cols, plan.rows) == (3, 1)  # never a 2x2 with a blank cell
    assert_rows_fill(plan, area)
    assert validate_equal_sizes(plan.rects())


def test_eight_profiles_are_four_by_two_on_ultrawide():
    area = WorkArea(67, 32, 3373, 1408, "hdmi")
    plan = GridCalculator().plan([f"p{i}" for i in range(8)], area, gap=0, margin=0)
    assert (plan.cols, plan.rows) == (4, 2)
    assert_rows_fill(plan, area)
    assert validate_equal_sizes(plan.rects())
    assert plan.slots[0].rect.w >= 500


def test_seven_profiles_stretch_the_last_row():
    area = WorkArea(67, 32, 3373, 1408, "hdmi")
    plan = GridCalculator().plan([f"p{i}" for i in range(7)], area, gap=0, margin=0)
    assert (plan.cols, plan.rows) == (4, 2)
    assert_rows_fill(plan, area)
    top = [s.rect for s in plan.slots[:4]]
    bottom = [s.rect for s in plan.slots[4:]]
    assert len(bottom) == 3
    assert validate_equal_sizes(top)
    assert validate_equal_sizes(bottom)
    assert bottom[0].w > top[0].w
    assert abs(bottom[0].h - top[0].h) <= 1
    assert bottom[0].x == area.x
    assert bottom[-1].x + bottom[-1].w == area.x + area.w


@pytest.mark.parametrize("n", list(range(1, 21)))
def test_up_to_twenty_profiles_fill_the_live_screen(n):
    area = WorkArea(67, 32, 3373, 1408, "hdmi")
    plan = GridCalculator().plan([f"p{i}" for i in range(n)], area, gap=0, margin=0)
    assert_rows_fill(plan, area)
    assert min(s.rect.w for s in plan.slots) >= 500


@pytest.mark.parametrize("area", TALL_AREAS, ids=lambda a: f"{a.w}x{a.h}")
def test_three_profiles_one_column_on_tall(area):
    plan = GridCalculator().plan(["a", "b", "c"], area, gap=0, margin=0)
    assert (plan.cols, plan.rows) == (1, 3)
    assert_rows_fill(plan, area)
    assert validate_equal_sizes(plan.rects())
