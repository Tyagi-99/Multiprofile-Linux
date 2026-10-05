"""A Windows profile must enumerate speech voices.

``speechSynthesis.getVoices()`` returned an empty array. Measured on this
host, both in a profile and in stock Chromium 151::

    voices: 0

That is normal for a Linux box with no speech-dispatcher installed, and
impossible for the Windows machine the profile claims to be. Windows ships
Microsoft David and Zira with the operating system, so every Chrome on
Windows enumerates at least those. An empty voice list on a ``Win32``
navigator is a platform contradiction, and it is one of the cheapest checks
a probe can run: one property access, no permission, no user gesture.

The core set is deliberately **not** randomised per profile. Nearly every
Windows install has exactly David and Zira, so identical lists are what real
machines look like here; adding noise would make profiles less plausible
rather than more. Only the optional language packs vary, because those
genuinely differ between machines.
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
(() => {
  const V = speechSynthesis.getVoices();
  const o = {
    count: V.length,
    names: V.map(v => v.name),
    langs: V.map(v => v.lang),
    local: V.filter(v => v.localService).length,
    defaults: V.filter(v => v.default).length,
    proto_ok: V.length ? V[0] instanceof SpeechSynthesisVoice : null,
    uris: V.map(v => v.voiceURI)
  };
  try { o.iframe = document.getElementById('f').contentWindow
          .speechSynthesis.getVoices().length; }
  catch (e) { o.iframe = 'ERR:' + e.name; }
  return JSON.stringify(o);
})()
"""


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>v</title><iframe id=f srcdoc='x'></iframe>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/v"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _probe(seed: int, tmp_path, ua_id: str = "win_chrome") -> dict:
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
    # `userAgentId` is explicit. This file is about the voices a *Windows*
    # profile presents (F29), and its Win32 platform used to come from
    # `_detect_pool("")`'s default. That default is now the host's own
    # platform, so the Windows claim has to be made rather than inherited --
    # and a Linux profile reporting zero voices is correct, because a Linux
    # desktop without speech-dispatcher has none. See
    # `fingerprint/identity_matrix.py`.
    pid = create_profile({
        "name": f"v{seed}", "proxyType": "none", "startUrl": page.url, "seed": seed,
        "userAgentId": ua_id,
    })["id"]
    try:
        assert L.launch_profile(pid, url=page.url, direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        time.sleep(2.5)
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
            "params": {"expression": _PROBE, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 30
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
    return {s: _probe(s, tmp_path_factory.mktemp(f"v{s}")) for s in (111, 222)}


def test_the_voice_list_is_not_empty(two_profiles):
    """The measurement in the module docstring, as an assertion."""
    for seed, p in two_profiles.items():
        assert p["count"] > 0, f"{seed}: a Win32 navigator with no voices"


def test_the_windows_voices_windows_always_has_are_present(two_profiles):
    """David and Zira ship with the operating system."""
    for seed, p in two_profiles.items():
        joined = " ".join(p["names"])
        assert "Microsoft David" in joined, (seed, p["names"])
        assert "Microsoft Zira" in joined, (seed, p["names"])


def test_exactly_one_voice_is_the_default(two_profiles):
    """Marking every local voice whose language matches flagged three at
    once -- a more specific anomaly than the empty list it replaced."""
    for seed, p in two_profiles.items():
        assert p["defaults"] == 1, (seed, p["defaults"], p["names"])


def test_the_voices_are_real_speech_synthesis_voices(two_profiles):
    """A plain object would fail instanceof."""
    for seed, p in two_profiles.items():
        assert p["proto_ok"] is True, seed


def test_local_and_network_voices_are_both_represented(two_profiles):
    """Chrome's own network voices sit alongside the OS ones; a list that
    was all-local or all-remote is not a shape Chrome produces."""
    for seed, p in two_profiles.items():
        assert 0 < p["local"] < p["count"], (seed, p["local"], p["count"])


def test_every_voice_has_a_uri_and_a_language(two_profiles):
    for seed, p in two_profiles.items():
        assert all(p["uris"]), (seed, p["uris"])
        assert all("-" in lang for lang in p["langs"]), (seed, p["langs"])


def test_an_iframe_sees_the_same_voices(two_profiles):
    for seed, p in two_profiles.items():
        assert p["iframe"] == p["count"], (seed, p["iframe"], p["count"])


def test_the_core_voices_are_shared_and_the_optional_ones_vary(two_profiles):
    """Real Windows machines agree on David and Zira and differ on language
    packs, so the core set must match across profiles while the tail need
    not."""
    a, b = two_profiles[111], two_profiles[222]
    core = {"Microsoft David - English (United States)",
            "Microsoft Zira - English (United States)"}
    assert core <= set(a["names"]) and core <= set(b["names"])
    assert set(a["names"]) != set(b["names"]), "no optional pack varied"


# --------------------------------------------------------------------------
# The other direction, which matters more now that Linux is the default
# --------------------------------------------------------------------------

def test_a_linux_profile_does_not_present_windows_voices(tmp_path_factory):
    """A Windows voice list on a Linux claim would be a contradiction.

    `speechSynthesis.getVoices()` is OS-shaped: a Windows machine has
    Microsoft David and friends, and a Linux desktop without
    speech-dispatcher installed has **none at all**. So zero voices is the
    correct and ordinary answer for a Linux claim, and "Microsoft David on a
    Linux navigator" would be the F29 contradiction pointed the other way.

    Worth asserting rather than assuming, because the Windows voice list is
    injected and an injection that ignores the claim would be invisible until
    someone measured it -- which is how the three contradictions of
    5 October 2026 survived.
    """
    probe = _probe(333, tmp_path_factory.mktemp("v-linux"), ua_id="linux_chrome")
    joined = " ".join(probe["names"])
    assert "Microsoft" not in joined, probe["names"]
    assert "Microsoft David" not in joined, probe["names"]
