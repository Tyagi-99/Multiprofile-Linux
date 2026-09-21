"""WorkAreaProvider fail-loud tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from multiprofile.browser.work_area import WorkAreaError, WorkAreaProvider


def test_env_override(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_WORKAREA", "10,20,1280,720")
    area = WorkAreaProvider().detect()
    assert area.x == 10 and area.y == 20 and area.w == 1280 and area.h == 720
    assert "env" in area.source or "MULTIPROFILE_WORKAREA" in area.source


def test_all_providers_fail_is_loud(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_WORKAREA", raising=False)
    with patch("multiprofile.browser.work_area._from_env", return_value=None), patch(
        "multiprofile.browser.work_area._from_net_workarea", return_value=None
    ), patch("multiprofile.browser.work_area._from_xdpyinfo", return_value=None):
        with pytest.raises(WorkAreaError) as ei:
            WorkAreaProvider().detect()
    err = ei.value
    assert err.code == "work_area_unavailable"
    assert err.reasons
    blob = str(err).lower()
    assert "1920" not in blob or "refus" in blob


def test_never_silent_default_resolution(monkeypatch):
    monkeypatch.delenv("MULTIPROFILE_WORKAREA", raising=False)
    with patch("multiprofile.browser.work_area._from_env", return_value=None), patch(
        "multiprofile.browser.work_area._from_net_workarea", return_value=None
    ), patch("multiprofile.browser.work_area._from_xdpyinfo", return_value=None):
        with pytest.raises(WorkAreaError):
            WorkAreaProvider().detect()
