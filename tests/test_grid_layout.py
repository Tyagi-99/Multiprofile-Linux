"""Geometry unit tests for equal-card SlotPlan (N=1..20, several work areas)."""
from __future__ import annotations

import pytest

from multiprofile.browser.grid import (
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


def assert_edge_to_edge(plan, area, *, gap=0, margin=0):
    rects = [s.rect for s in plan.slots]
    assert len(rects) == len(plan.slots)
    assert validate_no_overlap(rects)["valid"]
    assert validate_within_work_area(rects, area)["valid"]
    assert validate_equal_sizes(rects)
    cells = _by_pos(plan)
    ox = area.x + margin
    oy = area.y + margin
    right = area.x + area.w - margin
    bottom = area.y + area.h - margin
    for (r, c), rect in cells.items():
        if c == 0:
            assert rect.x == ox
        if r == 0:
            assert rect.y == oy
        if c == plan.cols - 1:
            assert rect.x + rect.w == right
        if r == plan.rows - 1:
            assert rect.y + rect.h == bottom
        nbr = cells.get((r, c + 1))
        if nbr is not None:
            assert rect.x + rect.w + gap == nbr.x
        below = cells.get((r + 1, c))
        if below is not None:
            assert rect.y + rect.h + gap == below.y


@pytest.mark.parametrize("n", NS)
@pytest.mark.parametrize("area", AREAS, ids=lambda a: f"{a.w}x{a.h}+{a.x}+{a.y}")
def test_grid_equal_no_overlap_inside_no_phantoms(n, area):
    calc = GridCalculator()
    ids = [f"p{i}" for i in range(n)]
    plan = calc.plan(ids, area, gap=0, margin=0)
    assert len(plan.slots) == n
    assert plan.cols * plan.rows >= n
    assert_edge_to_edge(plan, area, gap=0, margin=0)
    if n >= 2 and plan.cols >= 2:
        a, b = plan.slots[0].rect, plan.slots[1].rect
        assert b.x == a.x + a.w


@pytest.mark.parametrize("n", EDGE_NS)
@pytest.mark.parametrize("area", AREAS, ids=lambda a: f"{a.w}x{a.h}+{a.x}+{a.y}")
def test_edge_to_edge_consumes_entire_work_area(n, area):
    plan = GridCalculator().plan([f"p{i}" for i in range(n)], area)
    assert plan.gap == 0 and plan.margin == 0
    assert_edge_to_edge(plan, area)
    occupied = sum(s.rect.w * s.rect.h for s in plan.slots)
    if n == plan.cols * plan.rows:
        assert occupied == area.w * area.h


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
    assert_edge_to_edge(plan, area)
    assert plan.slots[0].rect.y == 32
    assert plan.slots[-1].rect.y + plan.slots[-1].rect.h == 1080


def test_gap_still_configurable_between_cells_only():
    area = WorkArea(0, 0, 1920, 1080, "t")
    plan = GridCalculator().plan(["a", "b", "c", "d"], area, gap=6, margin=0)
    assert_edge_to_edge(plan, area, gap=6, margin=0)


def test_defaults_are_zero_gap_and_margin():
    s = get_settings()
    assert s.window_tile_gap == 0
    assert s.window_tile_margin == 0


def test_sensible_grid_shapes():
    area = WorkArea(0, 0, 1920, 1080, "t")
    calc = GridCalculator()
    shapes = {
        4: (2, 2),
        6: (3, 2),
        9: (3, 3),
        12: (4, 3),
        16: (4, 4),
    }
    for n, expected in shapes.items():
        plan = calc.plan([f"p{i}" for i in range(n)], area)
        assert (plan.cols, plan.rows) == expected, (n, plan.cols, plan.rows)


def test_grid_rejects_tiny_area():
    calc = GridCalculator()
    with pytest.raises(ValueError):
        calc.plan(["a", "b", "c"], WorkArea(0, 0, 50, 50, "tiny"), gap=0, margin=0)
