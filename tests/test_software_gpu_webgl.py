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


# --------------------------------------------------------------------------
# A display is not a GPU
# --------------------------------------------------------------------------

def test_a_virtual_display_does_not_count_as_a_gpu(monkeypatch, tmp_path):
    """`_probe_gpu_available` used to return True whenever DISPLAY was set,
    on the reasoning that "at minimum Xvfb may work with ANGLE".

    It does not. Xvfb is a window server, not a graphics device: ANGLE has
    nothing to bind, WebGL fails outright, and because the probe claimed
    hardware the software path with its SwiftShader flag was never taken. Any
    headless server with a virtual display hits this, which is a large share
    of the machines this product runs on unattended.
    """
    from pathlib import Path

    import multiprofile.browser.launcher as L

    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    assert L._probe_gpu_available() is False
    assert SWIFTSHADER_GATE in L.chrome_gpu_args("auto")


def test_a_wayland_display_does_not_count_either(monkeypatch):
    from pathlib import Path

    import multiprofile.browser.launcher as L

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    assert L._probe_gpu_available() is False


def test_a_real_render_node_still_counts(monkeypatch):
    """The fix must not go the other way and send a machine with a GPU down
    the software path."""
    from pathlib import Path

    import multiprofile.browser.launcher as L

    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    monkeypatch.setattr(Path, "glob", lambda self, pat: [Path("/dev/dri/renderD128")])
    monkeypatch.setattr(Path, "stat", lambda self, **k: True)

    assert L._probe_gpu_available() is True
    assert L.chrome_gpu_args("auto") == ["--use-gl=angle", "--use-angle=gl"]
