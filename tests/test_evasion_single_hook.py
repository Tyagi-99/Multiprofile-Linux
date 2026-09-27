"""evasion.js must reach a page exactly once.

There were two routes into the page and both were live. The launcher passed
``--load-extension`` and ``--disable-extensions-except`` AND the CDP injector
called ``Page.addScriptToEvaluateOnNewDocument``. Google Chrome refuses the
flags outright::

    --disable-extensions-except is not allowed in Google Chrome, ignoring

which is why the CDP route was written in the first place -- but Chromium
honours them, so on Chromium the script ran twice and on Chrome once. Measured
on Chromium 141 with a real ``http://`` document, before the fix::

    [Evasion] evasion.js active on: http://127.0.0.1:40679/hooks
    [Evasion] evasion.js active on: http://127.0.0.1:40679/hooks

and after::

    [Evasion] evasion.js active on: http://127.0.0.1:44223/hooks

Two runs mean canvas and WebGL noise applied twice, so the rendered
fingerprint is not the one the config specifies and is not reproducible across
engines -- and the behaviour silently differed between Chrome and Chromium,
which is exactly what makes fingerprint bugs impossible to reproduce.

(The audit measured this as ``toString(toDataURL).match(/copy = null/g).length
== 2``. That count is 2 for a SINGLE layer -- the wrapper's source contains
``let copy = null`` and ``catch(e) { copy = null; }`` -- so it is not a layer
count. The console line above is, and the conclusion held.)

Note the canvas hook already carried an ``alreadyShielded`` guard for this
case, which is why the double run did not double-wrap ``toDataURL``. The other
hooks have no such guard, and relying on per-hook guards to survive a second
whole-script execution is not a design. One route is.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

LAUNCHER = (
    Path(__file__).resolve().parent.parent
    / "src" / "multiprofile" / "browser" / "launcher.py"
)


# ---------------------------------------------------------------------------
# one route, asserted on the launch command
# ---------------------------------------------------------------------------


def _flag_literals() -> list[str]:
    """Every string the launcher can actually hand the browser.

    Checked as literals (including f-string parts) so the comment that
    explains this history does not trip the test, but a re-added flag does.
    """
    import ast

    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("--"):
                out.append(node.value)
    return out


def test_the_browser_is_not_told_to_load_an_extension():
    flags = _flag_literals()
    assert not any(f.startswith("--load-extension") for f in flags), flags
    assert not any(f.startswith("--disable-extensions-except") for f in flags), flags


def test_exclusivity_now_means_no_extensions_at_all():
    """Our script arrives over CDP, so there is nothing to except."""
    assert "--disable-extensions" in _flag_literals()
    assert "allow_webstore_extensions" in LAUNCHER.read_text(encoding="utf-8")


def test_the_staging_copy_is_gone():
    from multiprofile.browser import launcher

    assert not hasattr(launcher, "_prepare_extension")
    assert "shutil.copytree" not in LAUNCHER.read_text(encoding="utf-8")


def test_the_identity_script_bundle_is_still_found():
    """It is now a script SOURCE, not something the browser loads."""
    from multiprofile.browser.launcher import extension_src

    assert (extension_src() / "evasion.js").exists()


def test_the_token_substitutions_survived_the_removal():
    """The staging step used to replace these; losing them silently would
    have dropped the profile's timezone, seed and WebRTC IP."""
    from multiprofile.browser.cdp_inject import build_identity_script

    out = build_identity_script(
        {"seed": 5},
        'let tz = "__PROFILE_TIMEZONE__"; let ip = "__PROFILE_ALLOCATED_IP__"; '
        'let s = "__PROFILE_SEED__";',
        replacements={
            "__PROFILE_TIMEZONE__": "Europe/Berlin",
            "__PROFILE_ALLOCATED_IP__": "203.0.113.9",
            "__PROFILE_SEED__": "5",
        },
    )
    assert "Europe/Berlin" in out
    assert "203.0.113.9" in out
    assert "__PROFILE_" not in out


def test_the_launcher_passes_those_substitutions():
    source = LAUNCHER.read_text(encoding="utf-8")
    for token in ("__PROFILE_TIMEZONE__", "__PROFILE_ALLOCATED_IP__", "__PROFILE_SEED__"):
        assert token in source, f"{token} is no longer substituted for the page"


# ---------------------------------------------------------------------------
# one route, measured in a real browser
# ---------------------------------------------------------------------------


def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


live = pytest.mark.skipif(not _chrome_available(), reason="Chrome is not installed")


class _Page:
    """A real http:// document — content scripts do not run on data: or about:."""

    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>hooks</title><body>probe</body>"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/hooks"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _cdp_json(port: int, path: str):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"Host": f"localhost:{port}"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")


@live
@pytest.mark.network
def test_a_launched_browser_loads_no_extension(xdg):
    """The second injection route, observed at its source.

    Before the fix the target list carried
    ``chrome-extension://…/background.js`` on Chromium, and that extension's
    manifest injects evasion.js as a MAIN-world content script on every page.
    """
    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    pid = create_profile({
        "name": "one-hook-targets", "proxyType": "none", "startUrl": "about:blank",
    })["id"]
    try:
        res = L.launch_profile(pid, url="about:blank", direct=True)
        assert res.get("ok") is True, res.get("error")
        assert not any(a.startswith("--load-extension") for a in res["cmd"])
        time.sleep(1.0)
        targets = _cdp_json(get_runtime(pid).cdp_port, "/json")
    finally:
        L.stop_profile(pid)

    extensions = [t for t in targets if "chrome-extension://" in (t.get("url") or "")]
    assert extensions == [], f"an extension is loaded again: {extensions}"


@live
@pytest.mark.network
def test_evasion_runs_exactly_once_per_document(xdg):
    """THE hook-layer test: count the script's own executions in one document."""
    import websocket

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    page = _Page()
    pid = create_profile({
        "name": "one-hook-runs", "proxyType": "none", "startUrl": "about:blank",
    })["id"]
    lines: list[str] = []
    try:
        assert L.launch_profile(pid, url="about:blank", direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        time.sleep(1.0)
        target = next(t for t in _cdp_json(port, "/json") if t.get("type") == "page")
        ws = websocket.WebSocket()
        ws.connect(target["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)
        ws.settimeout(1.0)
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.enable", "params": {}}))
            ws.send(json.dumps({
                "id": 2, "method": "Page.navigate", "params": {"url": page.url},
            }))
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    msg = json.loads(ws.recv())
                except Exception:  # noqa: BLE001 - idle recv timeout
                    continue
                if msg.get("method") != "Runtime.consoleAPICalled":
                    continue
                text = " ".join(
                    str(a.get("value"))
                    for a in msg["params"].get("args") or []
                    if "value" in a
                )
                if "[Evasion]" in text and page.url in text:
                    lines.append(text)
        finally:
            ws.close()
    finally:
        L.stop_profile(pid)
        page.close()

    assert len(lines) == 1, (
        f"evasion.js ran {len(lines)} times in one document — two hook layers "
        f"mean the canvas/WebGL noise is applied twice: {lines}"
    )
