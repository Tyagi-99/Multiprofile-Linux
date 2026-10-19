"""Gate T2's HTTP/2 half: what the product presents above a completed handshake.

``test_transport_identity_live.py`` measures the TLS first flight and stops
there, because ``transport_probe`` cannot see SETTINGS without a certificate
the browser will trust. This file supplies that certificate -- a lab one,
generated per run -- and measures the layer above: the client's SETTINGS
values, the order of its frame types, its connection-level window update and
its priority scheme.

The question it answers is the same one, one layer up: **does the product's
own machinery perturb what a destination sees?** The CDP overrides, the
injected script, the restricted font view and the proxy flags all sit above
the network stack, so the expectation is that they do not -- but the TLS
measurement was worth taking and so is this one, for the same reason: an
expectation is not a measurement.

The question it does **not** answer is whether these values match Chrome on
the *claimed* operating system. That is gate T1 and needs a real Windows
host. Nothing here licenses a claim about it.

**Why the engine is pinned to a wrapper.** The browser has to complete a
handshake against a certificate no one has vouched for. Installing a CA into
the user's trust store to arrange that would change what their browser trusts
for a test, which is not an acceptable price, so the engine is pointed at a
one-line wrapper that adds ``--ignore-certificate-errors`` and the matched
control is given the identical flag. What is compared is then the browser and
not the flag.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.network


def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


if not _chrome_available():
    pytest.skip("Chrome is not installed", allow_module_level=True)

if not shutil.which("openssl"):
    pytest.skip("openssl is needed for the lab certificate", allow_module_level=True)

from multiprofile.diagnostics.h2_probe import differences, observe_once  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _lab_cert(into: Path) -> tuple[Path, Path]:
    cert, key = into / "cert.pem", into / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key),
         "-out", str(cert), "-days", "1", "-nodes", "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return cert, key


def _wrapper(into: Path) -> Path:
    from multiprofile.browser.launcher import find_chrome

    path = into / "chromium-lab"
    path.write_text(
        "#!/bin/bash\n"
        "# Gate T2: stock Chromium plus the one flag that lets the lab\n"
        "# certificate complete a handshake. The control gets it too.\n"
        f'exec {find_chrome()} --ignore-certificate-errors '
        '--allow-insecure-localhost "$@"\n'
    )
    path.chmod(0o755)
    return path


@pytest.fixture(scope="module")
def captures(tmp_path_factory):
    """One h2 observation from the product and one from stock Chromium."""
    from _pytest.monkeypatch import MonkeyPatch

    tmp_path = tmp_path_factory.mktemp("h2")
    cert, key = _lab_cert(tmp_path)
    wrapper = _wrapper(tmp_path)

    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    mp.setenv("MULTIPROFILE_ENGINE_BINARY", str(wrapper))

    import multiprofile.browser.launcher as L
    from multiprofile.storage.profiles import create_profile

    out: dict[str, dict] = {}

    def _observe(label: str, port: int):
        out[label] = observe_once(port, cert, key, timeout=45.0)

    try:
        port = _free_port()
        watcher = threading.Thread(target=_observe, args=("product", port), daemon=True)
        watcher.start()
        time.sleep(1.0)
        url = f"https://localhost:{port}/"
        profile_id = create_profile(
            {"name": "h2-product", "proxyType": "none", "startUrl": url})["id"]
        assert L.launch_profile(profile_id, url=url, direct=True).get("ok") is True
        time.sleep(12.0)
        L.stop_profile(profile_id)
        watcher.join(timeout=25)

        port = _free_port()
        watcher = threading.Thread(target=_observe, args=("stock", port), daemon=True)
        watcher.start()
        time.sleep(1.0)
        user_data = tempfile.mkdtemp(prefix="h2-control-")
        control = subprocess.Popen(
            [str(wrapper), f"--user-data-dir={user_data}", "--no-first-run",
             "--no-default-browser-check", "--disable-sync",
             f"https://localhost:{port}/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(12.0)
        control.terminate()
        try:
            control.wait(timeout=10)
        except subprocess.TimeoutExpired:
            control.kill()
        watcher.join(timeout=25)
        shutil.rmtree(user_data, ignore_errors=True)
        yield out
    finally:
        mp.undo()


def test_both_sides_completed_an_h2_connection(captures):
    for label in ("product", "stock"):
        obs = captures.get(label) or {}
        assert obs.get("error") is None, f"{label}: {obs.get('error')}"
        assert obs.get("alpn") == "h2", f"{label} negotiated {obs.get('alpn')!r}"
        assert obs.get("preface_ok") is True, f"{label} sent no valid h2 preface"


def test_the_product_adds_no_http2_artefact(captures):
    """The claim this file exists to support, or to refute."""
    diff = differences(captures["product"], captures["stock"])
    assert diff == {}, f"product and stock disagree above TLS: {diff}"


def test_the_settings_frame_is_populated(captures):
    """A probe that reads nothing would pass the comparison above vacuously."""
    settings = captures["product"]["settings"]
    assert settings, "no SETTINGS captured, so the comparison proved nothing"
    assert "INITIAL_WINDOW_SIZE" in settings
    assert captures["product"]["window_update"], "no connection window update seen"


def test_the_frame_prefix_matches(captures):
    """Order is part of the fingerprint; the tail varies with sub-resources."""
    product = captures["product"]["frame_order"][:3]
    stock = captures["stock"]["frame_order"][:3]
    assert product == stock, f"{product} != {stock}"
