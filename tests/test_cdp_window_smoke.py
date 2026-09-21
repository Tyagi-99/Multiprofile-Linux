"""Optional live CDP smoke — skipped unless MULTIPROFILE_CDP_TILE_TESTS=1."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MULTIPROFILE_CDP_TILE_TESTS") != "1",
    reason="Set MULTIPROFILE_CDP_TILE_TESTS=1 to run live CDP tiling smoke",
)


def test_cdp_smoke_placeholder():
    # Live Chrome session required on IdeaPad; gated off by default.
    assert True
