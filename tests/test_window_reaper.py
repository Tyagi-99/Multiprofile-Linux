"""One window per profile — reap policy, ownership guard, and tiling wiring."""
from __future__ import annotations

import os

import pytest

from multiprofile.browser.grid import Rect
from multiprofile.browser.layout_manager import WindowLayoutManager
from multiprofile.browser.runtime_state import clear_all_runtimes, upsert_runtime
from multiprofile.browser.window_reaper import (
    WindowGroup,
    chrome_owns_profile_dir,
    group_windows,
    plan_reap,
)

# --------------------------------------------------------------------------
# grouping
# --------------------------------------------------------------------------


def test_group_windows_keeps_window_and_tab_order():
    groups = group_windows(
        [
            ("t1", 10, "https://a.test"),
            ("t2", 11, "https://b.test"),
            ("t3", 10, "https://c.test"),
        ]
    )
    assert [g.window_id for g in groups] == [10, 11]
    assert groups[0].target_ids == ("t1", "t3")
    assert groups[0].urls == ("https://a.test", "https://c.test")
    assert groups[1].tab_count == 1


def test_group_windows_drops_blank_target_ids():
    assert group_windows([("", 1, "x"), ("t1", 1, "y")]) == [
        WindowGroup(window_id=1, target_ids=("t1",), urls=("y",))
    ]


# --------------------------------------------------------------------------
# policy — what survives
# --------------------------------------------------------------------------


def test_single_window_closes_nothing():
    plan = plan_reap([WindowGroup(7, ("t1",), ("https://a.test",))], preferred_target_id="t1")
    assert plan.is_noop
    assert plan.close_target_ids == ()
    assert plan.keep_window_id == 7
    assert plan.keep_target_id == "t1"
    assert plan.reason == "single_window"


def test_no_windows_is_a_noop():
    plan = plan_reap([], preferred_target_id=None)
    assert plan.is_noop
    assert plan.keep_window_id is None
    assert plan.reason == "no_windows"


def test_window_with_empty_target_list_is_ignored():
    plan = plan_reap([WindowGroup(1, ()), WindowGroup(2, ("t1",))], preferred_target_id="t1")
    assert plan.is_noop
    assert plan.keep_window_id == 2


def test_session_restore_extras_are_closed_and_fullest_window_survives():
    """Our --new-window start page must not evict the operator's restored tabs."""
    restored = WindowGroup(20, ("r1", "r2", "r3"), ("https://x.test",) * 3)
    fresh = WindowGroup(21, ("f1",), ("https://example.com",))
    plan = plan_reap([restored, fresh], preferred_target_id="f1")

    assert plan.keep_window_id == 20
    assert plan.keep_target_id == "r1"
    assert plan.close_target_ids == ("f1",)
    assert plan.close_window_ids == (21,)
    assert not plan.is_noop


def test_tie_on_tab_count_prefers_the_window_the_tiler_picked():
    a = WindowGroup(30, ("a1", "a2"))
    b = WindowGroup(31, ("b1", "b2"))
    plan = plan_reap([a, b], preferred_target_id="b1")
    assert plan.keep_window_id == 31
    assert plan.keep_target_id == "b1"
    assert plan.close_target_ids == ("a1", "a2")


def test_tie_without_preference_is_deterministic_lowest_window_id():
    groups = [WindowGroup(41, ("b1",)), WindowGroup(40, ("a1",))]
    first = plan_reap(groups, preferred_target_id=None)
    second = plan_reap(list(reversed(groups)), preferred_target_id=None)
    assert first.keep_window_id == second.keep_window_id == 40


def test_three_windows_collapse_to_one():
    plan = plan_reap(
        [WindowGroup(1, ("a",)), WindowGroup(2, ("b", "c")), WindowGroup(3, ("d",))],
        preferred_target_id="a",
    )
    assert plan.keep_window_id == 2
    assert sorted(plan.close_target_ids) == ["a", "d"]
    assert sorted(plan.close_window_ids) == [1, 3]


def test_kept_target_is_always_inside_the_kept_window():
    plan = plan_reap(
        [WindowGroup(1, ("a", "b")), WindowGroup(2, ("c",))],
        preferred_target_id="c",  # preference lives in the window we close
    )
    assert plan.keep_window_id == 1
    assert plan.keep_target_id in ("a", "b")


# --------------------------------------------------------------------------
# ownership guard — never touch a Chrome that is not ours
# --------------------------------------------------------------------------


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


def _fake_proc(tmp_path, monkeypatch, pid: int, args: list[str]) -> None:
    """Stand in for /proc/<pid>/cmdline with a NUL-separated argv."""
    real_read_bytes = type(tmp_path).read_bytes
    blob = "\0".join(args).encode()

    def fake_read_bytes(self):
        if self.as_posix() == f"/proc/{pid}/cmdline":
            return blob
        return real_read_bytes(self)

    monkeypatch.setattr(type(tmp_path), "read_bytes", fake_read_bytes)


def test_owns_dir_accepts_our_own_profile_chrome(xdg, monkeypatch):
    from multiprofile.storage.xdg import chrome_profile_dir

    udd = chrome_profile_dir("prof-a") / "user-data"
    udd.mkdir(parents=True, exist_ok=True)
    _fake_proc(xdg, monkeypatch, 4242, ["/usr/bin/google-chrome-stable", f"--user-data-dir={udd}"])

    owned, why = chrome_owns_profile_dir(4242, "prof-a")
    assert owned is True
    assert "prof-a" in why


def test_owns_dir_rejects_another_profiles_chrome(xdg, monkeypatch):
    from multiprofile.storage.xdg import chrome_profile_dir

    other = chrome_profile_dir("prof-b") / "user-data"
    other.mkdir(parents=True, exist_ok=True)
    _fake_proc(xdg, monkeypatch, 4243, ["/usr/bin/google-chrome-stable", f"--user-data-dir={other}"])

    owned, why = chrome_owns_profile_dir(4243, "prof-a")
    assert owned is False
    assert "does not belong to profile prof-a" in why


def test_owns_dir_rejects_the_operators_personal_chrome(xdg, monkeypatch):
    """~/.config/google-chrome is never under chrome-profiles/ and must be refused."""
    _fake_proc(
        xdg,
        monkeypatch,
        4244,
        ["/usr/bin/google-chrome-stable", f"--user-data-dir={xdg / 'home/.config/google-chrome'}"],
    )
    owned, why = chrome_owns_profile_dir(4244, "prof-a")
    assert owned is False
    assert "outside" in why


def test_owns_dir_rejects_chrome_without_user_data_dir(xdg, monkeypatch):
    _fake_proc(xdg, monkeypatch, 4245, ["/usr/bin/google-chrome-stable", "--new-window"])
    owned, why = chrome_owns_profile_dir(4245, "prof-a")
    assert owned is False
    assert "no --user-data-dir" in why


def test_owns_dir_fails_closed_on_unreadable_pid(xdg):
    owned, why = chrome_owns_profile_dir(999_999_999, "prof-a")
    assert owned is False
    assert "cannot read" in why


@pytest.mark.parametrize("pid", [None, 0])
def test_owns_dir_fails_closed_without_a_pid(xdg, pid):
    owned, _ = chrome_owns_profile_dir(pid, "prof-a")
    assert owned is False


# --------------------------------------------------------------------------
# wiring — apply_plan reaps, then tiles the survivor
# --------------------------------------------------------------------------


class FakeCdp:
    """Minimal CDP stand-in: a set of windows, each with page targets."""

    def __init__(self, windows: dict[int, list[str]]):
        self.windows = {w: list(t) for w, t in windows.items()}
        self.closed: list[str] = []
        self.bounds: dict[int, Rect] = {}

    def list_page_windows(self):
        return [(t, w, "https://example.com") for w, ts in self.windows.items() for t in ts]

    def close_target(self, target_id: str) -> bool:
        self.closed.append(target_id)
        for targets in self.windows.values():
            if target_id in targets:
                targets.remove(target_id)
        return True

    def _window_of(self, target_id: str) -> int:
        for w, targets in self.windows.items():
            if target_id in targets:
                return w
        raise AssertionError(f"target {target_id} was closed or never existed")

    def get_window_id_for_target(self, target_id: str):
        return self._window_of(target_id), None

    def apply_and_verify(self, window_id, desired, **kw):
        self.bounds[window_id] = desired
        return {"ok": True, "attempts": 1, "desired": desired.to_dict(), "actual": desired.to_dict()}


def _install_fake_cdp(monkeypatch, fake: FakeCdp, picked: str):
    import multiprofile.browser.layout_manager as lm
    from multiprofile.browser.cdp_window import PageTarget

    class _Ctx:
        def __enter__(self_inner):
            fake.pick_page_target = lambda *a, **k: PageTarget(picked, "", "https://example.com")
            return fake

        def __exit__(self_inner, *a):
            return False

    monkeypatch.setattr(lm, "CdpWindowController", lambda port: _Ctx())


@pytest.fixture
def one_running_profile(xdg, monkeypatch):
    """A profile whose pid passes the ownership guard."""
    from multiprofile.storage.xdg import chrome_profile_dir

    clear_all_runtimes()
    udd = chrome_profile_dir("prof-a") / "user-data"
    udd.mkdir(parents=True, exist_ok=True)
    _fake_proc(xdg, monkeypatch, 7001, ["/usr/bin/google-chrome-stable", f"--user-data-dir={udd}"])
    upsert_runtime("prof-a", pid=7001, cdp_port=9333)
    yield
    clear_all_runtimes()


def _plan_for(ids):
    from multiprofile.browser.grid import GridCalculator
    from multiprofile.browser.work_area import WorkArea

    return GridCalculator().plan(ids, WorkArea(67, 32, 3373, 1408, "hdmi"))


def test_apply_plan_closes_extras_then_tiles_the_survivor(one_running_profile, monkeypatch):
    fake = FakeCdp({50: ["keep1", "keep2"], 51: ["extra1"]})
    _install_fake_cdp(monkeypatch, fake, picked="extra1")

    out = WindowLayoutManager().apply_plan(_plan_for(["prof-a"]))

    assert fake.closed == ["extra1"]
    entry = out["results"][0]
    assert entry["reap"]["closed"] == 1
    assert entry["reap"]["closeWindowIds"] == [51]
    # The slot went to the window that survived, not the one we closed.
    assert entry["windowId"] == 50
    assert entry["targetId"] in ("keep1", "keep2")
    assert entry["ok"] is True
    assert 50 in fake.bounds and 51 not in fake.bounds


def test_apply_plan_does_not_close_when_ownership_is_unproven(xdg, monkeypatch):
    clear_all_runtimes()
    # pid 0 cannot be proven to own anything -> guard refuses.
    upsert_runtime("prof-a", pid=0, cdp_port=9333)
    fake = FakeCdp({60: ["a"], 61: ["b"]})
    _install_fake_cdp(monkeypatch, fake, picked="a")

    out = WindowLayoutManager().apply_plan(_plan_for(["prof-a"]))

    assert fake.closed == []
    reap = out["results"][0]["reap"]
    assert reap["closed"] == 0
    assert "ownership unproven" in reap["skipped"]
    # Still tiles one window rather than refusing to lay out at all.
    assert out["results"][0]["ok"] is True
    clear_all_runtimes()


def test_apply_plan_is_a_noop_for_a_single_window(one_running_profile, monkeypatch):
    fake = FakeCdp({70: ["only"]})
    _install_fake_cdp(monkeypatch, fake, picked="only")

    out = WindowLayoutManager().apply_plan(_plan_for(["prof-a"]))

    assert fake.closed == []
    assert out["results"][0]["reap"]["reason"] == "single_window"
    assert out["results"][0]["windowId"] == 70


def test_reaping_can_be_switched_off(one_running_profile, monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_WINDOW_REAP_EXTRAS", "0")
    fake = FakeCdp({80: ["a", "b"], 81: ["c"]})
    _install_fake_cdp(monkeypatch, fake, picked="c")

    out = WindowLayoutManager().apply_plan(_plan_for(["prof-a"]))

    assert fake.closed == []
    assert out["results"][0]["reap"] is None
    # Without reaping the tiler still moves only the target it picked.
    assert out["results"][0]["windowId"] == 81


def test_reap_setting_defaults_on():
    from multiprofile.config import get_settings

    os.environ.pop("MULTIPROFILE_WINDOW_REAP_EXTRAS", None)
    assert get_settings().window_reap_extras is True
