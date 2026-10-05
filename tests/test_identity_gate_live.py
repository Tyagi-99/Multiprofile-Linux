"""The Phase 1 gate: one profile, three states, no contradictions in any.

The parity roadmap sets the bar as *"the same profile makes compatible claims
across these surfaces on first load, new tabs, and restart"*. The existing
suites each cover a slice -- worker realms, the wire identity, restart
stability of the stored seed -- and the three contradictions found on
5 October all sat between slices. So this asserts the whole manifest, in all
three states, through the collector the comparison harness uses, which is
what stops the test and the harness drifting into asking different questions.

Three states, because each has broken independently before:

**First load** is the start URL, which is already open when the injector
attaches, so it is reloaded rather than frozen before its first script.

**A new tab** is the residual F1: its first document request is formed by the
browser process before any renderer override exists, and a tab opened later
is the case a single pinned page session cannot reach.

**After restart** is the promise the whole product rests on -- the identity is
rebuilt from the persisted seed, and a profile that answers differently on
Tuesday is a profile that links itself.
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

from multiprofile.diagnostics.identity_probe import (  # noqa: E402
    REALM_AGREEMENT_FIELDS,
    SERVICE_WORKER_SOURCE,
    WORKER_SOURCE,
    collect_manifest,
    compare_manifests,
)


class _Origin:
    """A real origin, because a service worker cannot register on about:blank."""

    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                if self.path.startswith("/probe-worker.js"):
                    body, ctype = WORKER_SOURCE, "text/javascript"
                elif self.path.startswith("/probe-sw.js"):
                    body, ctype = SERVICE_WORKER_SOURCE, "text/javascript"
                else:
                    body, ctype = b"<!doctype html><title>gate</title>ok", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/gate"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _open_new_tab(port: int, url: str) -> None:
    """A tab opened the way an operator opens one, over the browser target."""
    import websocket

    with urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{port}/json/version",
                               headers={"Host": f"localhost:{port}"}),
        timeout=5,
    ) as response:
        browser_ws = json.loads(response.read())["webSocketDebuggerUrl"]
    ws = websocket.create_connection(browser_ws, timeout=30, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Target.createTarget",
                            "params": {"url": url}}))
        deadline = time.time() + 30
        while time.time() < deadline:
            message = json.loads(ws.recv())
            if message.get("id") == 1:
                assert "error" not in message, message["error"]
                return
        raise AssertionError("Target.createTarget did not answer")
    finally:
        ws.close()


@pytest.fixture(scope="module")
def states(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("gate")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    origin = _Origin()
    out: dict[str, object] = {}
    # No device preset and no explicit timezone: the configuration the
    # 5 October capture came from, and the one most likely to be shipped.
    pid = create_profile({"name": "gate", "proxyType": "none",
                          "startUrl": origin.url})["id"]
    try:
        assert L.launch_profile(pid, url=origin.url, direct=True).get("ok") is True
        time.sleep(2.5)
        port = get_runtime(pid).cdp_port
        out["first_load"] = collect_manifest(port, label="first_load")

        _open_new_tab(port, origin.url + "?tab=2")
        time.sleep(2.0)
        out["new_tab"] = collect_manifest(
            port, label="new_tab", url_fragment="?tab=2")

        L.stop_profile(pid)
        time.sleep(1.5)
        assert L.launch_profile(pid, url=origin.url, direct=True).get("ok") is True
        time.sleep(2.5)
        out["restart"] = collect_manifest(
            get_runtime(pid).cdp_port, label="restart")
        return out
    finally:
        L.stop_profile(pid)
        origin.close()
        mp.undo()


STATES = ("first_load", "new_tab", "restart")

#: Fields that must be identical in all three states. The canvas hash is in
#: here on purpose: it is derived from the persisted seed, so a change across
#: a restart means the seed did not survive.
STABLE_FIELDS = tuple(REALM_AGREEMENT_FIELDS)


@pytest.mark.parametrize("state", STATES)
def test_the_state_was_measured_at_all(states, state):
    manifest = states[state]
    assert not manifest.errors, (state, manifest.errors)
    assert manifest.observations.get("page"), state


@pytest.mark.parametrize("state", STATES)
def test_the_state_has_no_contradictions(states, state):
    """The gate. A contradiction needs no control: no real browser produces
    one, so each is a failure on its own."""
    findings = compare_manifests(states[state])
    contradictions = [f for f in findings if f.kind == "contradiction"]
    assert not contradictions, "\n".join(
        f"{state}: {f.surface}: {f.detail} (got {f.product!r}, "
        f"expected {f.control!r})" for f in contradictions
    )


@pytest.mark.parametrize("field", STABLE_FIELDS)
def test_the_identity_is_the_same_in_every_state(states, field):
    """A profile that answers differently on a new tab or after a restart
    links itself across sessions."""
    values = {}
    for state in STATES:
        page = states[state].observations.get("page") or {}
        if field in page:
            values[state] = page[field]
    assert len(set(map(repr, values.values()))) <= 1, (field, values)


def test_the_measurement_is_not_vacuous(states):
    """Positive control. Three empty manifests agree with each other."""
    page = states["first_load"].observations.get("page") or {}
    assert page.get("userAgent"), page
    assert page.get("uaPlatform"), page
    assert page.get("canvasHash") is not None, page
    geometry = states["first_load"].observations.get("geometry") or {}
    assert (geometry.get("screenW") or 0) > 100, geometry
    # ...and every realm actually answered, or "no contradictions" is only
    # true because nothing was asked.
    for realm in ("worker_blob", "shared", "service", "iframe"):
        got = states["first_load"].observations.get(realm) or {}
        assert isinstance(got, dict) and got.get("userAgent"), (realm, got)
