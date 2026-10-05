"""What a destination sees of the product's TLS, against a matched reference.

Phase 4 of the parity roadmap, and it is careful about one thing above all:
*"Do not declare transport alignment from a JA3/JA4 string without a matched
reference and controlled route."*

So this file does not print a fingerprint and call it a result. It captures
the ClientHello of a launched product profile **and** of stock Chromium on the
same host, in the same session, from the same destination, and compares them
field by field.

**The question it can answer.** Does the product's own machinery -- the CDP
overrides, the injected script, the restricted font view, the proxy flags --
perturb the TLS first flight? If it did, the product would be adding a
transport artefact of its own on top of whatever Chromium already presents,
and that would be a self-inflicted wound worth finding.

**The question it cannot answer, and must not appear to.** Whether that
first flight matches Chrome on the *claimed* operating system. Chromium ships
BoringSSL on every platform, so a Linux and a Windows build of the same
version are expected to look similar -- but "expected to" is not a
measurement, and producing one needs a genuine same-version Chrome on a real
Windows host. That reference does not exist here. It is a **gate**, recorded
in `docs/03_FEATURE_MATRIX.md`, and nothing in this file licenses a claim
about it.

GREASE (RFC 8701) is stripped before comparison. Chromium picks those values
per connection, so comparing raw JA3s compares randomness and would report a
difference between two runs of the *same* browser.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
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

from _route_lab import TlsObserver  # noqa: E402

from multiprofile.diagnostics.transport_probe import compare_hellos  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _software_gpu_flags() -> list[str]:
    """Match the product's own GPU decision so the control is comparable."""
    try:
        from multiprofile.browser.launcher import _probe_gpu_available

        if not _probe_gpu_available():
            return ["--disable-gpu", "--use-gl=swiftshader",
                    "--enable-unsafe-swiftshader"]
    except Exception:  # noqa: BLE001 - the control still runs without it
        pass
    return []


def _capture_stock(url: str) -> object:
    """Stock Chromium, scratch profile, no product flags."""
    from multiprofile.browser.launcher import find_chrome

    observer_wait = 6.0
    user_data = Path(tempfile.mkdtemp(prefix="tls-control-"))
    process = subprocess.Popen(
        [find_chrome(), f"--remote-debugging-port={_free_port()}",
         f"--user-data-dir={user_data}", "--no-first-run",
         "--no-default-browser-check", "--disable-sync",
         *_software_gpu_flags(), url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(observer_wait)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(user_data, ignore_errors=True)


@pytest.fixture(scope="module")
def flights(tmp_path_factory):
    """One ClientHello from the product and one from stock Chromium."""
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("tls")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    observer = TlsObserver()
    out: dict[str, object] = {}
    try:
        # Product first. The navigation fails -- there is no certificate --
        # and the first flight is what is being measured.
        profile_id = create_profile({
            "name": "tls-product", "proxyType": "none",
            "startUrl": observer.url,
        })["id"]
        assert L.launch_profile(
            profile_id, url=observer.url, direct=True).get("ok") is True
        time.sleep(6.0)
        L.stop_profile(profile_id)
        assert observer.hellos, (
            "the product sent no parsable ClientHello: "
            f"{len(observer.raw)} raw buffer(s), errors={observer.errors}"
        )
        out["product"] = observer.hellos[0]
        product_count = len(observer.hellos)

        _capture_stock(observer.url)
        assert len(observer.hellos) > product_count, (
            "stock Chromium sent no parsable ClientHello: "
            f"errors={observer.errors}"
        )
        out["control"] = observer.hellos[product_count]
        out["errors"] = list(observer.errors)
        return out
    finally:
        observer.stop()
        mp.undo()


def test_both_first_flights_were_captured(flights):
    assert flights["product"] is not None
    assert flights["control"] is not None
    assert flights.get("errors") == [], flights.get("errors")


def test_the_flight_looks_like_a_modern_browser(flights):
    """Positive control. Two unparsed or empty hellos would compare equal."""
    for label in ("product", "control"):
        hello = flights[label]
        data = hello.to_dict()
        assert data["cipherCount"] >= 10, (label, data["cipherCount"])
        assert data["extensionCount"] >= 10, (label, data["extensionCount"])
        assert "h2" in data["alpn"], (label, data["alpn"])
        assert data["greaseCiphers"], (
            f"{label}: no GREASE cipher, which every Chromium sends -- the "
            "parse is probably wrong"
        )
        assert "0x304" in data["supportedVersionsNoGrease"], (label, data)


def test_chromium_randomises_extension_order_so_ja3_cannot_be_compared(flights):
    """The measurement's own noise floor, established before anything is
    attributed to the product.

    Measured on Chromium 151: **sixteen** ClientHellos captured from *one*
    browser run gave sixteen distinct extension orders, one distinct
    extension set, and one distinct cipher list. Chromium shuffles the
    extension order per connection deliberately, to stop the ecosystem
    ossifying around a fixed layout.

    JA3 concatenates the extension list in order, so a Chromium JA3 is a new
    value on every connection and two runs of the same binary disagree. The
    first version of this suite compared extension order and reported a
    difference -- which was real, and was Chromium's randomness, not the
    product's doing. Real JA4 sorts the list for exactly this reason.
    """
    from multiprofile.diagnostics.transport_probe import is_grease

    product = flights["product"]
    control = flights["control"]
    product_order = [v for v in product.extensions if not is_grease(v)]
    control_order = [v for v in control.extensions if not is_grease(v)]

    assert sorted(product_order) == sorted(control_order), (
        "the extension SETS differ, which randomisation does not explain"
    )
    # The headline: same set, and the raw JA3s disagree anyway.
    assert product.ja3(strip_grease=True) != control.ja3(strip_grease=True) or \
        product_order == control_order, (
        "JA3s matched, which means the order happened to match this run -- "
        "possible but not something to rely on"
    )
    assert [v for v in product.cipher_suites if is_grease(v)], (
        "no GREASE cipher observed, which every Chromium sends -- the parse "
        "is probably wrong and this file proves nothing"
    )


def test_the_sorted_fingerprint_is_stable_across_connections(flights):
    """And the corollary: a sorted form *is* comparable.

    If this failed, there would be no stable transport fingerprint to compare
    at all and the assertion below would be meaningless.
    """
    assert flights["product"].ja4_like() == flights["control"].ja4_like(), (
        flights["product"].ja4_like(), flights["control"].ja4_like()
    )


def test_the_product_adds_no_transport_artefact_of_its_own(flights):
    """THE Phase 4 assertion this harness can actually make.

    The product's CDP overrides, injected script, restricted font view and
    proxy flags must not change the TLS first flight. If they did, the
    product would be adding an artefact *on top of* whatever Chromium
    presents -- detectable, self-inflicted, and entirely ours to fix.

    This says nothing about whether the flight matches Chrome on a claimed
    foreign OS. See the module docstring.
    """
    report = compare_hellos(flights["product"], flights["control"])
    assert report["identical"], (
        "the product's TLS first flight differs from stock Chromium on the "
        "same host:\n" + json.dumps(report["differences"], indent=2)
    )
    assert report["ja4Like"]["equal"], report["ja4Like"]
    # JA3 is reported but deliberately not asserted on; see the test above.
    assert report["ja3"]["comparable"] is False, report["ja3"]
