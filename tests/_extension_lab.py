"""Shared live-extension measurement helpers.

Not a test module. It lives here, imported as ``from _extension_lab import
...``, for the same reason ``_route_lab`` does: pytest puts ``tests/`` on
``sys.path`` and ``tests`` is not a package, so a cross-module
``from tests.test_extension_live import ...`` resolves locally and **fails in
CI**, where PYTHONPATH is ``src``. That is not hypothetical -- it silently
removed a twelve-test acceptance suite from one CI run.

Everything here reads what the browser reports. The marker a content script
writes into a page, and values read out of ``chrome.storage.local`` from
inside the extension's own popup.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Site:
    """A local page the content script is allowed to touch."""

    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>ext</title><body>page"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/e"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _page_target(port: int, timeout: float = 25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"})
            targets = json.loads(urllib.request.urlopen(req, timeout=3).read())
            page = next((t for t in targets if t.get("type") == "page"
                         and "127.0.0.1" in (t.get("url") or "")), None)
            if page:
                return page
        except Exception:  # noqa: BLE001 - the browser may not be up yet
            pass
        time.sleep(0.4)
    return None


def _evaluate(port: int, expression: str, *, await_promise: bool = False,
              timeout: float = 30.0):
    import websocket

    page = _page_target(port, timeout=timeout)
    if page is None:
        return {"error": "no page target"}
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=timeout,
                                     suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": await_promise,
            "returnByValue": True}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                return json.loads(value) if value else {"error": "no value"}
    finally:
        ws.close()
    return {"error": "timeout"}


#: What the page can see about the extension without any extension API.
_MARKER = """
JSON.stringify({
  marker: !!document.getElementById('mp-test-ext-marker'),
  id: (document.getElementById('mp-test-ext-marker')||{dataset:{}}).dataset.extid || null,
  version: (document.getElementById('mp-test-ext-marker')||{dataset:{}}).dataset.version || null
})
"""


def _read_marker(port: int) -> dict:
    return _evaluate(port, _MARKER)



def _extension_storage(port: int, ext_id: str, *, write: dict | None = None,
                       timeout: float = 40.0):
    """Read or write ``chrome.storage.local`` from inside the extension itself.

    Driven through the extension's own popup page over CDP, because that is a
    real extension context: a web page cannot touch ``chrome.storage``, and
    faking it from the page would test nothing.

    The readiness poll is not decoration. A freshly opened extension page
    answers ``Runtime.evaluate`` before Blink has bound the extension APIs
    into it, so the first evaluation raised "cannot read properties of
    undefined (reading 'local')" and the write silently did nothing while the
    read appeared to work -- which looked exactly like storage not persisting.
    """
    import websocket

    popup = f"chrome-extension://{ext_id}/popup.html"
    deadline = time.time() + timeout
    target = None
    while time.time() < deadline and target is None:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{popup}",
                headers={"Host": f"localhost:{port}"}, method="PUT")
            target = json.loads(urllib.request.urlopen(req, timeout=5).read())
        except Exception:  # noqa: BLE001 - retry while the browser settles
            time.sleep(0.4)
    if target is None:
        return {"error": "could not open the extension popup"}

    try:
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True)
    except Exception as e:  # noqa: BLE001 - reported, not raised
        return {"error": f"could not attach to the popup: {e}"}

    next_id = [0]

    def ev(expression: str, await_promise: bool = False):
        next_id[0] += 1
        mid = next_id[0]
        ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": await_promise,
            "returnByValue": True}}))
        stop = time.time() + timeout
        while time.time() < stop:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid:
                return msg
        return {"error": "timeout"}

    try:
        # Wait for the extension APIs to exist in this realm.
        ready = False
        stop = time.time() + timeout
        while time.time() < stop:
            msg = ev("typeof chrome !== 'undefined' && !!(chrome.storage "
                     "&& chrome.storage.local)")
            value = ((msg.get("result") or {}).get("result") or {}).get("value")
            if value is True:
                ready = True
                break
            time.sleep(0.3)
        if not ready:
            return {"error": "chrome.storage never became available in the popup"}

        if write is not None:
            msg = ev(
                "new Promise(r => chrome.storage.local.set(" + json.dumps(write)
                + ", () => chrome.storage.local.get(null,"
                " a => r(JSON.stringify(a)))))", await_promise=True)
        else:
            msg = ev("new Promise(r => chrome.storage.local.get(null,"
                     " a => r(JSON.stringify(a))))", await_promise=True)
        res = (msg.get("result") or {}).get("result") or {}
        if res.get("value"):
            return json.loads(res["value"])
        return {"error": json.dumps(msg)[:300]}
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001,S110 - teardown
            pass


#: What a page can see about extension A without any extension API.
MARKER_A = """
JSON.stringify({
  marker: !!document.getElementById('mp-test-ext-marker'),
  id: (document.getElementById('mp-test-ext-marker')||{dataset:{}}).dataset.extid || null,
  version: (document.getElementById('mp-test-ext-marker')||{dataset:{}}).dataset.version || null
})
"""

#: The same for extension B, which carries a distinct marker id so that
#: "both ran" cannot be satisfied by one of them running twice.
MARKER_B = """
JSON.stringify({
  marker: !!document.getElementById('mp-test-ext-b-marker'),
  id: (document.getElementById('mp-test-ext-b-marker')||{dataset:{}}).dataset.extid || null,
  version: (document.getElementById('mp-test-ext-b-marker')||{dataset:{}}).dataset.version || null
})
"""


def read_marker(port: int) -> dict:
    return _evaluate(port, MARKER_A)


def read_marker_b(port: int) -> dict:
    return _evaluate(port, MARKER_B)


# ---------------------------------------------------------------------------
# The coverage gate
# ---------------------------------------------------------------------------

#: When set to ``1``, a browser that cannot load unpacked extensions is a
#: **failure** rather than a skip.
REQUIRE_ENV = "MP_REQUIRE_EXTENSION_TESTS"


def require_unpacked_support(module: str) -> object:
    """Gate a live extension module on the engine, and fail if coverage is required.

    Skipping was the right default and the wrong outcome. CI skipped these
    suites for the entire life of the feature -- the runner image ships branded
    Google Chrome, ``--load-extension`` is gone from branded Chrome 137+, so the
    capability gate correctly refused and twelve acceptance tests quietly did
    not run. The job stayed green. A PR was then described as having passing
    extension coverage in CI when it had none, which had to be corrected in the
    PR body after the fact.

    ``-rs`` made the skip visible; it did not make it *fail*. So the job that is
    responsible for this coverage sets ``MP_REQUIRE_EXTENSION_TESTS=1`` and a
    missing engine becomes red. A developer on branded Chrome still gets a skip,
    which is honest for them: their browser genuinely cannot do it.

    Returns the resolved capability so the caller can report the binary it got.
    """
    import pytest

    from multiprofile.engine_choice import resolve

    cap = resolve()
    if cap.unpacked:
        return cap

    detail = (
        f"{module}: this browser cannot load unpacked extensions: {cap.reason} "
        f"(binary={cap.binary}, origin={cap.origin})"
    )
    import os

    if os.environ.get(REQUIRE_ENV) == "1":
        pytest.fail(
            detail
            + f"\n\n{REQUIRE_ENV}=1, so this is a failure and not a skip. This "
            "environment is responsible for extension acceptance coverage and "
            "has no engine that can provide it. Install Chromium or Chrome for "
            "Testing and point MULTIPROFILE_ENGINE_BINARY at it. Branded Google "
            "Chrome 137+ cannot load unpacked extensions and no flag changes "
            "that.",
            pytrace=False,
        )
    pytest.skip(detail, allow_module_level=True)
    return cap

def wait_for_local_storage_on_disk(profile_id: str, marker: str,
                                   timeout: float = 20.0) -> bool:
    """Wait until a localStorage value is actually durable on disk.

    ``stop_profile`` is SIGTERM plus a wait for the pid, which is the right
    way to stop a browser, but Chromium commits its localStorage LevelDB
    asynchronously and does not run a full shutdown flush on a signal. So
    "the page says the value is set" and "the value survives the process" are
    different claims, and the gap between them is a race against a background
    writer.

    On a developer machine that race is almost always won, which is why
    ``test_profile_data_survives_the_restart_that_applies_an_extension``
    passed locally for the whole life of the feature and failed the first time
    it ran on a loaded CI runner -- reporting "profile data lost on restart"
    when nothing had been lost and nothing was wrong with the product.

    Polling the on-disk store turns the sleep into a measurement: once the
    bytes are there, a restart genuinely must preserve them, and that is the
    claim worth testing. Returns False on timeout so the caller can say
    "Chromium never committed it" rather than "the data was lost".
    """
    import time as _time

    from multiprofile.storage.xdg import chrome_profile_dir

    needle = marker.encode()
    deadline = _time.time() + timeout
    root = chrome_profile_dir(profile_id)
    while _time.time() < deadline:
        for candidate in root.rglob("Local Storage/leveldb/*"):
            if candidate.suffix.lower() not in (".ldb", ".log"):
                continue
            try:
                if needle in candidate.read_bytes():
                    return True
            except OSError:
                continue
        _time.sleep(0.5)
    return False

