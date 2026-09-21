"""Geometry unit tests for equal-card SlotPlan (N=1..20, several work areas)."""
from __future__ import annotations

import pytest

from multiprofile.browser.grid import (
    GridCalculator,
    validate_equal_sizes,
    validate_no_overlap,
    validate_within_work_area,
)
from multiprofile.browser.work_area import WorkArea

NS = [1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 20]
AREAS = [
    WorkArea(0, 0, 1366, 768, "test"),
    WorkArea(0, 0, 1920, 1080, "test"),
    WorkArea(0, 0, 2560, 1440, "test"),
    WorkArea(0, 0, 3440, 1440, "test"),
    WorkArea(0, 32, 1920, 1048, "test-panel"),
]


@pytest.mark.parametrize("n", NS)
@pytest.mark.parametrize("area", AREAS, ids=lambda a: f"{a.w}x{a.h}+{a.x}+{a.y}")
def test_grid_equal_no_overlap_inside_no_phantoms(n, area):
    calc = GridCalculator()
    ids = [f"p{i}" for i in range(n)]
    plan = calc.plan(ids, area, gap=6, margin=0)
    assert len(plan.slots) == n
    rects = [s.rect for s in plan.slots]
    assert validate_equal_sizes(rects)
    assert validate_no_overlap(rects)["valid"]
    assert validate_within_work_area(rects, area)["valid"]
    assert plan.cols * plan.rows >= n
    assert len(plan.slots) == n
    if n >= 2 and plan.cols >= 2:
        a, b = plan.slots[0].rect, plan.slots[1].rect
        assert b.x >= a.x + a.w


def test_grid_rejects_tiny_area():
    calc = GridCalculator()
    with pytest.raises(ValueError):
        calc.plan(["a", "b", "c"], WorkArea(0, 0, 50, 50, "tiny"), gap=6, margin=0)
