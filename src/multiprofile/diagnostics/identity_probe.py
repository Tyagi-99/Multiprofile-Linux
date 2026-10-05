"""One redacted identity manifest, collected the same way from any browser.

The point of this module is that the *collector* is shared. A comparison is
only evidence if both sides were asked the same question in the same session
on the same host, and the two contradictions a public checker found on
5 October 2026 were both in surfaces the internal probes happened not to
cover. A manifest makes "which surfaces were measured" a reviewable list
instead of an assumption.

**Redaction is in the collector, not in a later pass.** Nothing here ever
returns an IP address, a cookie, a credential or a raw fingerprint hash. The
WebRTC section records candidate *types* and whether a public candidate
appeared; it never records the candidate. That is deliberate: a redaction
step that runs afterwards is one someone forgets to run before pasting a
report into an issue.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

#: What a redacted value looks like in a manifest. Distinguishable from a
#: measurement that failed, which is ``None``.
REDACTED = "[redacted]"

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")
#: Loopback and link-local are not identifying and are worth keeping: a
#: WebRTC candidate for 127.0.0.1 means something different from one for a
#: routable address, and losing the distinction loses the finding.
_KEEP = ("127.0.0.1", "0.0.0.0", "::1", "::")


def redact(value: Any) -> Any:
    """Strip anything that identifies the operator or the host's network.

    Applied to every string that comes back from a browser, recursively
    through dicts and lists, before it reaches a manifest.
    """
    if isinstance(value, str):
        if value in _KEEP:
            return value
        out = _IPV4.sub(REDACTED, value)
        out = _IPV6.sub(REDACTED, out)
        return out
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# The fields every realm is asked for. One string, shared by the page, the
# worker sources and the served scripts, so a realm cannot be asked an easier
# question than its page.
REALM_FIELDS = (
    "platform:navigator.platform,"
    "userAgent:navigator.userAgent,"
    "uaPlatform:(navigator.userAgentData?String(navigator.userAgentData.platform):null),"
    "uaMobile:(navigator.userAgentData?String(navigator.userAgentData.mobile):null),"
    "uaBrands:(navigator.userAgentData?navigator.userAgentData.brands"
    ".map(b=>b.brand+'/'+b.version).sort().join('|'):null),"
    "cores:navigator.hardwareConcurrency,"
    "memory:(navigator.deviceMemory!==undefined?navigator.deviceMemory:null),"
    "language:navigator.language,"
    "languages:(navigator.languages||[]).join(','),"
    "timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,"
    "tzOffset:new Date().getTimezoneOffset(),"
    "locale:Intl.DateTimeFormat().resolvedOptions().locale,"
    "glRenderer:(()=>{try{const c=new OffscreenCanvas(8,8).getContext('webgl');"
    "const d=c.getExtension('WEBGL_debug_renderer_info');"
    "return String(c.getParameter(d.UNMASKED_RENDERER_WEBGL));}catch(e){return null}})(),"
    "glVendor:(()=>{try{const c=new OffscreenCanvas(8,8).getContext('webgl');"
    "const d=c.getExtension('WEBGL_debug_renderer_info');"
    "return String(c.getParameter(d.UNMASKED_VENDOR_WEBGL));}catch(e){return null}})(),"
    "canvasHash:(()=>{try{const c=new OffscreenCanvas(20,20);const x=c.getContext('2d');"
    "x.fillStyle='#f30';x.fillRect(0,0,20,20);x.fillStyle='#08f';"
    "x.font='12px serif';x.fillText('Ab',1,14);"
    "const d=x.getImageData(0,0,20,20).data;let h=0;"
    "for(let i=0;i<d.length;i++)h=(h*31+d[i])>>>0;return h;}catch(e){return null}})()"
)

#: Font faces worth asking about: the Windows set a preset claims, the
#: metric-compatible Linux files that back some of them, and two controls.
#: Measured by width comparison against a fallback, which is what a checker
#: does -- an alias that resolves to Liberation Sans answers "present" here
#: and that is the point, because it also answers "present" to a site.
PROBE_FONTS = (
    "Arial", "Calibri", "Cambria", "Consolas", "Georgia", "Impact",
    "Microsoft Sans Serif", "Segoe UI", "Tahoma", "Times New Roman",
    "Trebuchet MS", "Verdana",
    "Liberation Sans", "Liberation Serif", "Liberation Mono",
    "DejaVu Sans", "Noto Color Emoji",
    "Helvetica", "Courier New",
    "__ThisFontDoesNotExist__",
)

_PAGE_PROBE = r"""
(async () => {
  const F = "__FIELDS__";
  const FONTS = __FONTS__;
  const out = {};
  const guard = (label, fn) => { try { return fn(); } catch (e) { return {error: String(e)}; } };
  const D = (pr, ms, label) => Promise.race([pr,
    new Promise(r => setTimeout(() => r({timeout: label}), ms))]);

  out.page = guard('page', () => eval("({" + F + "})"));

  // ---- geometry -------------------------------------------------------
  // CSS device-width is resolved by the compositor. No script can reach it,
  // which is exactly why it caught a contradiction the JS getters could not.
  out.geometry = guard('geometry', () => {
    const mm = q => matchMedia(q).matches;
    const find = axis => {
      let lo = 1, hi = 20000, best = 0;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (mm(`(min-device-${axis}: ${mid}px)`)) { best = mid; lo = mid + 1; }
        else hi = mid - 1;
      }
      return best;
    };
    return {
      screenW: screen.width, screenH: screen.height,
      availW: screen.availWidth, availH: screen.availHeight,
      colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth,
      dpr: window.devicePixelRatio,
      cssDeviceW: find('width'), cssDeviceH: find('height'),
      cssExactW: mm(`(device-width: ${screen.width}px)`),
      cssExactH: mm(`(device-height: ${screen.height}px)`),
      cssDpr: mm(`(resolution: ${window.devicePixelRatio}dppx)`),
      innerW: window.innerWidth, innerH: window.innerHeight,
      outerW: window.outerWidth, outerH: window.outerHeight,
      orientation: (screen.orientation || {}).type || null,
      hover: mm('(hover: hover)'), pointer: mm('(pointer: fine)'),
      prefersDark: mm('(prefers-color-scheme: dark)'),
      maxTouchPoints: navigator.maxTouchPoints,
    };
  });

  // ---- fonts ----------------------------------------------------------
  // Width against a fallback, the way a checker measures it. `document.fonts
  // .check` answers from the CSS font source list and says yes to anything
  // with an alias, so it is reported separately rather than instead.
  out.fonts = guard('fonts', () => {
    const span = document.createElement('span');
    span.style.cssText = 'position:absolute;left:-9999px;font-size:72px;white-space:nowrap';
    span.textContent = 'mmmmmmmmmmlli WwQq@';
    document.body.appendChild(span);
    const base = {};
    for (const generic of ['monospace', 'sans-serif', 'serif']) {
      span.style.fontFamily = generic;
      base[generic] = [span.offsetWidth, span.offsetHeight];
    }
    const present = [], absent = [], checkApi = {};
    for (const font of FONTS) {
      let hit = false;
      for (const generic of ['monospace', 'sans-serif', 'serif']) {
        span.style.fontFamily = `'${font}',${generic}`;
        if (span.offsetWidth !== base[generic][0]
            || span.offsetHeight !== base[generic][1]) { hit = true; break; }
      }
      (hit ? present : absent).push(font);
      try { checkApi[font] = document.fonts.check(`72px '${font}'`); }
      catch (e) { checkApi[font] = null; }
    }
    // What the generics actually resolve to, as widths. Without this a
    // "font absent" result is unreadable: a face whose metrics equal the
    // generic it was tested against measures identical and reads as absent,
    // which is exactly what metric-compatible substitution is for. If the
    // product changes what `serif` resolves to, the same face can read
    // present in one browser and absent in another with nothing installed or
    // removed -- so the baseline is reported next to the verdict.
    const genericWidths = base;
    span.remove();
    return {present, absent, measured: FONTS.length, checkApi, genericWidths,
            method: "offsetWidth/offsetHeight against three generics; cannot "
                    + "distinguish a metric-compatible face from the generic "
                    + "it matches"};
  });

  // ---- iframe ---------------------------------------------------------
  out.iframe = await D(new Promise(res => {
    const f = document.createElement('iframe');
    f.style.display = 'none';
    f.srcdoc = '<!doctype html><title>i</title>';
    f.onload = () => {
      try {
        const w = f.contentWindow;
        res(w.eval("({" + F + "})"));
      } catch (e) { res({error: String(e)}); }
      finally { f.remove(); }
    };
    document.body.appendChild(f);
  }), 8000, 'iframe');

  // ---- workers --------------------------------------------------------
  const mkWorker = (opts, url) => new Promise(res => {
    const src = `self.onmessage=()=>postMessage(JSON.stringify({${F}}))`;
    const u = url || URL.createObjectURL(new Blob([src], {type: 'text/javascript'}));
    const w = new Worker(u, opts);
    w.onmessage = e => { res(JSON.parse(e.data)); w.terminate(); };
    w.onerror = e => res({error: e.message || 'worker error'});
    w.postMessage(1);
  });
  out.worker_blob = await D(mkWorker(undefined, null), 8000, 'worker_blob');
  out.worker_url = await D(mkWorker(undefined, '/probe-worker.js'), 8000, 'worker_url');
  out.worker_module = await D(mkWorker({type: 'module'}, null), 8000, 'worker_module');
  out.shared = await D(new Promise(res => {
    const src = `self.onconnect=e=>{const p=e.ports[0];`
      + `p.onmessage=()=>p.postMessage(JSON.stringify({${F}}));p.start();}`;
    const w = new SharedWorker(URL.createObjectURL(
      new Blob([src], {type: 'text/javascript'})));
    w.port.onmessage = e => res(JSON.parse(e.data));
    w.port.start(); w.port.postMessage(1);
  }), 8000, 'shared');
  out.service = await D((async () => {
    const reg = await navigator.serviceWorker.register('/probe-sw.js', {scope: '/'});
    await navigator.serviceWorker.ready;
    return await new Promise(res => {
      navigator.serviceWorker.addEventListener('message', e => res(JSON.parse(e.data)));
      const sw = reg.active || navigator.serviceWorker.controller;
      if (!sw) { res({error: 'no active service worker'}); return; }
      sw.postMessage(1);
    });
  })(), 20000, 'service');

  // ---- WebGPU ---------------------------------------------------------
  out.webgpu = await D((async () => {
    if (!navigator.gpu) return {available: false};
    const a = await navigator.gpu.requestAdapter();
    if (!a) return {available: true, adapter: null};
    const info = a.info || (a.requestAdapterInfo ? await a.requestAdapterInfo() : {});
    return {
      available: true,
      vendor: info.vendor ?? null, architecture: info.architecture ?? null,
      device: info.device ?? null, description: info.description ?? null,
      features: [...a.features].sort().slice(0, 12),
    };
  })(), 10000, 'webgpu');

  // ---- WebRTC ---------------------------------------------------------
  // Candidate TYPES and whether a routable one appeared. Never the address:
  // this manifest is meant to be pasteable into an issue.
  out.webrtc = await D(new Promise(res => {
    let pc;
    try {
      pc = new RTCPeerConnection({iceServers: [{urls: 'stun:stun.l.google.com:19302'}]});
    } catch (e) { res({error: String(e)}); return; }
    const types = new Set(); const protocols = new Set();
    let sawRoutable = false, sawMdns = false, count = 0;
    pc.onicecandidate = e => {
      if (!e.candidate) {
        res({candidates: count, types: [...types].sort(),
             protocols: [...protocols].sort(),
             sawRoutableAddress: sawRoutable, sawMdnsHostname: sawMdns});
        try { pc.close(); } catch (_) {}
        return;
      }
      count++;
      const c = e.candidate;
      if (c.type) types.add(c.type);
      if (c.protocol) protocols.add(c.protocol);
      const addr = c.address || '';
      if (/\.local$/.test(addr)) sawMdns = true;
      else if (addr && !/^(127\.|0\.0\.0\.0|::1?$|169\.254\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(addr)) {
        sawRoutable = true;
      }
    };
    pc.createDataChannel('probe');
    pc.createOffer().then(o => pc.setLocalDescription(o)).catch(e => res({error: String(e)}));
  }), 12000, 'webrtc');

  // ---- engine surface -------------------------------------------------
  out.engine = guard('engine', () => ({
    webdriver: navigator.webdriver,
    pluginCount: (navigator.plugins || []).length,
    mimeTypeCount: (navigator.mimeTypes || []).length,
    vendor: navigator.vendor,
    productSub: navigator.productSub,
    hasChrome: typeof window.chrome !== 'undefined',
    permissionsQuery: typeof (navigator.permissions || {}).query === 'function',
    // The F3 surface, asked the way a page asks it.
    toStringLeaks: (() => {
      const probes = [
        ['canvas.toDataURL', HTMLCanvasElement.prototype.toDataURL],
        ['getImageData', CanvasRenderingContext2D.prototype.getImageData],
        ['getParameter', window.WebGLRenderingContext
          ? WebGLRenderingContext.prototype.getParameter : null],
      ];
      const leaks = [];
      for (const [name, fn] of probes) {
        if (!fn) continue;
        const src = Function.prototype.toString.call(fn);
        const nativeShaped = /\{\s*\[native code\]\s*\}/.test(src);
        const ownToString = Object.prototype.hasOwnProperty.call(fn, 'toString');
        const constructible = 'prototype' in fn;
        if (!nativeShaped || ownToString || constructible) {
          leaks.push({name, nativeShaped, ownToString, constructible});
        }
      }
      return leaks;
    })(),
  }));

  return JSON.stringify(out);
})()
""".replace("__FIELDS__", REALM_FIELDS.replace('"', '\\"')).replace(
    "__FONTS__", json.dumps(list(PROBE_FONTS))
)

PAGE_PROBE = _PAGE_PROBE

#: Served at ``/probe-worker.js`` -- a worker loaded from a URL rather than a
#: blob, because those reached the browser by a different path once and the
#: difference was measurable.
WORKER_SOURCE = (
    f"self.onmessage=()=>postMessage(JSON.stringify({{{REALM_FIELDS}}}))"
).encode()

#: Served at ``/probe-sw.js``. This is the realm the 5 October CreepJS run
#: reported from, and the one that was leaking.
SERVICE_WORKER_SOURCE = f"""self.addEventListener('message', e => {{
  e.source.postMessage(JSON.stringify({{{REALM_FIELDS}}}));
}});
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
""".encode()

#: Realms a manifest compares against its page. ``iframe`` is in the list
#: because ``evasion.js`` reaches iframes by a separate code path.
REALMS = ("iframe", "worker_blob", "worker_url", "worker_module", "shared", "service")

#: Fields that must agree between a page and every realm it owns. Omits the
#: ones a realm legitimately cannot answer.
REALM_AGREEMENT_FIELDS = (
    "platform", "userAgent", "uaPlatform", "uaMobile", "uaBrands",
    "cores", "memory", "language", "timezone", "glRenderer", "glVendor",
    "canvasHash",
)


@dataclass
class ParityFinding:
    """One attributable difference, with both sides recorded."""

    surface: str
    detail: str
    product: Any = None
    control: Any = None
    severity: str = "warn"      # "fail" | "warn" | "info"
    kind: str = "contradiction"  # contradiction | divergence | absence

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface, "detail": self.detail,
            "product": self.product, "control": self.control,
            "severity": self.severity, "kind": self.kind,
        }


@dataclass
class Manifest:
    label: str
    observations: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "environment": self.environment,
            "headers": self.headers,
            "observations": self.observations,
            "errors": list(self.errors),
        }


def _cdp_page_target(port: int, url_fragment: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        try:
            last = json.loads(urllib.request.urlopen(req, timeout=3).read())
        except Exception:  # noqa: BLE001 - retried until the deadline
            time.sleep(0.25)
            continue
        for target in last:
            if target.get("type") == "page" and url_fragment in (target.get("url") or ""):
                return target
        time.sleep(0.25)
    raise RuntimeError(
        f"no page target matching {url_fragment!r} on port {port}; "
        f"saw {[t.get('url') for t in last][:5]}"
    )


def collect_manifest(
    port: int,
    *,
    label: str,
    url_fragment: str = "127.0.0.1",
    environment: dict[str, Any] | None = None,
    timeout: float = 90.0,
) -> Manifest:
    """Run the probe against one browser over CDP and return a redacted manifest.

    ``port`` is a CDP port. The browser must already be on a page served by
    the harness, because the service-worker and URL-worker probes need a real
    origin -- ``about:blank`` cannot register a service worker.
    """
    import websocket  # imported here so the module imports without it

    manifest = Manifest(label=label, environment=environment or {})
    target = _cdp_page_target(port, url_fragment)
    ws = websocket.create_connection(
        target["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True
    )
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": PAGE_PROBE, "awaitPromise": True,
                       "returnByValue": True},
        }))
        deadline = time.monotonic() + timeout
        raw: str | None = None
        while time.monotonic() < deadline:
            message = json.loads(ws.recv())
            if message.get("id") != 1:
                continue
            result = message.get("result") or {}
            details = result.get("exceptionDetails")
            if details:
                exception = (details.get("exception") or {}).get("description")
                manifest.errors.append(
                    f"probe raised: {exception or details.get('text')}"
                )
                return manifest
            raw = (result.get("result") or {}).get("value")
            break
        if raw is None:
            manifest.errors.append("probe did not answer before the deadline")
            return manifest
        manifest.observations = redact(json.loads(raw))
        return manifest
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001,S110 - closing a dead socket is not a failure
            pass


def _realm_findings(observations: dict[str, Any]) -> list[ParityFinding]:
    """Realms that disagree with their own page. Self-contained: no control."""
    page = observations.get("page") or {}
    findings: list[ParityFinding] = []
    if not isinstance(page, dict) or page.get("error"):
        return [ParityFinding("page", "the page probe did not answer",
                              product=page, severity="fail", kind="absence")]
    for realm in REALMS:
        got = observations.get(realm)
        if not isinstance(got, dict):
            findings.append(ParityFinding(
                realm, "realm did not answer", product=got,
                severity="fail", kind="absence"))
            continue
        if got.get("timeout"):
            findings.append(ParityFinding(
                realm, f"realm timed out: {got['timeout']}",
                severity="fail", kind="absence"))
            continue
        if got.get("error"):
            findings.append(ParityFinding(
                realm, f"realm errored: {got['error']}",
                severity="fail", kind="absence"))
            continue
        for key in REALM_AGREEMENT_FIELDS:
            if key not in page or key not in got:
                continue
            if page[key] != got[key]:
                findings.append(ParityFinding(
                    f"{realm}.{key}",
                    f"{realm} disagrees with its page on {key}",
                    product=got[key], control=page[key],
                    severity="fail", kind="contradiction"))
    return findings


def _geometry_findings(observations: dict[str, Any]) -> list[ParityFinding]:
    geometry = observations.get("geometry") or {}
    if not isinstance(geometry, dict) or geometry.get("error"):
        return [ParityFinding("geometry", "geometry probe did not answer",
                              product=geometry, severity="fail", kind="absence")]
    findings: list[ParityFinding] = []
    pairs = (
        ("cssDeviceW", "screenW", "CSS device-width vs screen.width"),
        ("cssDeviceH", "screenH", "CSS device-height vs screen.height"),
    )
    for css_key, js_key, detail in pairs:
        if geometry.get(css_key) != geometry.get(js_key):
            findings.append(ParityFinding(
                f"geometry.{css_key}", detail,
                product=geometry.get(css_key), control=geometry.get(js_key),
                severity="fail", kind="contradiction"))
    for key, detail in (("cssExactW", "(device-width: Npx) for the claimed width"),
                        ("cssExactH", "(device-height: Npx) for the claimed height")):
        if geometry.get(key) is False:
            findings.append(ParityFinding(
                f"geometry.{key}", f"{detail} does not match",
                product=False, control=True,
                severity="fail", kind="contradiction"))
    if geometry.get("innerW") and geometry.get("screenW"):
        if geometry["innerW"] > geometry["screenW"]:
            findings.append(ParityFinding(
                "geometry.innerW", "the viewport is wider than the claimed screen",
                product=geometry["innerW"], control=geometry["screenW"],
                severity="fail", kind="contradiction"))
    return findings


def _ua_findings(observations: dict[str, Any]) -> list[ParityFinding]:
    page = observations.get("page") or {}
    if not isinstance(page, dict):
        return []
    markers = {"Windows": "Windows NT", "macOS": "Mac OS X", "Linux": "Linux",
               "Android": "Android", "iOS": ("iPhone", "iPad")}
    platform = page.get("uaPlatform")
    user_agent = page.get("userAgent") or ""
    findings: list[ParityFinding] = []
    marker = markers.get(platform)
    if marker:
        needles = marker if isinstance(marker, tuple) else (marker,)
        if not any(n in user_agent for n in needles):
            findings.append(ParityFinding(
                "page.userAgent",
                f"userAgentData.platform is {platform!r} but the UA string "
                f"does not name it",
                product=user_agent, control=platform,
                severity="fail", kind="contradiction"))
    return findings


def _font_findings(product: dict[str, Any], control: dict[str, Any] | None
                   ) -> list[ParityFinding]:
    fonts = product.get("fonts") or {}
    if not isinstance(fonts, dict) or fonts.get("error"):
        return []
    findings: list[ParityFinding] = []
    present = set(fonts.get("present") or [])
    if "__ThisFontDoesNotExist__" in present:
        findings.append(ParityFinding(
            "fonts", "a font that cannot exist measured as present, so the "
                     "measurement is unreliable",
            product=sorted(present)[:6], severity="fail", kind="absence"))
    # Sparser than the control is itself a signal, which is how CreepJS
    # flagged this: 1 of 51 for the product against 6 of 51 for stock.
    if control:
        control_fonts = control.get("fonts") or {}
        if isinstance(control_fonts, dict) and control_fonts.get("present") is not None:
            control_present = set(control_fonts.get("present") or [])
            if len(present) < len(control_present):
                findings.append(ParityFinding(
                    "fonts.present",
                    "the profile exposes fewer fonts than stock Chromium on "
                    "the same host, which is a divergence from the ordinary "
                    "browser rather than toward it",
                    product=sorted(present), control=sorted(control_present),
                    severity="warn", kind="divergence"))
    return findings


def compare_manifests(product: Manifest, control: Manifest | None = None
                      ) -> list[ParityFinding]:
    """Findings for one manifest, sharpened by a control when there is one.

    Two kinds, kept apart on purpose:

    **Contradictions** are self-evident -- a realm disagreeing with its own
    page, or CSS disagreeing with ``screen.*``. They need no control and they
    are failures, because no real browser produces them.

    **Divergences** are differences from stock Chromium on the same host.
    They are *not* automatically failures: spoofing a platform is a
    divergence and it is the product working. They are reported so a human
    decides, and the control is what makes them attributable to the product
    rather than to the environment.
    """
    observations = product.observations or {}
    findings = (
        _realm_findings(observations)
        + _geometry_findings(observations)
        + _ua_findings(observations)
        + _font_findings(observations, control.observations if control else None)
    )
    engine = observations.get("engine") or {}
    if isinstance(engine, dict):
        leaks = engine.get("toStringLeaks") or []
        if leaks:
            findings.append(ParityFinding(
                "engine.toStringLeaks",
                "hooked functions do not have the shape of a native one",
                product=leaks, severity="fail", kind="contradiction"))
        if engine.get("webdriver"):
            findings.append(ParityFinding(
                "engine.webdriver", "navigator.webdriver is true",
                product=True, control=False, severity="fail",
                kind="contradiction"))
    webrtc = observations.get("webrtc") or {}
    if isinstance(webrtc, dict) and webrtc.get("sawRoutableAddress"):
        findings.append(ParityFinding(
            "webrtc", "a routable address appeared in an ICE candidate",
            product=True, severity="warn", kind="divergence"))
    order = {"fail": 0, "warn": 1, "info": 2}
    return sorted(findings, key=lambda f: (order.get(f.severity, 9), f.surface))
