"""What a launched profile's WebGPU adapter actually says.

The unit tests check the derivation. This one checks the engine, because the
fix has two halves that can fail independently: a launch flag that keeps an
adapter alive under the x11 ozone platform, and a hook that rewrites what the
adapter says about itself. Either half alone leaves a contradiction.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.network


def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


pytest.importorskip("websocket")
if not _chrome_available():
    pytest.skip("Chrome is not installed", allow_module_level=True)

_PROBE = r"""
(async () => {
  const o = {};
  const c = document.createElement('canvas').getContext('webgl');
  const d = c.getExtension('WEBGL_debug_renderer_info');
  o.webgl_renderer = c.getParameter(d.UNMASKED_RENDERER_WEBGL);
  o.has_gpu = typeof navigator.gpu;
  if (navigator.gpu) {
    const a = await navigator.gpu.requestAdapter();
    if (!a) { o.adapter = null; return JSON.stringify(o); }
    const i = a.info || {};
    o.adapter = {vendor: i.vendor, architecture: i.architecture,
                 device: i.device, description: i.description};
    // The adapter must still be a working one.
    o.limit = a.limits ? a.limits.maxTextureDimension2D : null;
    o.feature_count = [...(a.features || [])].length;
    try { o.device_ok = !!(await a.requestDevice()); }
    catch (e) { o.device_ok = 'THREW:' + e.name; }
  }
  return JSON.stringify(o);
})()
"""


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>g</title>ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/g"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _probe(seed: int, tmp_path) -> dict:
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({
        "name": f"gpu{seed}", "proxyType": "none", "startUrl": page.url, "seed": seed,
    })["id"]
    try:
        assert L.launch_profile(pid, url=page.url, direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        time.sleep(3.0)

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        target = next(
            t for t in json.loads(urllib.request.urlopen(req, timeout=5).read())
            if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
        )
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=15, suppress_origin=True
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "awaitPromise": True, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 40
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        return json.loads(value)
    finally:
        L.stop_profile(pid)
        page.close()
        mp.undo()


@pytest.fixture(scope="module")
def two_profiles(tmp_path_factory):
    probed = {s: _probe(s, tmp_path_factory.mktemp(f"g{s}")) for s in (111, 222)}
    # A host whose engine exposes no WebGPU cannot be asked whether this
    # product's WebGPU claim is coherent. Measured on Chromium 151 with
    # software rendering: `navigator.gpu` is undefined, and neither
    # --enable-unsafe-webgpu nor a SwiftShader Vulkan driver brings it back.
    #
    # Skipped on the browser's own answer rather than on a guess about the
    # machine, so this still runs wherever WebGPU exists -- which includes
    # the development host and any runner with a real GPU. Asserting into an
    # absent API would report an environment gap as a product failure, and a
    # suite that cries wolf about the environment is one whose real findings
    # get skimmed.
    if all(p.get("has_gpu") != "object" for p in probed.values()):
        pytest.skip(
            "this engine exposes no navigator.gpu (software rendering); "
            "WebGPU coherence cannot be measured here"
        )
    return probed


def test_webgpu_is_available_at_all(two_profiles):
    """Forcing --ozone-platform=x11 for window placement used to cost the
    adapter entirely. --enable-features=Vulkan buys it back."""
    for seed, p in two_profiles.items():
        assert p["has_gpu"] == "object", seed
        assert p["adapter"] is not None, f"{seed}: requestAdapter() returned null"


def test_the_adapter_agrees_with_the_webgl_claim(two_profiles):
    """The measurement in the module docstring, as an assertion."""
    for seed, p in two_profiles.items():
        renderer = p["webgl_renderer"]
        vendor = p["adapter"]["vendor"]
        if "NVIDIA" in renderer:
            assert vendor == "nvidia", (seed, renderer, p["adapter"])
        elif "AMD" in renderer:
            assert vendor == "amd", (seed, renderer, p["adapter"])
        elif "Intel" in renderer:
            assert vendor == "intel", (seed, renderer, p["adapter"])
        else:
            pytest.fail(f"{seed}: unexpected renderer {renderer}")


def test_the_adapter_does_not_describe_this_host(two_profiles):
    """Unspoofed, every profile reported the host's real card identically --
    a contradiction with the WebGL claim and a link between profiles."""
    for seed, p in two_profiles.items():
        info = p["adapter"]
        assert info["architecture"] != "gen-12lp" or "Iris" in p["webgl_renderer"], (
            f"{seed}: reported this host's own architecture: {info}"
        )


def test_the_adapter_is_still_a_working_one(two_profiles):
    """Only the four identity fields are replaced. A synthesised adapter that
    failed on first use would be a louder tell than the one it replaced."""
    for seed, p in two_profiles.items():
        assert p["limit"] and p["limit"] >= 8192, (seed, p["limit"])
        assert p["feature_count"] > 0, seed
        assert p["device_ok"] is True, (seed, p["device_ok"])


def test_two_profiles_can_claim_different_cards(two_profiles):
    """If the pool gave both the same card this says nothing, so it only
    asserts consistency when the WebGL claims actually differ."""
    a, b = two_profiles[111], two_profiles[222]
    if a["webgl_renderer"] != b["webgl_renderer"]:
        assert a["adapter"] != b["adapter"], (a["adapter"], b["adapter"])
