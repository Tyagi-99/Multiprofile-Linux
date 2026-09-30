"""A host with no GPU must still have WebGL.

Measured on Chromium 151, with the flags the product used on any machine
without ``/dev/dri``::

    --disable-gpu --use-gl=swiftshader
        canvas.getContext('webgl')  ->  null

Chrome gates SwiftShader behind ``--enable-unsafe-swiftshader`` from 121
onward. Without it WebGL does not fall back to software -- it is absent
entirely.

That is not a CI detail. A server, a VM or a container takes this path, and a
profile claiming an "NVIDIA GeForce RTX 4060" whose `getContext('webgl')`
returns null is a contradiction no real browser produces. It is **F26**'s
shape one more time: the GPU story disagreeing with itself, and this time by
not existing.

The unit tests below are the guard; `test_webgl_live.py`-style verification of
the real browser happens in the live suite, which is where a browser exists.
"""
from __future__ import annotations

import pytest

from multiprofile.browser.launcher import chrome_gpu_args

SWIFTSHADER_GATE = "--enable-unsafe-swiftshader"


@pytest.mark.parametrize("mode", ["software", "auto"])
def test_every_software_path_enables_swiftshader(mode, monkeypatch):
    """`auto` reaches the same flags on a host with no GPU, which is the case
    that matters -- nobody sets `software` by hand on a server."""
    monkeypatch.setattr(
        "multiprofile.browser.launcher._probe_gpu_available", lambda: False
    )
    flags = chrome_gpu_args(mode)
    assert "--use-gl=swiftshader" in flags
    assert SWIFTSHADER_GATE in flags, flags


def test_the_hardware_path_does_not_carry_it(monkeypatch):
    """It is meaningless with a real GPU, and a flag that appears where it
    does nothing is a flag nobody can reason about later."""
    monkeypatch.setattr(
        "multiprofile.browser.launcher._probe_gpu_available", lambda: True
    )
    flags = chrome_gpu_args("auto")
    assert SWIFTSHADER_GATE not in flags
    assert "--use-gl=angle" in flags


def test_angle_mode_does_not_carry_it():
    assert SWIFTSHADER_GATE not in chrome_gpu_args("angle")


def test_disabled_mode_is_left_alone():
    """`disabled` is the legacy compatibility mode and means "no GPU stack at
    all". Adding a WebGL-enabling flag to it would change what it means."""
    assert chrome_gpu_args("disabled") == ["--disable-gpu"]


def test_the_flag_list_is_not_shared_between_calls():
    """It is a module-level list; handing callers the same object would let
    one launch's mutation reach the next."""
    a = chrome_gpu_args("software")
    a.append("--poisoned")
    assert "--poisoned" not in chrome_gpu_args("software")
