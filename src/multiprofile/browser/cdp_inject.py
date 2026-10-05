"""Identity injection over CDP — the replacement for the browser extension.

WHY THIS EXISTS
---------------
The per-profile MV3 extension was responsible for three things: running the
fingerprint script in the page, answering proxy authentication challenges,
and seeding cookies. It did none of them, because Google Chrome refuses the
flags that load it::

    WARNING:chrome/browser/extensions/extension_service.cc:441]
    --disable-extensions-except is not allowed in Google Chrome, ignoring.

Verified on Chrome 151: zero of our extensions appear in the profile's
``Preferences``, and the extension's script never ran on a real
``http://`` page. Profiles were browsing with the host's own fingerprint
while the console reported them healthy, and authenticated proxies could
never complete a challenge.

CDP can do all three on stock Chrome, with no extension and no flags:

    Page.addScriptToEvaluateOnNewDocument   fingerprint, before page script
    Fetch.enable{handleAuthRequests:true}   proxy auth, password off argv
    Network.setCookies                      cookie seeding

It also carries the fourth job the extension never had: the HTTP-layer
identity. ``Network.setUserAgentOverride`` is applied here, per attached
target, because this is the only session that lives as long as the browser
AND sees every tab. Anywhere else it is discarded (see F1 in
``docs/02_AUDIT_REPORT.md``) or covers only the first tab.

THE SESSION MUST STAY OPEN
--------------------------
``cdp_identity.PinnedCdpSession`` documents the rule this module obeys:
Chromium scopes overrides to the DevTools session that set them, and they
vanish when it disconnects. So this runs a long-lived connection on a daemon
thread for the whole life of the browser, not a connect-send-close helper.

NEW TABS ARE COVERED
--------------------
``Target.setAutoAttach`` with ``waitForDebuggerOnStart`` catches every new
page *before* it runs a line of script, so a tab opened by the operator --
or by ``window.open`` -- gets the same identity as the first one.

CREDENTIALS
-----------
The proxy password is held in memory for the lifetime of the browser and is
written to no log, no command line and no file. ``__repr__`` is overridden
so a traceback cannot print it either.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import identity_faults
from .cdp_identity import (
    IDENTITY_PROBE_PATH,
    UA_OVERRIDE_METHOD,
    low_entropy_client_hints,
)

log = logging.getLogger("multiprofile.browser.cdp_inject")

try:
    import websocket
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    websocket = None  # type: ignore

_CONNECT_TIMEOUT = 8.0
_RECV_TIMEOUT = 1.0


def _first_request_hints_enabled() -> bool:
    """Whether to arm browser-scope Fetch for the first-request hint fix.

    Read at attach time rather than captured at import, so a test or an
    operator can turn it off without rebuilding anything. Never raises: a
    missing config module must not stop a browser launching.
    """
    try:
        from ..config import get_settings

        return bool(get_settings().first_request_hints)
    except Exception:  # noqa: BLE001 - default to the safe (correct) behaviour
        return True


_WORKER_TARGET_TYPES = frozenset({"service_worker", "shared_worker", "worker"})

#: Worker target types that do NOT inherit the owner document's UA override.
#:
#: A dedicated worker runs in its owner document's renderer, so
#: ``Network.setUserAgentOverride`` applied to the page target covers it for
#: free. A shared worker and a service worker each get their own target and
#: their own process, and inherit nothing -- which is exactly the split a
#: CreepJS run on this host measured (see ``_identify_worker``).
_OWN_PROCESS_WORKER_TYPES = frozenset({"service_worker", "shared_worker"})

#: Error label for a UA override sent to a worker target. See ``_send``.
_WORKER_UA_LABEL = "Network.setUserAgentOverride[worker]"


def build_worker_identity_script(fingerprint: dict[str, Any]) -> str:
    """The identity a worker realm gets, as a self-contained script.

    ``evasion.js`` is written against a window: it touches ``document``,
    ``HTMLIFrameElement`` and ``screen``, none of which exist in a worker. It
    used to reach workers by wrapping the ``Worker`` constructor and building
    a new blob that ran a prelude and then ``importScripts()`` the real
    script. That covered dedicated workers and nothing else, and it broke one
    of them.

    Two failures came out of it, both measured on this host.

    A **service worker** is registered by URL and fetched by the browser, so
    no constructor wrapper can reach it. One page reported::

        top document     platform: Win32          hardwareConcurrency: 8
        service worker   platform: Linux x86_64   hardwareConcurrency: 16

    The User-Agent was right in both, because that comes from a process-wide
    flag, and so was the timezone, because that is set on the engine. Only the
    values spoofed in JavaScript were missing -- and they were missing in the
    one realm that outlives the page. A service worker persists across
    navigations and can be messaged by any page in scope, so a script reading
    the host's real platform and core count through one gets a stable
    cross-profile identifier for free.

    A **module worker** has no ``importScripts()``. The wrapper called it
    anyway inside a ``try/catch``, so the prelude loaded, the real script
    silently never did, and the worker answered nothing::

        bare Chromium 151   classic: Linux x86_64/16   module: Linux x86_64/16
        profile (wrapper)   classic: Win32/12          module: TIMEOUT

    That breaks any site whose bundler emits a module worker, and a browser
    where module workers hang is not the Chrome the User-Agent claims.

    So the wrapper is gone and this is the single mechanism. The worker is
    auto-attached with ``waitForDebuggerOnStart``, which freezes it before its
    first line, so evaluating here lands ahead of the worker's own code --
    strictly more reliable than rewriting its source, and it reaches dedicated,
    module, shared and service workers alike. Having exactly one mechanism
    also matters for the canvas noise below: applying it twice would give a
    classic worker a different fingerprint from a module one.
    """
    gpu = fingerprint.get("gpu") or {}
    payload = {
        "platform": fingerprint.get("platform") or "",
        "cores": fingerprint.get("cores"),
        "memory": fingerprint.get("memory"),
        "language": fingerprint.get("language") or "",
        "languages": list(fingerprint.get("languages") or []),
        "gpuVendor": gpu.get("vendor") or "",
        "gpuRenderer": gpu.get("renderer") or "",
        "seed": int(fingerprint.get("seed") or 1),
    }
    cfg = json.dumps(payload, separators=(",", ":"))
    return _WORKER_SCRIPT_TEMPLATE.replace("__MP_WORKER_CFG__", cfg)


# The worker-side body. Kept as one string so the values it depends on come
# from the fingerprint above and nowhere else.
_WORKER_SCRIPT_TEMPLATE = r"""
(() => { try {
  const C = __MP_WORKER_CFG__;
  const nav = self.navigator;
  const proto = self.WorkerNavigator ? self.WorkerNavigator.prototype : null;

  // Native-function masking, same as the page's (F3). A worker is a realm a
  // page can ask arbitrary questions of, so the two tells the page had were
  // both reachable here: an own `toString` on each hook, which
  // Function.prototype.toString.call() ignores while reporting the real
  // source, and `Object.prototype.hasOwnProperty.call(fn, 'toString')`
  // returning true, which no native function does.
  const nativeSources = new WeakMap();
  // Method shorthand, not a plain function: a plain function has an own
  // `prototype` and is constructible, and a native method is neither, so
  // `'prototype' in OffscreenCanvasRenderingContext2D.prototype.getImageData`
  // identified the product in one line without reading any source. The
  // property is non-configurable, so it can only be avoided at construction.
  // `length` is restored because rest parameters report 0.
  const native = (fn, name) => {
    try {
      const arity = fn.length;
      const holder = {
        [name](...args) { return Reflect.apply(fn, this, args); }
      };
      const shim = holder[name];
      Object.defineProperty(shim, 'length', {value: arity, configurable: true});
      nativeSources.set(shim, 'function ' + name + '() { [native code] }');
      return shim;
    } catch (e) {
      try { nativeSources.set(fn, 'function ' + name + '() { [native code] }'); } catch (e2) {}
      return fn;
    }
  };
  try {
    const FnProto = self.Function && self.Function.prototype;
    if (FnProto) {
      const original = FnProto.toString;
      const masked = new Proxy(original, {
        apply: function (target, thisArg, args) {
          const claimed = nativeSources.get(thisArg);
          if (claimed !== undefined) return claimed;
          return Reflect.apply(target, thisArg, args);
        }
      });
      nativeSources.set(masked, 'function toString() { [native code] }');
      Object.defineProperty(FnProto, 'toString', {
        value: masked, writable: true, enumerable: false, configurable: true
      });
    }
  } catch (e) {}

  // Prototype when there is one, instance only as a fallback. Defining on
  // both shadowed correctly and left an own property on navigator that a real
  // one does not have -- getOwnPropertyDescriptor(navigator, 'platform')
  // answered where native returns undefined.
  const put = (key, value) => {
    if (value === null || value === undefined) return;
    const get = native(function () { return value; }, 'get ' + key);
    const target = proto || nav;
    if (!target) return;
    try { Object.defineProperty(target, key, {get: get, configurable: true, enumerable: true}); }
    catch (e) {}
  };
  if (nav) {
    put('platform', C.platform);
    put('hardwareConcurrency', C.cores);
    put('deviceMemory', C.memory);
    put('language', C.language);
    put('languages', Object.freeze(C.languages.slice()));
  }

  // Canvas and WebGL noise, identical to the page's, because a worker can
  // render through OffscreenCanvas and a probe can compare the two.
  const SEED = C.seed | 0;
  function pixelHash(x, y) {
    let h = Math.imul(SEED ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(x + 1, 0xc2b2ae35) ^ Math.imul(y + 1, 0x27d4eb2f);
    h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
    h = Math.imul(h ^ (h >>> 12), 0x297a2d39);
    return (h ^ (h >>> 15)) >>> 0;
  }
  function addNoise(data, ox, oy, w, h) {
    if (w * h > 4000000) return;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        if (data[i + 3] === 0) continue;
        const r = pixelHash(ox + x, oy + y);
        if ((r & 15) !== 0) continue;
        const up = ((r >>> 8) & 1) === 1;
        const a = data[i + 3];
        if (a === 255) {
          const c = i + ((r >>> 4) % 3);
          const v = data[c];
          data[c] = up ? (v === 255 ? 254 : v + 1) : (v === 0 ? 1 : v - 1);
        } else {
          data[i + 3] = (up || a === 1) ? a + 1 : a - 1;
        }
      }
    }
  }
  try {
    if (self.OffscreenCanvasRenderingContext2D) {
      const orig = self.OffscreenCanvasRenderingContext2D.prototype.getImageData;
      self.OffscreenCanvasRenderingContext2D.prototype.getImageData = native(
        function getImageData(sx, sy, sw, sh) {
          const img = orig.apply(this, arguments);
          try { addNoise(img.data, 0, 0, img.width, img.height); } catch (e) {}
          return img;
        }, 'getImageData');
    }
  } catch (e) {}
  try {
    const hookGL = function (p) {
      if (!p || !p.getParameter) return;
      const origGP = p.getParameter;
      p.getParameter = native(function getParameter(param) {
        if (param === 37445 && C.gpuVendor) return C.gpuVendor;
        if (param === 37446 && C.gpuRenderer) return C.gpuRenderer;
        return origGP.apply(this, arguments);
      }, 'getParameter');
      if (p.readPixels) {
        const origRP = p.readPixels;
        p.readPixels = native(function readPixels(x, y, w, h, format, type, pixels) {
          const ret = origRP.apply(this, arguments);
          try {
            if (pixels && pixels.length && w > 0 && h > 0) {
              const len = Math.min(pixels.length, w * h * 4);
              for (let i = 0; i < len; i += 4) {
                const px = (i / 4) | 0;
                const r = pixelHash(px % w, (px / w) | 0);
                if ((r & 15) !== 0) continue;
                if (pixels[i + 3] === 0) continue;
                const c = i + ((r >>> 4) % 3);
                const v = pixels[c];
                pixels[c] = ((r >>> 8) & 1) ? (v === 255 ? 254 : v + 1) : (v === 0 ? 1 : v - 1);
              }
            }
          } catch (e) {}
          return ret;
        }, 'readPixels');
      }
    };
    if (self.WebGLRenderingContext) hookGL(self.WebGLRenderingContext.prototype);
    if (self.WebGL2RenderingContext) hookGL(self.WebGL2RenderingContext.prototype);
  } catch (e) {}
} catch (e) {} })()
"""


def build_identity_script(
    fingerprint: dict[str, Any],
    evasion_source: str,
    *,
    replacements: dict[str, str] | None = None,
) -> str:
    """Prefix the evasion source with this profile's config.

    This is now the ONLY way ``evasion.js`` reaches a page. The per-profile
    extension copy that used to do the same job is gone (Chromium honoured
    ``--load-extension`` while Chrome refused it, so the hooks were installed
    twice on one engine and once on the other), which makes this function the
    single source of truth for what the page sees — including the
    ``__PROFILE_*`` tokens the staging step used to substitute. Tokens left
    unreplaced default themselves inside the script, so a missing one is
    survivable, but the caller should pass them.
    """
    cfg = json.dumps(fingerprint, separators=(",", ":"))
    body = evasion_source
    for token, value in (replacements or {}).items():
        body = body.replace(token, value)
    if "__PROFILE_CONFIG_JSON__" in body:
        body = body.replace('"__PROFILE_CONFIG_JSON__"', "null")
        body = body.replace("'__PROFILE_CONFIG_JSON__'", "null")

    # The config is a closure variable, not a global.
    #
    # It used to be emitted as `var __MP_INLINE_CFG__ = {...};
    # window.__MP_PROFILE__ = __MP_INLINE_CFG__;` at top level, which put the
    # entire spoofing configuration on `window` where any page could read it:
    # 25 keys, ~2 KB, including the per-profile `seed`. That is worse than
    # doing no spoofing at all. A site reading the seed learns the product's
    # name from one property, derives every noise value the script will
    # produce, and gains a stable cross-site identifier more reliable than any
    # fingerprint -- the profile hands over the thing it exists to protect.
    #
    # `evasion.js` is itself an IIFE, so wrapping the pair in one more
    # function makes the config reachable by closure from the code that needs
    # it and unreachable from the page. Nothing else consumed the globals.
    return "(function(){\n" + f"var __MP_INLINE_CFG__ = {cfg};\n" + body + "\n})();\n"


@dataclass
class ProxyAuth:
    """Proxy credentials held in memory only."""

    username: str
    password: str

    def __repr__(self) -> str:  # never let a traceback print the password
        return f"ProxyAuth(username={self.username!r}, password=<redacted>)"


@dataclass
class InjectionStatus:
    """What actually happened, for the runtime registry and the console."""

    ok: bool = False
    script_installed: bool = False
    auth_armed: bool = False
    cookies_applied: int = 0
    ua_override_applied: bool = False
    timezone_applied: bool = False
    locale_applied: bool = False
    geolocation_applied: bool = False
    screen_metrics_applied: bool = False
    targets_seen: int = 0
    workers_identified: int = 0
    workers_ua_applied: int = 0
    workers_ua_failed: int = 0
    error: str | None = None
    detail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "scriptInstalled": self.script_installed,
            "authArmed": self.auth_armed,
            "cookiesApplied": self.cookies_applied,
            "uaOverrideApplied": self.ua_override_applied,
            "timezoneApplied": self.timezone_applied,
            "localeApplied": self.locale_applied,
            "geolocationApplied": self.geolocation_applied,
            "screenMetricsApplied": self.screen_metrics_applied,
            "targetsSeen": self.targets_seen,
            "workersIdentified": self.workers_identified,
            "workersUaApplied": self.workers_ua_applied,
            "workersUaFailed": self.workers_ua_failed,
            "error": self.error,
            "detail": list(self.detail),
        }


def _browser_ws_url(port: int, timeout_s: float = _CONNECT_TIMEOUT) -> str:
    """Browser-level endpoint — the only one that can auto-attach targets."""
    deadline = time.monotonic() + timeout_s
    last = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.0
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            url = data.get("webSocketDebuggerUrl") or ""
            if url:
                return url
        except Exception as e:  # noqa: BLE001 - retry until the deadline
            last = str(e)
        time.sleep(0.15)
    raise RuntimeError(f"CDP browser endpoint not ready on 127.0.0.1:{port}: {last}")


class IdentityInjector:
    """Holds one browser-level CDP connection and applies identity to every page."""

    def __init__(
        self,
        port: int,
        *,
        script: str | None = None,
        proxy_auth: ProxyAuth | None = None,
        cookies: list[dict[str, Any]] | None = None,
        ua_override: dict[str, Any] | None = None,
        timezone_id: str | None = None,
        worker_script: str | None = None,
        locale: str | None = None,
        geolocation: dict[str, Any] | None = None,
        screen_metrics: dict[str, Any] | None = None,
        profile_id: str = "",
    ):
        self.port = int(port)
        self.timezone_id = (timezone_id or "").strip() or None
        self.script = script or ""
        self.worker_script = worker_script or ""
        self.locale = (locale or "").strip() or None
        self.geolocation = geolocation or None
        self.screen_metrics = screen_metrics or None
        self.proxy_auth = proxy_auth
        self.cookies = cookies or []
        self.ua_override = ua_override or None
        self.profile_id = profile_id
        # Derived from the override rather than passed in, so the headers a
        # paused request is given and the identity the page reports come from
        # one source and cannot drift apart.
        self.hint_headers = low_entropy_client_hints(self.ua_override)
        self.status = InjectionStatus()

        self._ws: Any = None
        self._id = 0
        self._pending: dict[int, str] = {}
        self._attached: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._cookies_done = False
        self._locale_done = False

    # -- wire ------------------------------------------------------------

    def _send(self, method: str, params: dict[str, Any] | None = None,
              session_id: str | None = None, label: str | None = None) -> int:
        """Fire a command. Responses are handled by the receive loop.

        ``label`` is what the error branch reports and matches on. It exists
        so the same method sent to two kinds of target can fail
        independently: a UA override rejected on a shared-worker target must
        not be read as the page's override having died.
        """
        with self._lock:
            self._id += 1
            msg_id = self._id
            msg: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            self._pending[msg_id] = label or method
            self._ws.send(json.dumps(msg))
        return msg_id

    def _existing_page_targets(self) -> list[str]:
        """Page targets already open, read over the HTTP endpoint."""
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json/list", timeout=2.0
            ) as resp:
                items = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - auto-attach still covers new tabs
            log.debug("could not enumerate existing targets for %s: %s", self.profile_id, e)
            # Auto-attach covers targets opened from here on, so this is not
            # fatal -- but any page ALREADY open goes uninjected, and an
            # uninjected page is one browsing as the host (F11).
            identity_faults.record(
                "targets_unenumerable",
                f"already-open page targets could not be listed ({e}); the hooks were "
                "installed on new targets only, so any page already open is unmasked",
                where="browser.cdp_inject._existing_page_targets",
                profile_id=self.profile_id,
            )
            return []
        return [
            t["id"]
            for t in items
            if t.get("type") == "page" and t.get("id")
            and not str(t.get("url", "")).startswith("devtools://")
        ]

    # -- lifecycle -------------------------------------------------------

    def start(self, timeout_s: float = _CONNECT_TIMEOUT) -> InjectionStatus:
        """Connect, arm auto-attach, and run the event loop on a daemon thread."""
        if websocket is None:
            self.status.error = "websocket-client is not installed"
            return self.status
        try:
            url = _browser_ws_url(self.port, timeout_s=timeout_s)
            # suppress_origin: without an Origin header Chrome accepts us
            # under its DEFAULT policy, so the browser no longer needs
            # --remote-allow-origins=* and a web page stays locked out.
            self._ws = websocket.create_connection(url, timeout=timeout_s, suppress_origin=True)
            self._ws.settimeout(_RECV_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - a failed attach is reported, not raised
            self.status.error = f"cdp_connect_failed: {e}"
            log.warning("identity injection could not connect for %s: %s", self.profile_id, e)
            identity_faults.record(
                "inject_connect_failed",
                f"the identity injector could not attach over CDP ({e}); no hooks, no "
                "proxy auth and no cookies reached this browser",
                where="browser.cdp_inject.CdpInjector.start",
                profile_id=self.profile_id,
            )
            return self.status

        # waitForDebuggerOnStart is what makes this airtight: a new page is
        # frozen until we have installed the script on it.
        self._send("Target.setAutoAttach", {
            "autoAttach": True,
            "waitForDebuggerOnStart": True,
            "flatten": True,
        })

        if self.hint_headers and _first_request_hints_enabled():
            # Fetch on the BROWSER session, not per target, and this is the
            # whole point of it. Network.setUserAgentOverride is scoped to a
            # renderer, but a new tab's first document request is formed by
            # the browser process before that renderer exists -- so no
            # per-target interception can ever see it, however early it is
            # armed. Measured on Chromium 151, a tab opened by the operator:
            #
            #   intended : sec-ch-ua "Google Chrome";v="151", "Chromium";v="151", …
            #              sec-ch-ua-platform "Windows"
            #   actual   : sec-ch-ua "Chromium";v="151", "Not=A?Brand";v="99"
            #              sec-ch-ua-platform "Linux"
            #
            # The User-Agent was already right (it comes from the process
            # flag); the brand list was the host's own, GREASE spelling and
            # all, beside a Windows UA. That is the F1 contradiction surviving
            # on one request per tab, and it is the residual F1 left open.
            #
            # Document requests only: one pause per navigation, not one per
            # subresource, so the cost stays off the page's critical path.
            # Auth keeps its own unrestricted per-target Fetch in
            # _on_attached -- a 407 can arrive on any request, and that path
            # is proven, so it is left exactly as it was.
            self._send("Fetch.enable", {
                "patterns": [{
                    "urlPattern": "*",
                    "requestStage": "Request",
                    "resourceType": "Document",
                }],
            })
        self._thread = threading.Thread(
            target=self._loop, name=f"mp-inject-{self.profile_id or self.port}", daemon=True
        )
        self._thread.start()

        # setAutoAttach only catches targets created after it. The start-URL
        # page already exists by the time CDP is ready, so attach to what is
        # open now as well -- otherwise the very first tab, the one the
        # operator is looking at, is the only one left unmasked.
        for target_id in self._existing_page_targets():
            self._send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        # Give the first target a moment so callers get a truthful status.
        self._ready.wait(timeout=5.0)
        self.status.ok = self.status.script_installed or not self.script
        return self.status

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001,S110 - best effort
            pass
        self._ws = None

    def __enter__(self) -> "IdentityInjector":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- event loop ------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._ws.recv()
            except Exception as e:  # noqa: BLE001
                if self._stop.is_set():
                    return
                text = str(e)
                if "timed out" in text.lower() or isinstance(e, TimeoutError):
                    continue  # idle tick, not a failure
                log.debug("injection socket closed for %s: %s", self.profile_id, e)
                return
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            try:
                self._handle(msg)
            except Exception as e:  # noqa: BLE001 - one bad event must not kill the loop
                log.debug("injection event handling failed for %s: %s", self.profile_id, e)

    def _handle(self, msg: dict[str, Any]) -> None:
        # A command that failed is the whole story when the page turns out
        # unmasked. Record it instead of dropping it on the floor.
        if "id" in msg and "error" in msg:
            err = msg.get("error") or {}
            label = self._pending.pop(msg["id"], f"id={msg['id']}")
            detail = f"{label}: {err.get('message')}"
            if detail not in self.status.detail:
                self.status.detail.append(detail)
            if label.startswith("Page.addScriptToEvaluateOnNewDocument"):
                self.status.script_installed = False
                self.status.ok = False
            if label == UA_OVERRIDE_METHOD:
                # An unmasked wire identity is not a detail to swallow.
                self.status.ua_override_applied = False
                self.status.ok = False
            if label == _WORKER_UA_LABEL:
                # The page's override is unaffected, so ua_override_applied
                # stays as it was -- but a worker whose userAgentData reports
                # the host beside a page claiming another OS is an identity
                # contradiction, not a cosmetic gap, so `ok` does drop.
                self.status.workers_ua_failed += 1
                self.status.ok = False
            log.warning("CDP injection command failed for %s — %s", self.profile_id, detail)
            return
        if "id" in msg:
            self._pending.pop(msg["id"], None)
            return

        method = msg.get("method")
        if method == "Target.attachedToTarget":
            self._on_attached(msg.get("params") or {})
        elif method == "Fetch.authRequired":
            self._on_auth_required(msg.get("params") or {}, msg.get("sessionId"))
        elif method == "Fetch.requestPaused":
            self._on_request_paused(msg.get("params") or {}, msg.get("sessionId"))

    def _on_request_paused(self, params: dict[str, Any], session_id: str | None) -> None:
        """Release a paused request, correcting its client hints on the way.

        Every paused request MUST be released or the page hangs forever, so
        this is written so that the release happens whatever else goes wrong:
        the rewrite is computed first, inside a guard, and a failure there
        falls back to continuing the request untouched. A hung tab would be a
        far worse bug than the leak this fixes.

        Only the headers the browser process got wrong are replaced. Chromium
        applies ``Network.setUserAgentOverride`` per renderer target, but a new
        tab's first document request is formed by the browser process before
        that lands, so it carries the host's own ``sec-ch-ua`` and
        ``sec-ch-ua-platform`` next to the spoofed User-Agent from the process
        flag -- the contradiction F1 was about, surviving on one request.
        """
        request_id = params.get("requestId")
        if not request_id:
            return

        override: dict[str, Any] = {"requestId": request_id}
        try:
            request = params.get("request") or {}
            # Never touch the launch-time identity probe. It measures what the
            # browser would really send, so correcting its headers here would
            # make the assertion tautological -- "verified" from headers this
            # handler had just written, even with the UA override dead. It is
            # also, during a launch, the ONLY document request there is, so
            # skipping it takes ~0.5s back off every launch.
            if IDENTITY_PROBE_PATH in str(request.get("url") or ""):
                self._send("Fetch.continueRequest", override, session_id=session_id)
                return

            if self.hint_headers:
                headers = dict(request.get("headers") or {})
                # Case-insensitively drop what we are about to set, so a
                # header the browser spelled differently cannot survive
                # alongside ours and be sent twice.
                lowered = {k.lower() for k in self.hint_headers}
                merged = {k: v for k, v in headers.items() if k.lower() not in lowered}
                merged.update(self.hint_headers)
                override["headers"] = [
                    {"name": k, "value": str(v)} for k, v in merged.items()
                ]
        except Exception as e:  # noqa: BLE001 - never hang a request over this
            identity_faults.record(
                "hint_rewrite_failed",
                f"could not correct client hints on a paused request ({e}); it was "
                "released unmodified and may carry the host's own hints",
                where="browser.cdp_inject.CdpInjector._on_request_paused",
                profile_id=self.profile_id,
            )
            override = {"requestId": request_id}

        self._send("Fetch.continueRequest", override, session_id=session_id)

    def _identify_worker(self, session_id: str, target: dict[str, Any],
                         params: dict[str, Any]) -> None:
        """Give a worker realm the same identity as the page that owns it.

        A service worker is registered by URL and fetched by the browser, so
        the ``new Worker()`` wrapper in ``evasion.js`` cannot reach it. It was
        therefore the one realm still reporting the host: measured here,
        ``platform: Linux x86_64`` and ``hardwareConcurrency: 16`` inside a
        service worker whose page said ``Win32`` and ``8``.

        Because ``waitForDebuggerOnStart`` freezes the worker before its first
        line, evaluating here lands ahead of the worker's own code.
        """
        # The same dedupe the page path needs, and for a sharper reason: the
        # canvas noise is applied by this script, so evaluating it twice
        # noises an already-noised buffer. Measured while building this, a
        # dedicated worker attached twice and hashed 2367204759 where the page
        # and the shared and service workers all hashed 414750390 -- the
        # realms disagreeing again, in the surface meant to make them agree.
        target_id = target.get("targetId") or ""
        target_type = target.get("type") or ""
        # The decision is made under the lock; the send happens outside it.
        # _send takes the same lock, and threading.Lock is not re-entrant, so
        # releasing a duplicate from inside this block deadlocked the receive
        # loop -- every launch after the first one hung.
        with self._lock:
            duplicate = bool(target_id) and target_id in self._attached
            if target_id and not duplicate:
                self._attached.add(target_id)
        if duplicate:
            if params.get("waitingForDebugger"):
                self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)
            return

        try:
            if self.worker_script:
                self._send("Runtime.enable", session_id=session_id)
                self._send("Runtime.evaluate",
                           {"expression": self.worker_script,
                            "awaitPromise": False, "returnByValue": True},
                           session_id=session_id)
                self.status.workers_identified += 1
            if self.ua_override is not None and target_type in _OWN_PROCESS_WORKER_TYPES:
                # `navigator.userAgentData`, natively, in the two realms that
                # do not inherit it. Measured on this host against CreepJS,
                # product profile, no device preset:
                #
                #   page           userAgentData: Google Chrome 151, Windows 11
                #   service worker userAgentData: Chromium 151, Linux
                #
                # and then narrowed with a probe that asks each realm
                # separately (tests/test_worker_realms_live.py):
                #
                #   worker_blob / worker_url / worker_module  Windows   ok
                #   shared / service                          Linux     leak
                #
                # A dedicated worker runs in its owner document's renderer,
                # so the page's override already covers it. A shared or
                # service worker is its own target in its own process and
                # inherits nothing, so the override has to be sent here.
                #
                # Sent rather than shimmed on purpose. The worker script
                # could have defined a fake NavigatorUAData, and that is the
                # F3 trade the rest of this file keeps losing: a shim has a
                # shape. `Network.setUserAgentOverride` makes the engine
                # answer, so there is nothing to find. It also fixes
                # `getHighEntropyValues()`, which a shim would have had to
                # reimplement -- including the platformVersion that tells
                # Windows 10 from Windows 11.
                self._send(UA_OVERRIDE_METHOD, self.ua_override,
                           session_id=session_id, label=_WORKER_UA_LABEL)
                self.status.workers_ua_applied += 1
            if self.timezone_id:
                # Cheap and harmless: the worker inherits TZ from the process,
                # but a worker started after a timezone change would not.
                self._send("Emulation.setTimezoneOverride",
                           {"timezoneId": self.timezone_id}, session_id=session_id)
        except Exception as e:  # never leave a worker frozen  # noqa: BLE001 - broad by design; the failure is logged, not hidden
            log.warning("worker identity failed for %s: %s", self.profile_id, e)
            self.status.detail.append(f"worker identity failed: {e}")
        finally:
            if params.get("waitingForDebugger"):
                self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)

    def _on_attached(self, params: dict[str, Any]) -> None:
        session_id = params.get("sessionId")
        target = params.get("targetInfo") or {}
        target_type = target.get("type")
        if session_id and target_type in _WORKER_TARGET_TYPES:
            self._identify_worker(session_id, target, params)
            return
        if not session_id or target_type not in ("page", "iframe"):
            # Still release it, or a frozen target blocks the browser.
            if session_id and params.get("waitingForDebugger"):
                self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)
            return

        # setAutoAttach and our explicit attachToTarget can both fire for the
        # same page. Installing twice would hook canvas and friends twice --
        # the exact bug loader.js used to cause.
        target_id = target.get("targetId") or ""
        with self._lock:
            duplicate = bool(target_id) and target_id in self._attached
            if target_id and not duplicate:
                self._attached.add(target_id)
        if duplicate:
            # Skipping the install is right; skipping the release is not. A
            # target auto-attached from two sessions arrives twice, and
            # returning without unfreezing it leaves the second one paused
            # forever -- measured as a page that never finished loading once
            # auto-attach was armed on page sessions too.
            #
            # The send is outside the lock on purpose: _send takes it too and
            # threading.Lock is not re-entrant.
            if params.get("waitingForDebugger"):
                self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)
            return

        self.status.targets_seen += 1

        log.info("injection attached to %s target url=%s waiting=%s",
                 self.profile_id, str(target.get("url"))[:80], params.get("waitingForDebugger"))

        if self.ua_override is not None:
            # UA and Client Hints belong here, on the session that outlives
            # the launch, for the same reason the script does. Chromium drops
            # Network.setUserAgentOverride when the session that set it
            # disconnects, so a launch-time one-shot left the wire carrying
            # the host identity (F1). Setting it per attached target also
            # covers tabs the operator opens later, which a single pinned
            # page session cannot.
            self._send(UA_OVERRIDE_METHOD, self.ua_override, session_id=session_id)
            self.status.ua_override_applied = True

        if self.screen_metrics:
            # CSS `device-width` and `device-height` are resolved by the
            # compositor from the real display. No JavaScript can reach them,
            # so `screen.width` defined as a getter in evasion.js left a
            # contradiction any page can read in one line. Measured on this
            # host, profile with no device preset:
            #
            #   screen.width/height        1920 x 1080   (the claim, JS)
            #   CSS device-width/height    2560 x 1600   (the display)
            #
            # and stock Chromium reported 2560 x 1600 on both.
            #
            # `width`/`height`/`deviceScaleFactor` are sent as 0, which CDP
            # reads as "do not override the viewport". Verified on Chromium
            # 151: `screenHeight` moved CSS device-height 1600 -> 1440 while
            # innerWidth/innerHeight/outerWidth/outerHeight stayed
            # byte-identical. That distinction is the whole reason this can be
            # applied to every profile and not just the preset ones -- a
            # viewport override would letterbox and scale the operator's
            # window, which is why the no-preset path originally skipped it.
            self._send("Emulation.setDeviceMetricsOverride",
                       self.screen_metrics, session_id=session_id)
            self.status.screen_metrics_applied = True

        if self.timezone_id:
            # The engine's own clock, not a patched one. evasion.js hooks
            # Intl.DateTimeFormat, resolvedOptions, getTimezoneOffset,
            # toString and toTimeString -- but not toLocaleString, and that
            # gap was measurable: a profile set to America/New_York reported
            # Eastern time from Date.toString and Asia/Kolkata time from
            # Date.toLocaleString, in the same document. Patching one method
            # at a time cannot win that game; setting the renderer's timezone
            # makes every surface agree, including the ones nobody has
            # thought of yet.
            self._send("Emulation.setTimezoneOverride",
                       {"timezoneId": self.timezone_id}, session_id=session_id)
            self.status.timezone_applied = True

        if self.locale and not self._locale_done:
            # The same argument as the timezone, for the same reason. The
            # profile's language reached navigator.language through the
            # injected script and Accept-Language through the UA override, but
            # nothing told the engine, so Intl collation, date and number
            # formatting still followed the host: a profile claiming de-DE
            # formatted 1.234,56 as 1,234.56. Setting the locale makes every
            # Intl surface agree, including the ones no script patches.
            #
            # Once per browser, not once per target. Unlike the timezone
            # override this one is per browser context and refuses to be set
            # twice -- "Another locale override is already in effect" -- so
            # sending it for every attached target meant every target after
            # the first got an error back and, on a failed re-apply, the
            # profile's Intl would have fallen back to the host. The error was
            # invisible until bulk launch started attaching several targets at
            # once and it appeared in the log.
            self._send("Emulation.setLocaleOverride",
                       {"locale": self.locale}, session_id=session_id)
            self._locale_done = True
            self.status.locale_applied = True

        if self.geolocation:
            # Unset, getCurrentPosition resolved against the host -- the
            # operator's real surroundings, identical for every profile on the
            # machine. This is the only surface in the identity that leaks a
            # physical fact about the person rather than the computer, so it
            # is set even when the site will probably never ask.
            self._send("Emulation.setGeolocationOverride", {
                "latitude": self.geolocation["latitude"],
                "longitude": self.geolocation["longitude"],
                "accuracy": self.geolocation.get("accuracy", 1200.0),
            }, session_id=session_id)
            self.status.geolocation_applied = True

        # Dedicated workers are children of a page target, so the browser
        # session's auto-attach never sees them -- measured here, only
        # shared_worker and service_worker ever arrived. Arming auto-attach on
        # the page's own session is what brings `new Worker(...)` into the
        # same path as every other realm, including workers loaded from a URL
        # rather than a blob.
        if self.worker_script:
            # Filtered to dedicated workers only. Shared and service workers
            # already arrive on the browser session, and attaching them from
            # here as well paused them twice: a target held by two sessions
            # has to be released on both, and the service worker that was
            # working a moment earlier stopped activating at all. Dedicated
            # workers are the only type the browser session cannot see, so
            # they are the only type this asks for.
            self._send("Target.setAutoAttach", {
                "autoAttach": True,
                "waitForDebuggerOnStart": True,
                "flatten": True,
                "filter": [{"type": "worker", "exclude": False}, {"exclude": True}],
            }, session_id=session_id)

        if self.script:
            # Page must be enabled on this session before the page machinery
            # will honour a new-document script.
            self._send("Page.enable", session_id=session_id)
            self._send("Runtime.enable", session_id=session_id)
            self._send("Page.addScriptToEvaluateOnNewDocument",
                       {"source": self.script, "runImmediately": True},
                       session_id=session_id)
            self.status.script_installed = True

        if self.proxy_auth is not None:
            # Unrestricted: a 407 can arrive on any request, not just the
            # document, so auth cannot be narrowed to a resource type.
            self._send("Fetch.enable", {"handleAuthRequests": True}, session_id=session_id)
            self.status.auth_armed = True

        if self.cookies and not self._cookies_done:
            self._send("Network.enable", session_id=session_id)
            self._send("Network.setCookies", {"cookies": self.cookies}, session_id=session_id)
            self.status.cookies_applied = len(self.cookies)
            self._cookies_done = True

        if params.get("waitingForDebugger"):
            # Auto-attached before its first script ran: unfreeze it and the
            # very first document already carries the identity.
            self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)
        else:
            # A page that was already open when we attached (the start URL).
            # addScriptToEvaluateOnNewDocument only affects the NEXT document,
            # so reload once to put this one under the same identity rather
            # than leave the operator's first tab unmasked.
            if self.script:
                self._send("Page.reload", {}, session_id=session_id)
                self.status.detail.append("reloaded a pre-existing page to apply identity")
        self._ready.set()

    def _on_auth_required(self, params: dict[str, Any], session_id: str | None) -> None:
        challenge = params.get("authChallenge") or {}
        request_id = params.get("requestId")
        if challenge.get("source") != "Proxy" or self.proxy_auth is None:
            # Not ours: decline rather than offer proxy credentials to a site.
            self._send("Fetch.continueWithAuth",
                       {"requestId": request_id, "authChallengeResponse": {"response": "Default"}},
                       session_id=session_id)
            return
        self._send("Fetch.continueWithAuth", {
            "requestId": request_id,
            "authChallengeResponse": {
                "response": "ProvideCredentials",
                "username": self.proxy_auth.username,
                "password": self.proxy_auth.password,
            },
        }, session_id=session_id)
        log.info("proxy auth challenge answered for %s", self.profile_id)


_active: dict[str, IdentityInjector] = {}
_active_lock = threading.Lock()


def start_injection(
    profile_id: str,
    port: int,
    *,
    script: str | None = None,
    proxy_auth: ProxyAuth | None = None,
    cookies: list[dict[str, Any]] | None = None,
    ua_override: dict[str, Any] | None = None,
    timezone_id: str | None = None,
    worker_script: str | None = None,
    locale: str | None = None,
    geolocation: dict[str, Any] | None = None,
    screen_metrics: dict[str, Any] | None = None,
) -> InjectionStatus:
    """Start (and register) injection for a profile, replacing any previous one."""
    stop_injection(profile_id)
    injector = IdentityInjector(
        port,
        script=script,
        proxy_auth=proxy_auth,
        cookies=cookies,
        ua_override=ua_override,
        timezone_id=timezone_id,
        worker_script=worker_script,
        locale=locale,
        geolocation=geolocation,
        screen_metrics=screen_metrics,
        profile_id=profile_id,
    )
    status = injector.start()
    with _active_lock:
        _active[profile_id] = injector
    return status


def stop_injection(profile_id: str) -> bool:
    """Tear down a profile's injection session. Safe to call twice."""
    with _active_lock:
        injector = _active.pop(profile_id, None)
    if injector is None:
        return False
    injector.close()
    return True


def stop_all() -> None:
    with _active_lock:
        ids = list(_active)
    for pid in ids:
        stop_injection(pid)


def active_profiles() -> list[str]:
    with _active_lock:
        return list(_active)
