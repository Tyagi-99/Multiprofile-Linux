"""Launch flags that make CDP window bounds actually move native windows."""
from __future__ import annotations

import json
from pathlib import Path

from multiprofile.browser import grid, launcher
from multiprofile.browser.grid import GridCalculator
from multiprofile.browser.work_area import WorkArea


def test_platform_args_force_x11_so_cdp_bounds_are_real():
    """Wayland compositors ignore Browser.setWindowBounds; X11/XWayland does not."""
    args = launcher.chrome_window_platform_args()
    assert "--ozone-platform=x11" in args
    assert "--ozone-platform-hint=x11" in args


def test_disable_features_merged_once_includes_wayland_decorations():
    arg = launcher.chrome_disable_features_arg()
    assert arg.startswith("--disable-features=")
    val = arg.split("=", 1)[1]
    parts = val.split(",")
    assert len(parts) == len(set(parts))
    assert "WaylandWindowDecorations" in parts
    assert "ChromeWhatsNewUI" in parts


def test_launcher_source_uses_helpers_not_duplicate_disable_features():
    src = Path("src/multiprofile/browser/launcher.py").read_text(encoding="utf-8")
    assert "chrome_window_platform_args()" in src
    assert "chrome_disable_features_arg()" in src
    # Only the helper may mention the flag; launch cmd must not add a second copy.
    assert src.count("--disable-features=") == 1


def test_jobs_pass_slot_rects_at_launch():
    src = Path("src/multiprofile/jobs.py").read_text(encoding="utf-8")
    assert "window_rect=None" not in src
    assert "slot_rect_map" in src


def test_ensure_no_client_frame_shadow_writes_pref(tmp_path):
    launcher.ensure_no_client_frame_shadow(tmp_path)
    data = json.loads((tmp_path / "Default" / "Preferences").read_text(encoding="utf-8"))
    assert data["browser"]["custom_chrome_frame"] is False


def test_ensure_no_client_frame_shadow_preserves_other_keys(tmp_path):
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir(parents=True)
    prefs.write_text(
        json.dumps({"browser": {"window_placement": {"left": 9}}, "keep": True}),
        encoding="utf-8",
    )
    launcher.ensure_no_client_frame_shadow(tmp_path)
    data = json.loads(prefs.read_text(encoding="utf-8"))
    assert data["keep"] is True
    assert data["browser"]["window_placement"]["left"] == 9
    assert data["browser"]["custom_chrome_frame"] is False


def test_launcher_writes_no_shadow_pref_before_spawn():
    src = Path("src/multiprofile/browser/launcher.py").read_text(encoding="utf-8")
    assert "ensure_no_client_frame_shadow(" in src


def test_slot_rect_map_assigns_distinct_cells():
    plan = GridCalculator().plan(["a", "b", "c", "d"], WorkArea(0, 32, 1920, 1048, "t"))
    m = grid.slot_rect_map(plan)
    assert set(m) == {"a", "b", "c", "d"}
    cells = {(r.x, r.y, r.w, r.h) for r in m.values()}
    assert len(cells) == 4
