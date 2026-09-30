"""F3, measured: a page must not be able to read the hooks' source.

`makeNative` assigned an **own** `toString` to every hooked function. That
defeats `fn.toString()` and nothing else, because
`Function.prototype.toString.call(fn)` never consults an own property.
Measured on a live profile before the fix::

    Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL)
      -> "function toDataURL() { let copy = null; try { copy = n..."
    Function.prototype.toString.call(WebGLRenderingContext.prototype.getParameter)
      -> "function getParameter(param) { // UNMASKED_VENDOR_WEBGL if..."
    Object.prototype.hasOwnProperty.call(toDataURL, 'toString')
      -> true          <- no native function has this, ever
    Object.getOwnPropertyDescriptor(navigator, 'platform')
      -> {get, set, enumerable, configurable}   <- native returns undefined

The WebGL hook handed over its own source comment naming what it spoofs. This
is not a statistical signal; it is the spoofing code giving a page its
implementation, and `Function.prototype.toString.call` is the check every
serious detector actually runs.

The fix patches the one function all of those paths go through, backed by a
WeakMap of what each hook should claim to be, and stops defining accessors on
instances where the real ones live on the prototype.

**What this does not fix, and the tests at the end say so.** The spoofing
still runs in JavaScript inside the page. This closes the surface the finding
measured; it does not make the product undetectable, and `docs/04` §8 keeps
the ceiling stated. These tests exist to hold the closed part closed.
"""
from __future__ import annotations

import json
import threading
import time
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


class _Page:
    def __init__(self) -> None:
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                body = b"<!doctype html><title>mask</title>ok"
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
        return f"http://127.0.0.1:{self._srv.server_address[1]}/mask"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


#: Every probe in F3's table, plus the realm-borrowing bypass that the fix had
#: to anticipate, plus the controls that stop this passing for a bad reason.
_PROBE = r"""
(async () => {
  const FTS = Function.prototype.toString;
  const src = (fn) => { try { return FTS.call(fn); } catch (e) { return 'THREW:' + e.name; } };
  const o = {};

  // --- F3's table, verbatim -------------------------------------------
  o.toDataURL_src = src(HTMLCanvasElement.prototype.toDataURL);
  o.getParameter_src = src(WebGLRenderingContext.prototype.getParameter);
  o.own_toString = Object.prototype.hasOwnProperty.call(
    HTMLCanvasElement.prototype.toDataURL, 'toString');
  o.nav_platform_own = Object.getOwnPropertyDescriptor(navigator, 'platform') !== undefined;
  o.nav_cores_own = Object.getOwnPropertyDescriptor(navigator, 'hardwareConcurrency') !== undefined;

  // The accessor must still exist -- on the prototype, where the real one is.
  o.proto_platform = Object.getOwnPropertyDescriptor(
    Object.getPrototypeOf(navigator), 'platform') !== undefined;

  // --- the masking must describe itself honestly ----------------------
  o.fts_self = src(FTS);
  o.fts_dot_toString = (() => { try { return FTS.toString(); } catch (e) { return 'THREW'; } })();
  o.fts_name = FTS.name;
  o.fts_length = FTS.length;
  o.fts_own_toString = Object.prototype.hasOwnProperty.call(FTS, 'toString');

  // --- controls: masking that lies about everything is its own tell ---
  // A function the product never touched must still report its real source.
  function untouched() { return 42; }
  o.untouched_src = src(untouched);
  // A genuinely native function the product never hooked.
  o.array_push_src = src(Array.prototype.push);
  // Native throws a TypeError for a non-callable `this`. So must we.
  o.non_callable = (() => { try { FTS.call({}); return 'NO THROW'; }
                           catch (e) { return e.constructor.name; } })();

  // --- shape: a hook must look like a native method --------------------
  // Found by diffing against bare Chromium. Every hook was a plain function,
  // which has an own `prototype` and is constructible; a native method is
  // neither, so either check identified the product without reading a line
  // of source and survived the masking above completely.
  const shaped = [
    ['HTMLCanvasElement.prototype', 'toDataURL', 'fn'],
    ['CanvasRenderingContext2D.prototype', 'getImageData', 'fn'],
    ['WebGLRenderingContext.prototype', 'getParameter', 'fn'],
    ['Element.prototype', 'getBoundingClientRect', 'fn'],
    ['AudioBuffer.prototype', 'getChannelData', 'fn'],
    ['Date.prototype', 'getTimezoneOffset', 'fn'],
    ['Intl.DateTimeFormat.prototype', 'resolvedOptions', 'fn'],
    ['Navigator.prototype', 'platform', 'acc'],
    ['Navigator.prototype', 'hardwareConcurrency', 'acc'],
    ['Navigator.prototype', 'languages', 'acc'],
    ['Screen.prototype', 'width', 'acc'],
  ];
  const resolve = (path) => path.split('.').reduce((o, k) => (o ? o[k] : undefined), window);
  o.withPrototype = [];
  o.constructible = [];
  o.shapeChecked = 0;
  for (const [path, prop, kind] of shaped) {
    const holder = resolve(path);
    if (!holder) continue;
    const d = Object.getOwnPropertyDescriptor(holder, prop);
    if (!d) continue;
    const f = kind === 'acc' ? d.get : d.value;
    if (typeof f !== 'function') continue;
    o.shapeChecked++;
    if (Object.prototype.hasOwnProperty.call(f, 'prototype')) {
      o.withPrototype.push(path + '.' + prop);
    }
    try { new f(); o.constructible.push(path + '.' + prop); } catch (e) {}
  }
  // A control: Intl.DateTimeFormat really is a constructor, so it must keep
  // both. Stripping them there would be the anomaly.
  o.dtf_has_prototype = Object.prototype.hasOwnProperty.call(Intl.DateTimeFormat, 'prototype');

  // --- the realm-borrowing bypass -------------------------------------
  // A fresh realm ships a pristine Function.prototype.toString. Borrowing it
  // is the one-line way around masking that only patched the top window.
  try {
    const f = document.createElement('iframe');
    f.style.display = 'none';
    document.documentElement.appendChild(f);
    const clean = f.contentWindow.Function.prototype.toString;
    o.iframe_toDataURL_src = (() => { try { return clean.call(HTMLCanvasElement.prototype.toDataURL); }
                                      catch (e) { return 'THREW:' + e.name; } })();
    o.iframe_getParameter_src = (() => { try { return clean.call(WebGLRenderingContext.prototype.getParameter); }
                                         catch (e) { return 'THREW:' + e.name; } })();
    o.iframe_nav_platform_own =
      Object.getOwnPropertyDescriptor(f.contentWindow.navigator, 'platform') !== undefined;
    f.remove();
  } catch (e) {
    o.iframe_error = String(e);
  }

  // --- a worker is a realm a page can ask anything of -----------------
  try {
    const code = `self.onmessage = () => {
      const FTS = Function.prototype.toString;
      const r = {};
      try { r.getImageData_src = FTS.call(OffscreenCanvasRenderingContext2D.prototype.getImageData); }
      catch (e) { r.getImageData_src = 'N/A'; }
      r.own_toString = Object.prototype.hasOwnProperty.call(
        OffscreenCanvasRenderingContext2D.prototype.getImageData, 'toString');
      r.nav_platform_own = Object.getOwnPropertyDescriptor(navigator, 'platform') !== undefined;
      r.fts_self = FTS.call(FTS);
      function untouched() { return 42; }
      r.untouched_src = FTS.call(untouched);
      r.platform = navigator.platform;
      self.postMessage(JSON.stringify(r));
    };`;
    const w = new Worker(URL.createObjectURL(new Blob([code], {type: 'text/javascript'})));
    o.worker = await new Promise((res) => {
      const t = setTimeout(() => res('TIMEOUT'), 8000);
      w.onmessage = (e) => { clearTimeout(t); res(e.data); };
      w.onerror = (e) => { clearTimeout(t); res('ERR:' + e.message); };
      w.postMessage(1);
    });
    w.terminate();
  } catch (e) { o.worker = 'THREW:' + String(e); }

  // --- the spoofing must still work -----------------------------------
  o.platform = navigator.platform;
  o.cores = navigator.hardwareConcurrency;
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const d = gl.getExtension('WEBGL_debug_renderer_info');
    o.gpu = gl.getParameter(d.UNMASKED_RENDERER_WEBGL);
  } catch (e) { o.gpu = 'ERR'; }

  return JSON.stringify(o);
})()
"""


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    import urllib.request

    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as launcher
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile

    tmp_path = tmp_path_factory.mktemp("mask")
    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({
        "name": "mask", "proxyType": "none", "startUrl": page.url,
        "userAgentId": "win_chrome", "timezone": "America/New_York",
    })["id"]
    try:
        assert launcher.launch_profile(pid, url=page.url, direct=True).get("ok") is True
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
            target["webSocketDebuggerUrl"], timeout=60, suppress_origin=True
        )
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "awaitPromise": True, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 60
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        yield json.loads(value)
    finally:
        try:
            launcher.stop_profile(pid)
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass
        page.close()
        mp.undo()


NATIVE = "[native code]"


# --------------------------------------------------------------------------
# 1. F3's table
# --------------------------------------------------------------------------

def test_a_hooked_function_does_not_hand_over_its_source(measured):
    """The finding's first line. This returned the hook body before."""
    assert NATIVE in measured["toDataURL_src"], measured["toDataURL_src"]


def test_the_webgl_hook_no_longer_names_what_it_spoofs(measured):
    """It returned its own comment, `// UNMASKED_VENDOR_WEBGL`."""
    src = measured["getParameter_src"]
    assert NATIVE in src, src
    assert "UNMASKED" not in src, src
    assert "TARGET_GPU" not in src, src


def test_a_hook_has_no_own_toString(measured):
    """No native function has one. Finding it was a single-property giveaway
    that did not depend on reading any source."""
    assert measured["own_toString"] is False


def test_navigator_has_no_own_platform(measured):
    """Native lives on Navigator.prototype; an own property on the instance
    says someone put it there."""
    assert measured["nav_platform_own"] is False
    assert measured["nav_cores_own"] is False


def test_the_accessor_still_exists_on_the_prototype(measured):
    """Removing the instance copy must not have removed the spoof -- that
    would trade a detectable identity for no identity."""
    assert measured["proto_platform"] is True


# --------------------------------------------------------------------------
# 2. The masking describes itself honestly
# --------------------------------------------------------------------------

def test_the_masking_reports_itself_as_native(measured):
    """A patched toString that reports its own source is the same defect one
    level up, and it is where a detector looks second."""
    assert NATIVE in measured["fts_self"], measured["fts_self"]
    assert NATIVE in measured["fts_dot_toString"], measured["fts_dot_toString"]


def test_the_masking_keeps_the_native_shape(measured):
    assert measured["fts_name"] == "toString"
    assert measured["fts_length"] == 0
    assert measured["fts_own_toString"] is False


# --------------------------------------------------------------------------
# 3. Controls — this must not pass by lying about everything
# --------------------------------------------------------------------------

def test_an_ordinary_function_still_reports_its_real_source(measured):
    """Masking that returns "[native code]" for everything is trivially
    detectable: declare a function, ask for its source, and a browser that
    claims it is native is not a browser. The map is consulted, not assumed."""
    src = measured["untouched_src"]
    assert NATIVE not in src, src
    assert "42" in src, src


def test_a_genuinely_native_function_is_unaffected(measured):
    assert NATIVE in measured["array_push_src"], measured["array_push_src"]


def test_a_non_callable_this_still_throws_a_TypeError(measured):
    """Native throws. Returning a string instead would be its own signal."""
    assert measured["non_callable"] == "TypeError", measured["non_callable"]


# --------------------------------------------------------------------------
# 3b. Shape — the tell that needs no source at all
# --------------------------------------------------------------------------

def test_no_hook_carries_an_own_prototype(measured):
    """Native methods have none. 22 of the replaced functions did, which is a
    one-line identification that the toString masking does not touch:

        'prototype' in HTMLCanvasElement.prototype.toDataURL
    """
    assert measured["shapeChecked"] >= 8, "the probe resolved almost nothing"
    assert measured["withPrototype"] == [], measured["withPrototype"]


def test_no_hook_is_constructible(measured):
    """`new HTMLCanvasElement.prototype.toDataURL()` throws in a real browser
    and did not here. The other half of the same tell."""
    assert measured["constructible"] == [], measured["constructible"]


def test_a_real_constructor_keeps_its_prototype(measured):
    """Intl.DateTimeFormat is genuinely constructed by pages. Stripping its
    prototype to satisfy the rule above would be the anomaly, not the fix."""
    assert measured["dtf_has_prototype"] is True


# --------------------------------------------------------------------------
# 4. The bypass the fix had to anticipate
# --------------------------------------------------------------------------

def test_a_toString_borrowed_from_an_iframe_does_not_reveal_the_hooks(measured):
    """Three lines of JavaScript get a pristine Function.prototype.toString
    from a fresh realm. Masking only the top window would have been defeated
    by it, and this is the first thing an evasion-aware detector tries."""
    assert "iframe_error" not in measured, measured.get("iframe_error")
    assert NATIVE in measured["iframe_toDataURL_src"], measured["iframe_toDataURL_src"]
    src = measured["iframe_getParameter_src"]
    assert NATIVE in src and "UNMASKED" not in src, src


def test_an_iframe_realm_has_no_own_navigator_platform(measured):
    assert measured["iframe_nav_platform_own"] is False


# --------------------------------------------------------------------------
# 5. Worker realms
# --------------------------------------------------------------------------

def _worker(measured):
    raw = measured.get("worker")
    if not isinstance(raw, str) or raw.startswith(("TIMEOUT", "ERR:", "THREW:")):
        pytest.fail(f"worker probe did not run: {raw!r}")
    return json.loads(raw)


def test_a_worker_hook_does_not_hand_over_its_source(measured):
    """The worker script had the same own-toString defect, in a realm a page
    can ask arbitrary questions of."""
    w = _worker(measured)
    if w["getImageData_src"] != "N/A":
        assert NATIVE in w["getImageData_src"], w["getImageData_src"]
    assert w["own_toString"] is False


def test_a_worker_navigator_has_no_own_platform(measured):
    assert _worker(measured)["nav_platform_own"] is False


def test_the_worker_masking_also_reports_itself_as_native(measured):
    assert NATIVE in _worker(measured)["fts_self"]


def test_the_worker_still_reports_real_source_for_its_own_functions(measured):
    w = _worker(measured)
    assert NATIVE not in w["untouched_src"], w["untouched_src"]


def test_the_worker_identity_still_holds(measured):
    """F30's property: the worker and its page are the same machine."""
    assert _worker(measured)["platform"] == measured["platform"]


# --------------------------------------------------------------------------
# 6. The identity must still be the profile's
# --------------------------------------------------------------------------

def test_the_spoofing_still_works(measured):
    """The failure this fix could plausibly have caused: a page that reads
    nothing because nothing was applied."""
    assert measured["platform"] == "Win32", measured["platform"]
    assert isinstance(measured["cores"], int) and measured["cores"] > 0
    assert "ANGLE" in str(measured["gpu"]), measured["gpu"]
