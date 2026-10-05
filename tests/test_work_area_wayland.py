"""F13: tiling has to work on the compositor the operator actually runs.

All three work-area detectors were X11 tools — ``xrandr``, ``_NET_WORKAREA``
via ``xprop``, and ``xdpyinfo``. Measured on this project's own development
host, Hyprland on Wayland::

    xrandr_primary: unavailable
    _NET_WORKAREA:  unavailable
    xdpyinfo:       unavailable
    no reliable work area (set MULTIPROFILE_WORKAREA=x,y,w,h)

`xrandr` and `xdpyinfo` were not installed at all, so XWayland would not have
helped. Window tiling — a real differentiator, and the reason
``--ozone-platform=x11`` is passed at all — was simply dead, and the failure
told the operator to set an environment variable rather than that the three
things that failed were X11 utilities they do not have.

Refusing to invent a size stays correct: a wrong work area tiles windows
off-screen, which is worse than not tiling. What these tests pin is that the
compositor is asked first, that scale and reserved edges are honoured, and
that the failure explains itself.
"""
from __future__ import annotations

import json

import pytest

from multiprofile.browser import work_area as wa


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_WORKAREA", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")


def _fake_tools(monkeypatch, available: set[str], outputs: dict[str, object]):
    """Pretend a given set of CLI tools exists and answers with given JSON."""
    monkeypatch.setattr(wa.shutil, "which", lambda t: f"/usr/bin/{t}" if t in available else None)

    def check_output(cmd, **kw):
        tool = cmd[0]
        if tool not in outputs:
            raise OSError(f"{tool} not available")
        return json.dumps(outputs[tool])

    monkeypatch.setattr(wa.subprocess, "check_output", check_output)


#: The real shape this host reports, with a 26px top bar.
HYPR = [{
    "name": "eDP-1", "x": 0, "y": 0, "width": 2560, "height": 1600,
    "scale": 1, "focused": True, "reserved": [0, 26, 0, 0],
}]


# --------------------------------------------------------------------------
# 1. The compositor is asked, and understood
# --------------------------------------------------------------------------

def test_hyprland_is_detected(monkeypatch):
    """The finding: this exact configuration produced no work area at all."""
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": HYPR})
    area = wa.WorkAreaProvider().detect()
    assert (area.x, area.y, area.w, area.h) == (0, 26, 2560, 1574)
    assert area.source == "hyprctl"


def test_the_reserved_bar_is_subtracted(monkeypatch):
    """Without this a tiled window sits underneath the status bar. It is the
    Wayland equivalent of _NET_WORKAREA, which the X11 path already honoured."""
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": HYPR})
    area = wa.WorkAreaProvider().detect()
    assert area.y == 26
    assert area.h == 1600 - 26


@pytest.mark.parametrize(("reserved", "expected"), [
    ([0, 26, 0, 0], (0, 26, 2560, 1574)),      # top bar
    ([0, 0, 0, 40], (0, 0, 2560, 1560)),       # bottom dock
    ([60, 0, 0, 0], (60, 0, 2500, 1600)),      # left bar
    ([10, 20, 30, 40], (10, 20, 2520, 1540)),  # all four
    ([0, 0, 0, 0], (0, 0, 2560, 1600)),        # none
    (None, (0, 0, 2560, 1600)),                # not reported
])
def test_every_edge_of_reserved_is_honoured(monkeypatch, reserved, expected):
    monitor = dict(HYPR[0])
    if reserved is None:
        monitor.pop("reserved", None)
    else:
        monitor["reserved"] = reserved
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": [monitor]})
    area = wa.WorkAreaProvider().detect()
    assert (area.x, area.y, area.w, area.h) == expected


def test_scale_is_converted_to_logical_pixels(monkeypatch):
    """Windows are positioned in logical coordinates. Ignoring a 2x scale
    would place every tiled window in the top-left quadrant."""
    monitor = {**HYPR[0], "width": 3840, "height": 2160, "scale": 2, "reserved": [0, 0, 0, 0]}
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": [monitor]})
    area = wa.WorkAreaProvider().detect()
    assert (area.w, area.h) == (1920, 1080)


def test_the_focused_monitor_is_used(monkeypatch):
    """Where the operator is looking is where a launched window lands."""
    monitors = [
        {**HYPR[0], "name": "HDMI-1", "x": 0, "width": 1920, "height": 1080,
         "focused": False, "reserved": [0, 0, 0, 0]},
        {**HYPR[0], "name": "eDP-1", "x": 1920, "focused": True, "reserved": [0, 0, 0, 0]},
    ]
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": monitors})
    area = wa.WorkAreaProvider().detect()
    assert area.x == 1920
    assert area.w == 2560


# --------------------------------------------------------------------------
# 2. The other compositors
# --------------------------------------------------------------------------

def test_sway_is_detected(monkeypatch):
    outputs = [{"name": "eDP-1", "focused": True, "active": True,
                "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080}}]
    _fake_tools(monkeypatch, {"swaymsg"}, {"swaymsg": outputs})
    area = wa.WorkAreaProvider().detect()
    assert (area.w, area.h) == (1920, 1080)
    assert area.source == "swaymsg"


def test_wlr_randr_is_detected(monkeypatch):
    outputs = [{
        "name": "eDP-1", "enabled": True, "scale": 1,
        "position": {"x": 0, "y": 0},
        "modes": [{"width": 1920, "height": 1080, "current": True}],
    }]
    _fake_tools(monkeypatch, {"wlr-randr"}, {"wlr-randr": outputs})
    area = wa.WorkAreaProvider().detect()
    assert (area.w, area.h) == (1920, 1080)
    assert area.source == "wlr-randr"


def test_detectors_are_tried_in_order_and_the_first_answer_wins(monkeypatch):
    sway = [{"focused": True, "rect": {"x": 0, "y": 0, "width": 1280, "height": 720}}]
    _fake_tools(monkeypatch, {"hyprctl", "swaymsg"}, {"hyprctl": HYPR, "swaymsg": sway})
    assert wa.WorkAreaProvider().detect().source == "hyprctl"


def test_a_detector_that_answers_nonsense_falls_through(monkeypatch):
    """A compositor that reports a 4-pixel monitor must not win."""
    tiny = [{**HYPR[0], "width": 50, "height": 50, "reserved": [0, 0, 0, 0]}]
    sway = [{"focused": True, "rect": {"x": 0, "y": 0, "width": 1280, "height": 720}}]
    _fake_tools(monkeypatch, {"hyprctl", "swaymsg"}, {"hyprctl": tiny, "swaymsg": sway})
    assert wa.WorkAreaProvider().detect().source == "swaymsg"


def test_a_bar_that_consumes_the_whole_screen_is_refused(monkeypatch):
    monitor = {**HYPR[0], "reserved": [0, 1590, 0, 0]}
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": [monitor]})
    with pytest.raises(wa.WorkAreaError):
        wa.WorkAreaProvider().detect()


@pytest.mark.parametrize("junk", [[], {}, None, "not json", [{"no": "geometry"}]])
def test_malformed_compositor_output_does_not_crash(monkeypatch, junk):
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": junk})
    with pytest.raises(wa.WorkAreaError):
        wa.WorkAreaProvider().detect()


# --------------------------------------------------------------------------
# 3. The failure explains itself
# --------------------------------------------------------------------------

def test_a_wayland_session_with_no_tools_says_so(monkeypatch):
    """"Set this variable" does not tell a Wayland user that the three things
    that failed were X11 utilities they do not have and do not want."""
    _fake_tools(monkeypatch, set(), {})
    with pytest.raises(wa.WorkAreaError) as excinfo:
        wa.WorkAreaProvider().detect()
    joined = " ".join(excinfo.value.reasons)
    assert "Wayland session" in joined
    assert "hyprctl" in joined and "swaymsg" in joined
    assert "MULTIPROFILE_WORKAREA" in joined


def test_an_x11_session_does_not_mention_wayland(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    _fake_tools(monkeypatch, set(), {})
    with pytest.raises(wa.WorkAreaError) as excinfo:
        wa.WorkAreaProvider().detect()
    assert "Wayland session" not in " ".join(excinfo.value.reasons)


def test_wayland_detectors_are_skipped_on_an_x11_session(monkeypatch):
    """A Wayland tool being installed is not a reason to trust it when the
    session is X11 -- it would describe a compositor that is not running."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": HYPR})
    with pytest.raises(wa.WorkAreaError):
        wa.WorkAreaProvider().detect()


def test_the_env_override_still_wins(monkeypatch):
    """The documented escape hatch must outrank a working compositor."""
    monkeypatch.setenv("MULTIPROFILE_WORKAREA", "10,20,800,600")
    _fake_tools(monkeypatch, {"hyprctl"}, {"hyprctl": HYPR})
    area = wa.WorkAreaProvider().detect()
    assert (area.x, area.y, area.w, area.h) == (10, 20, 800, 600)
    assert area.source.startswith("env:")


def test_nothing_ever_invents_a_size(monkeypatch):
    """The property the original code got right, kept: a wrong work area
    tiles windows off-screen, which is worse than not tiling."""
    _fake_tools(monkeypatch, set(), {})
    with pytest.raises(wa.WorkAreaError):
        wa.WorkAreaProvider().detect()


# --------------------------------------------------------------------------
# 4. This host
# --------------------------------------------------------------------------

def test_this_machine_can_tile():
    """Not mocked. On the development host -- Hyprland, no X11 tools -- this
    failed entirely before the fix."""
    import shutil as real_shutil

    if not any(real_shutil.which(t) for t, _ in wa._WAYLAND_DETECTORS) and not any(
        real_shutil.which(t) for t in ("xrandr", "xprop", "xdpyinfo")
    ):
        pytest.skip("no work-area detector available on this host")
    area = wa.WorkAreaProvider().detect()
    assert area.w >= 100 and area.h >= 100
