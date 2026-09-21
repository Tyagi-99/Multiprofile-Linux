"""Launch state registry transitions."""
from __future__ import annotations

from multiprofile.browser.runtime_state import (
    LaunchState,
    clear_all_runtimes,
    clear_runtime,
    get_runtime,
    set_launch_state,
    upsert_runtime,
)


def setup_function():
    clear_all_runtimes()


def test_upsert_transition_clear():
    assert get_runtime("p1") is None
    rt = upsert_runtime("p1", pid=42, cdp_port=9333)
    assert rt.pid == 42 and rt.cdp_port == 9333
    set_launch_state("p1", LaunchState.LAUNCHING)
    set_launch_state("p1", LaunchState.CDP_READY, cdp_port=9333)
    set_launch_state("p1", LaunchState.RUNNING)
    rt2 = get_runtime("p1")
    assert rt2.launch_state == LaunchState.RUNNING
    clear_runtime("p1")
    assert get_runtime("p1") is None


def test_failure_terminal():
    upsert_runtime("p2", pid=1, cdp_port=1)
    set_launch_state("p2", LaunchState.CDP_FAILED, error="boom", error_code="cdp_unreachable")
    rt = get_runtime("p2")
    assert rt.launch_state == LaunchState.CDP_FAILED
    assert rt.error_code == "cdp_unreachable"
