"""Same inputs → identical SlotPlan (idempotent tile geometry)."""
from __future__ import annotations

from multiprofile.browser.grid import GridCalculator
from multiprofile.browser.work_area import WorkArea


def test_plan_idempotent_three_times():
    calc = GridCalculator()
    area = WorkArea(0, 32, 1920, 1048, "test")
    ids = [f"p{i}" for i in range(9)]
    p1 = calc.plan(ids, area, gap=6, margin=0)
    p2 = calc.plan(ids, area, gap=6, margin=0)
    p3 = calc.plan(ids, area, gap=6, margin=0)
    assert [(s.profile_id, s.index, s.rect) for s in p1.slots] == [
        (s.profile_id, s.index, s.rect) for s in p2.slots
    ]
    assert [(s.profile_id, s.index, s.rect) for s in p2.slots] == [
        (s.profile_id, s.index, s.rect) for s in p3.slots
    ]
    assert p1.cols == p2.cols == p3.cols
    assert p1.rows == p2.rows == p3.rows


def test_plan_stable_order():
    calc = GridCalculator()
    area = WorkArea(0, 0, 1920, 1080, "test")
    ids = ["z", "a", "m"]
    plan = calc.plan(ids, area)
    assert [s.profile_id for s in plan.slots] == ids
