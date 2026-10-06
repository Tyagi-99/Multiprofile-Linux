"""Run the Node SDK's own tests against a real server, from pytest.

The Node client needs covering, and a separate CI job is a job someone
forgets to add. So this starts the real app on a loopback port, points
``MP_BASE_URL`` at it, and runs ``node --test`` -- which means the Node SDK is
covered by the same `pytest` invocation as everything else.

Skips when Node is absent rather than failing: a Python-only contributor
should not see a red suite for a toolchain they were not asked to install.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

SDK_DIR = Path(__file__).resolve().parents[1] / "sdk" / "node"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed")


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def test_the_node_sdk_passes_against_a_real_server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")

    from multiprofile.api.server import create_app

    httpd = make_server("127.0.0.1", 0, create_app(), handler_class=_QuietHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        result = subprocess.run(
            ["node", "--test", "multiprofile.test.mjs"],
            cwd=SDK_DIR, capture_output=True, text=True, timeout=180,
            env={**__import__("os").environ, "MP_BASE_URL": base_url},
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert result.returncode == 0, (
        "the Node SDK tests failed:\n"
        + result.stdout[-4000:] + "\n" + result.stderr[-2000:]
    )
    # Guard against the suite reporting success because every test skipped.
    assert "pass 9" in result.stdout or "# pass 9" in result.stdout, (
        "expected 9 passing Node tests; the suite may have skipped:\n"
        + result.stdout[-2000:]
    )
