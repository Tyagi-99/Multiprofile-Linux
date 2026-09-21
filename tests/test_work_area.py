"""WorkAreaProvider fail-loud tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from multiprofile.browser.work_area import WorkArea, WorkAreaError, WorkAreaProvider, intersect_work_areas


def test_env_override(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_WORKAREA", "10,20,1280,720")
    area = WorkAreaProvider().detect()
    assert area.x == 10 and area.y == 20 and area.w == 1280 and area.h == 720
    assert "env" in area.source or "MULTIPROFILE_WORKAREA" in area.source


def test_all_providers_fail_is_loud(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_WORKAREA", raising=False)
    with patch("multiprofile.browser.work_area._from_env", return_value=None), patch(
        "multiprofile.browser.work_area._from_xrandr_primary", return_value=None
    ), patch("multiprofile.browser.work_area._from_net_workarea", return_value=None), patch(
        "multiprofile.browser.work_area._from_xdpyinfo", return_value=None
    ):
        with pytest.raises(WorkAreaError) as ei:
            WorkAreaProvider().detect()
    err = ei.value
    assert err.code == "work_area_unavailable"
    assert err.reasons
    blob = str(err).lower()
    assert "1920" not in blob or "refus" in blob


def test_primary_intersect_excludes_dock_and_other_monitors():
    primary = WorkArea(0, 0, 3440, 1440, "xrandr:HDMI-1")
    net = WorkArea(67, 32, 5293, 1408, "xprop:_NET_WORKAREA")
    area = intersect_work_areas(primary, net, "xrandr:HDMI-1+xprop:_NET_WORKAREA")
    assert area is not None
    assert area.x == 67 and area.y == 32
    assert area.w == 3373 and area.h == 1408


def test_detect_clips_primary_to_net_workarea(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_WORKAREA", raising=False)
    primary = WorkArea(0, 0, 3440, 1440, "xrandr:HDMI-1")
    net = WorkArea(67, 32, 5293, 1408, "xprop:_NET_WORKAREA")
    with patch("multiprofile.browser.work_area._from_env", return_value=None), patch(
        "multiprofile.browser.work_area._from_xrandr_primary", return_value=primary
    ), patch("multiprofile.browser.work_area._from_net_workarea", return_value=net):
        area = WorkAreaProvider().detect()
    assert area.x == 67 and area.y == 32
    assert area.w == 3373 and area.h == 1408


def test_never_silent_default_resolution(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_WORKAREA", raising=False)
    with patch("multiprofile.browser.work_area._from_env", return_value=None), patch(
        "multiprofile.browser.work_area._from_xrandr_primary", return_value=None
    ), patch("multiprofile.browser.work_area._from_net_workarea", return_value=None), patch(
        "multiprofile.browser.work_area._from_xdpyinfo", return_value=None
    ):
        with pytest.raises(WorkAreaError):
            WorkAreaProvider().detect()
