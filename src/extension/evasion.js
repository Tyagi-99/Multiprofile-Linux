/**
 * Anti-Detect Fingerprint Defender (Comprehensive Multi-Realm Evasion Script)
 * Neutralizes:
 *  1. GPU & WebGL Information (Spoofed to match platform without leaks)
 *  2. CPU Cores (hardwareConcurrency across Main Window, Iframes & Web Workers)
 *  3. Device Memory (deviceMemory across all realms)
 *  4. Screen Resolution (Standardized & coherent with device)
 *  5. Canvas & AudioContext noise injection
 *  6. Client Hints consistency
 */
(function() {
  'use strict';

  // Helper to make hooked functions appear 100% native
  function makeNative(fn, name) {
    try {
      if (name) {
        Object.defineProperty(fn, 'name', { value: name, configurable: true });
      }
      const nativeStr = `function ${name || fn.name || ''}() { [native code] }`;
      fn.toString = function toString() { return nativeStr; };
      Object.defineProperty(fn.toString, 'name', { value: 'toString', configurable: true });
      fn.toString.toString = function toString() { return 'function toString() { [native code] }'; };
    } catch(e) {}
    return fn;
  }

  // Detect platform profile based on User-Agent
  const ua = navigator.userAgent || "";
  const isAndroid = /Android/i.test(ua);
  const isIPhone = /iPhone|iPad/i.test(ua);
  const isMac = /Macintosh|Mac OS X/i.test(ua) && !isIPhone;

  let TARGET_CORES = 8;
  let TARGET_MEMORY = 8;
  let TARGET_GPU_VENDOR = "Google Inc. (NVIDIA)";
  let TARGET_GPU_RENDERER = "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)";
  // Derived in fingerprint/_core.py from the renderer above, so the two
  // vocabularies for the same card cannot drift apart. Null means "claim
  // nothing" -- inventing an identity that disagreed with the WebGL string
  // would recreate the contradiction this exists to remove.
  let TARGET_WEBGPU = null;
  let SCREEN_W = 1920;
  let SCREEN_H = 1080;
  let SCREEN_AVAIL_H = 1040;

  if (isAndroid) {
    TARGET_CORES = 8;
    TARGET_MEMORY = 8;
    TARGET_GPU_VENDOR = "Google Inc. (Qualcomm)";
    TARGET_GPU_RENDERER = "ANGLE (Qualcomm, Adreno (TM) 740, OpenGL ES 3.2)";
    SCREEN_W = 412;
    SCREEN_H = 915;
    SCREEN_AVAIL_H = 915;
  } else if (isIPhone) {
    TARGET_CORES = 6;
    TARGET_MEMORY = undefined; // iOS Safari does not expose deviceMemory
    TARGET_GPU_VENDOR = "Google Inc. (Apple)";
    TARGET_GPU_RENDERER = "ANGLE (Apple, Apple GPU, OpenGL ES 3.0)";
    SCREEN_W = 393;
    SCREEN_H = 852;
    SCREEN_AVAIL_H = 852;
  } else if (isMac) {
    TARGET_CORES = 8;
    TARGET_MEMORY = 8;
    TARGET_GPU_VENDOR = "Google Inc. (Apple)";
    TARGET_GPU_RENDERER = "ANGLE (Apple, Apple M2 Pro, OpenGL ES 3.0)";
    SCREEN_W = 1728;
    SCREEN_H = 1117;
    SCREEN_AVAIL_H = 1080;
  }

  // Target Timezone (injected per-profile or default America/New_York)
  let rawTz = "__PROFILE_TIMEZONE__";
  let TARGET_TIMEZONE = (rawTz && !rawTz.startsWith("__")) ? rawTz : "America/New_York";
  let rawIp = "__PROFILE_ALLOCATED_IP__";
  const TARGET_IP = (rawIp && !rawIp.startsWith("__")) ? rawIp : "";
  let rawSeed = "__PROFILE_SEED__";
  let PROFILE_SEED = (rawSeed && !rawSeed.startsWith("__")) ? parseInt(rawSeed, 10) : 1;

  let TARGET_PLATFORM = isAndroid ? "Linux armv8l" : (isIPhone ? "iPhone" : (isMac ? "MacIntel" : "Win32"));
  let TARGET_TOUCH = (isAndroid || isIPhone) ? 5 : 0;
  let TARGET_LANGUAGES = ["en-US", "en"];
  let TARGET_LANGUAGE = "en-US";
  let TARGET_DPR = 1;
  let MP_WEBRTC_MODE = "default";
  let MP_CLIENT_HINTS = null;

  // Optional marker replaced by launcher with a JSON object literal; also accept window.__MP_PROFILE__
  // Placeholder token (string form kept for detection); launcher replaces whole null/*__PROFILE_CONFIG_JSON__*/ expr
  let MP_CFG = null; /*__PROFILE_CONFIG_JSON__*/
  try {
    if (typeof window !== "undefined" && window.__MP_PROFILE__ && typeof window.__MP_PROFILE__ === "object") {
      MP_CFG = window.__MP_PROFILE__;
    }
  } catch (e) { /* keep MP_CFG */ }
  // If launcher inlined: var __MP_INLINE_CFG__ = {...}; prefer it
  try {
    if (typeof __MP_INLINE_CFG__ !== "undefined" && __MP_INLINE_CFG__) {
      MP_CFG = __MP_INLINE_CFG__;
    }
  } catch (e) {}

  // Python seed-driven fingerprint wins over UA heuristics
  if (MP_CFG) {
    try {
      if (MP_CFG.cores != null) TARGET_CORES = MP_CFG.cores;
      if (MP_CFG.memory !== undefined) TARGET_MEMORY = MP_CFG.memory;
      if (MP_CFG.gpu) {
        if (MP_CFG.gpu.vendor) TARGET_GPU_VENDOR = MP_CFG.gpu.vendor;
        if (MP_CFG.gpu.renderer) TARGET_GPU_RENDERER = MP_CFG.gpu.renderer;
        if (MP_CFG.gpu.webgpu && MP_CFG.gpu.webgpu.vendor) TARGET_WEBGPU = MP_CFG.gpu.webgpu;
      }
      if (MP_CFG.screen) {
        if (MP_CFG.screen.w) SCREEN_W = MP_CFG.screen.w;
        if (MP_CFG.screen.h) SCREEN_H = MP_CFG.screen.h;
        if (MP_CFG.screen.availH) SCREEN_AVAIL_H = MP_CFG.screen.availH;
        if (MP_CFG.screen.dpr) TARGET_DPR = MP_CFG.screen.dpr;
      }
      if (MP_CFG.platform) TARGET_PLATFORM = MP_CFG.platform;
      if (MP_CFG.touch != null) TARGET_TOUCH = MP_CFG.touch;
      if (MP_CFG.timezone) TARGET_TIMEZONE = MP_CFG.timezone;
      if (MP_CFG.seed != null) PROFILE_SEED = parseInt(MP_CFG.seed, 10) || PROFILE_SEED;
      if (Array.isArray(MP_CFG.languages) && MP_CFG.languages.length) {
        TARGET_LANGUAGES = MP_CFG.languages.slice();
        TARGET_LANGUAGE = MP_CFG.language || MP_CFG.languages[0];
      } else if (MP_CFG.language) {
        TARGET_LANGUAGE = MP_CFG.language;
        TARGET_LANGUAGES = [MP_CFG.language];
      }
      MP_WEBRTC_MODE = MP_CFG.webrtcMode || MP_CFG.webrtcPolicy || "default";
      if (MP_CFG.clientHints) MP_CLIENT_HINTS = MP_CFG.clientHints;
    } catch (e) {}
  }

  // NOTE: no console output unless the launcher explicitly asks for it.
  //
  // This script used to announce itself on EVERY page and in every frame:
  //
  //     console.log("[Evasion] evasion.js active on:", location.href);
  //
  // A page cannot read past console output, so it was not directly
  // exploitable — but it was a plain-text confession visible in any DevTools
  // session, screen share, demo or support screenshot, and harvestable by a
  // page that installs a console proxy before this runs. The line survives
  // behind a flag rather than being deleted, because the number of times it
  // appears for one document is the only honest way to count how many times
  // this script ran, which is how the double-injection bug was measured. It is
  // OFF unless MULTIPROFILE_EVASION_DEBUG put debug:true in the config.
  if (MP_CFG && MP_CFG.debug === true) {
    try { console.log("[Evasion] evasion.js active on:", location.href); } catch (e) {}
  }


  // Deterministic noise generator
  function getNoiseValue(index) {
    const x = Math.sin(index * 9999.1234) * 10000;
    return x - Math.floor(x);
  }

  function pixelHash(x, y) {
    let h = Math.imul(PROFILE_SEED ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(x + 1, 0xc2b2ae35) ^ Math.imul(y + 1, 0x27d4eb2f);
    h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
    h = Math.imul(h ^ (h >>> 12), 0x297a2d39);
    return (h ^ (h >>> 15)) >>> 0;
  }

  // ==========================================
  // 1. HARDWARE & CPU/MEMORY PROTOTYPE SPOOFING
  // ==========================================
  function applyHardwareSpoofs(navObj, NavProto) {
    try {
      // hardwareConcurrency
      const hcGetter = makeNative(function() {
        return TARGET_CORES;
      }, 'get hardwareConcurrency');

      if (NavProto) {
        Object.defineProperty(NavProto, 'hardwareConcurrency', {
          get: hcGetter,
          configurable: true,
          enumerable: true
        });
      }
      if (navObj) {
        Object.defineProperty(navObj, 'hardwareConcurrency', {
          get: hcGetter,
          configurable: true,
          enumerable: true
        });
      }

      // deviceMemory
      if (TARGET_MEMORY !== undefined) {
        const dmGetter = makeNative(function() {
          return TARGET_MEMORY;
        }, 'get deviceMemory');

        if (NavProto) {
          Object.defineProperty(NavProto, 'deviceMemory', {
            get: dmGetter,
            configurable: true,
            enumerable: true
          });
        }
        if (navObj) {
          Object.defineProperty(navObj, 'deviceMemory', {
            get: dmGetter,
            configurable: true,
            enumerable: true
          });
        }
      } else {
        if (NavProto) delete NavProto.deviceMemory;
        if (navObj) delete navObj.deviceMemory;
      }

      // platform spoofing (Android -> Linux armv8l, iPhone -> iPhone, Mac -> MacIntel, Windows -> Win32)
      const platGetter = makeNative(function() { return TARGET_PLATFORM; }, 'get platform');
      if (NavProto) {
        Object.defineProperty(NavProto, 'platform', { get: platGetter, configurable: true, enumerable: true });
      }
      if (navObj) {
        Object.defineProperty(navObj, 'platform', { get: platGetter, configurable: true, enumerable: true });
      }

      // maxTouchPoints for mobile
      const touchGetter = makeNative(function() { return TARGET_TOUCH; }, 'get maxTouchPoints');
      if (NavProto) {
        Object.defineProperty(NavProto, 'maxTouchPoints', { get: touchGetter, configurable: true, enumerable: true });
      }
      if (navObj) {
        Object.defineProperty(navObj, 'maxTouchPoints', { get: touchGetter, configurable: true, enumerable: true });
      }

      // Keep navigator.webdriver exactly as the installed browser exposes it.
      // Replacing its native descriptor is rejected by some identity providers.
    } catch(e) {}
  }

  applyHardwareSpoofs(navigator, window.Navigator ? Navigator.prototype : null);

  // Languages spoof from profile config
  try {
    const langGetter = makeNative(function() { return TARGET_LANGUAGE; }, 'get language');
    const langsGetter = makeNative(function() { return TARGET_LANGUAGES.slice(); }, 'get languages');
    if (window.Navigator && Navigator.prototype) {
      Object.defineProperty(Navigator.prototype, 'language', { get: langGetter, configurable: true, enumerable: true });
      Object.defineProperty(Navigator.prototype, 'languages', { get: langsGetter, configurable: true, enumerable: true });
    }
    Object.defineProperty(navigator, 'language', { get: langGetter, configurable: true, enumerable: true });
    Object.defineProperty(navigator, 'languages', { get: langsGetter, configurable: true, enumerable: true });
  } catch(e) {}

  // devicePixelRatio from fingerprint screen.dpr
  try {
    if (TARGET_DPR) {
      Object.defineProperty(window, 'devicePixelRatio', {
        get: makeNative(function() { return TARGET_DPR; }, 'get devicePixelRatio'),
        configurable: true
      });
    }
  } catch(e) {}

  // ==========================================
  // 2. SCREEN RESOLUTION SPOOFING
  // ==========================================
  function applyScreenSpoofs(win) {
    try {
      const scr = win.screen;
      const ScrProto = win.Screen ? win.Screen.prototype : null;
      const screenProps = {
        width: SCREEN_W,
        height: SCREEN_H,
        availWidth: SCREEN_W,
        availHeight: SCREEN_AVAIL_H,
        colorDepth: 24,
        pixelDepth: 24,
        availLeft: 0,
        availTop: 0
      };

      for (const [prop, val] of Object.entries(screenProps)) {
        const getter = makeNative(function() { return val; }, `get ${prop}`);
        if (ScrProto) {
          try {
            Object.defineProperty(ScrProto, prop, { get: getter, configurable: true, enumerable: true });
          } catch(e) {}
        }
        if (scr) {
          try {
            Object.defineProperty(scr, prop, { get: getter, configurable: true, enumerable: true });
          } catch(e) {}
        }
      }
    } catch(e) {}
  }

  applyScreenSpoofs(window);

  // ==========================================
  // 3. WEBGL GPU HARDWARE MASKING
  // ==========================================
  function hookWebGLProto(proto) {
    if (!proto || !proto.getParameter) return;
    const originalGetParameter = proto.getParameter;

    const patchedGetParameter = makeNative(function getParameter(param) {
      // UNMASKED_VENDOR_WEBGL
      if (param === 37445) return TARGET_GPU_VENDOR;
      // UNMASKED_RENDERER_WEBGL
      if (param === 37446) return TARGET_GPU_RENDERER;
      // VENDOR
      if (param === 7936) return "Google Inc.";
      // RENDERER
      if (param === 7937) return "WebKit WebGL";

      return originalGetParameter.call(this, param);
    }, 'getParameter');

    proto.getParameter = patchedGetParameter;

    if (proto.readPixels) {
      const origReadPixels = proto.readPixels;
      proto.readPixels = makeNative(function readPixels(x, y, w, h, format, type, pixels) {
        const ret = origReadPixels.apply(this, arguments);
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

    // Also protect getExtension('WEBGL_debug_renderer_info')
    const originalGetExtension = proto.getExtension;
    if (originalGetExtension) {
      proto.getExtension = makeNative(function getExtension(name) {
        return originalGetExtension.call(this, name);
      }, 'getExtension');
    }
  }

  if (window.WebGLRenderingContext) hookWebGLProto(WebGLRenderingContext.prototype);
  if (window.WebGL2RenderingContext) hookWebGLProto(WebGL2RenderingContext.prototype);

  // Also hook OffscreenCanvas if present
  if (window.OffscreenCanvas) {
    try {
      const origOffscreenGetContext = OffscreenCanvas.prototype.getContext;
      OffscreenCanvas.prototype.getContext = makeNative(function getContext(type, ...args) {
        const ctx = origOffscreenGetContext.call(this, type, ...args);
        if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {
          hookWebGLProto(Object.getPrototypeOf(ctx));
        }
        return ctx;
      }, 'getContext');
    } catch(e) {}
  }

  // ==========================================
  // 4. CANVAS FINGERPRINT SHIELD (PER-PROFILE DETERMINISTIC ISOLATION)
  // ==========================================
  // Noise is fixed per profile (same drawing -> same hash on every read) and differs between
  // profiles. It is applied to a copy at read time, so the page's own canvas is never changed.
  // Colour noise goes only on fully opaque pixels: Chrome stores canvas pixels premultiplied by
  // alpha, so a +-1 on a semi-transparent pixel is rounded away on the way back out. On those
  // pixels the alpha value itself is nudged instead, which survives exactly.
  try {
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    const origPutImageData = CanvasRenderingContext2D.prototype.putImageData;
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    const origToBlob = HTMLCanvasElement.prototype.toBlob;

    // A hooked function carries its own toString (see makeNative); a native one does not.
    // If this script lands twice in one page (extension + CDP), the second copy must not stack.
    const alreadyShielded = Object.prototype.hasOwnProperty.call(origToDataURL, 'toString');

    if (!alreadyShielded) {
      const MAX_NOISE_PIXELS = 4000000;  // bigger canvases are images, not fingerprint probes

      // data holds the w x h rect whose top-left is (ox, oy) on the canvas. Pixels are picked by
      // canvas position, so getImageData and toDataURL agree on where the noise is.
      function addNoise(data, ox, oy, w, h) {
        if (w * h > MAX_NOISE_PIXELS) return;
        for (let y = 0; y < h; y++) {
          for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4;
            const a = data[i + 3];
            if (a === 0) continue;
            const r = pixelHash(ox + x, oy + y);
            if ((r & 15) !== 0) continue;  // about 1 in 16 pixels
            const up = ((r >>> 8) & 1) === 1;
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

      function noiseImageData(img, sx, sw, sy, sh) {
        // a negative width or height means the rect extends left or up from (sx, sy)
        const ox = Math.floor(sw < 0 ? sx + sw : sx);
        const oy = Math.floor(sh < 0 ? sy + sh : sy);
        addNoise(img.data, ox, oy, img.width, img.height);
      }

      function noisyCopy(canvas) {
        const w = canvas.width, h = canvas.height;
        if (!w || !h || w * h > MAX_NOISE_PIXELS) return null;
        const copy = document.createElement('canvas');
        copy.width = w;
        copy.height = h;
        const ctx = copy.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(canvas, 0, 0);
        const img = origGetImageData.call(ctx, 0, 0, w, h);  // throws on a tainted canvas
        addNoise(img.data, 0, 0, w, h);
        origPutImageData.call(ctx, img, 0, 0);
        return copy;
      }

      CanvasRenderingContext2D.prototype.getImageData = makeNative(function getImageData(sx, sy, sw, sh) {
        const img = origGetImageData.apply(this, arguments);
        try { noiseImageData(img, sx, sw, sy, sh); } catch(e) {}
        return img;
      }, 'getImageData');

      // Export from the noisy copy; on any failure (tainted, zero-size) fall through to the
      // original so the page sees exactly the error or result it would natively.
      HTMLCanvasElement.prototype.toDataURL = makeNative(function toDataURL() {
        let copy = null;
        try { copy = noisyCopy(this); } catch(e) { copy = null; }
        return origToDataURL.apply(copy || this, arguments);
      }, 'toDataURL');

      HTMLCanvasElement.prototype.toBlob = makeNative(function toBlob(callback) {
        let copy = null;
        try { copy = noisyCopy(this); } catch(e) { copy = null; }
        return origToBlob.apply(copy || this, arguments);
      }, 'toBlob');

      if (window.OffscreenCanvas && window.OffscreenCanvasRenderingContext2D) {
        const origOffGetImageData = OffscreenCanvasRenderingContext2D.prototype.getImageData;
        const origOffPutImageData = OffscreenCanvasRenderingContext2D.prototype.putImageData;
        const origConvertToBlob = OffscreenCanvas.prototype.convertToBlob;

        OffscreenCanvasRenderingContext2D.prototype.getImageData = makeNative(function getImageData(sx, sy, sw, sh) {
          const img = origOffGetImageData.apply(this, arguments);
          try { noiseImageData(img, sx, sw, sy, sh); } catch(e) {}
          return img;
        }, 'getImageData');

        if (origConvertToBlob) {
          OffscreenCanvas.prototype.convertToBlob = makeNative(function convertToBlob() {
            let copy = null;
            try {
              const w = this.width, h = this.height;
              if (w && h && w * h <= MAX_NOISE_PIXELS) {
                copy = new OffscreenCanvas(w, h);
                const ctx = copy.getContext('2d', { willReadFrequently: true });
                ctx.drawImage(this, 0, 0);
                const img = origOffGetImageData.call(ctx, 0, 0, w, h);
                addNoise(img.data, 0, 0, w, h);
                origOffPutImageData.call(ctx, img, 0, 0);
              }
            } catch(e) { copy = null; }
            return origConvertToBlob.apply(copy || this, arguments);
          }, 'convertToBlob');
        }
      }
    }

    // ==========================================
    // 5. FONTS -- handled at the engine, not here
    // ==========================================
    // This section used to hook measureText and document.fonts.check to hide
    // one or two names picked out of a hardcoded eight-font pool. It has been
    // removed rather than extended, for two reasons.
    //
    // It solved the wrong problem. A profile claiming Windows enumerated
    // thirteen Linux fonts, five of which (C059, P052, Z003, D050000L,
    // Standard Symbols PS) exist on no Windows machine -- and the hook's
    // response was to hide Georgia.
    //
    // And a canvas-only hook is a tell in itself. Measured on this host,
    // canvas and DOM font detection agree exactly, so suppressing a font in
    // measureText while a span measuring the same family still laid out at
    // its real width would have produced a disagreement no real browser
    // shows. The DOM half cannot be moved honestly from JavaScript, because
    // reporting a width the engine did not lay out desynchronises measurement
    // from rendering.
    //
    // The font set is now decided for the whole engine via a per-profile
    // fontconfig (see multiprofile/browser/fontconfig.py), so canvas, DOM,
    // document.fonts, workers and what is actually painted all agree without
    // anything having to lie.

  } catch(e) {}

  // CreepJS and similar probes fingerprint canvas inside Worker/OffscreenCanvas.
  // Content scripts do not run in workers, so wrap Worker to prepend the same seed.
  try {
    if (typeof Worker !== "undefined") {
      const NativeWorker = Worker;
      const prelude =
        "self.__MP_SEED=" + String(PROFILE_SEED | 0) + ";" +
        "self.__MP_GPU_V=" + JSON.stringify(TARGET_GPU_VENDOR) + ";" +
        "self.__MP_GPU_R=" + JSON.stringify(TARGET_GPU_RENDERER) + ";" +
        "(" + function () {
          const PROFILE_SEED = self.__MP_SEED || 1;
          function pixelHash(x, y) {
            let h = Math.imul(PROFILE_SEED ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(x + 1, 0xc2b2ae35) ^ Math.imul(y + 1, 0x27d4eb2f);
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
              self.OffscreenCanvasRenderingContext2D.prototype.getImageData = function (sx, sy, sw, sh) {
                const img = orig.apply(this, arguments);
                try { addNoise(img.data, 0, 0, img.width, img.height); } catch (e) {}
                return img;
              };
            }
          } catch (e) {}
          try {
            const hookGL = function (proto) {
              if (!proto || !proto.getParameter) return;
              const origGP = proto.getParameter;
              proto.getParameter = function (param) {
                if (param === 37445) return self.__MP_GPU_V;
                if (param === 37446) return self.__MP_GPU_R;
                return origGP.call(this, param);
              };
              if (proto.readPixels) {
                const origRP = proto.readPixels;
                proto.readPixels = function (x, y, w, h, format, type, pixels) {
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
                };
              }
            };
            if (self.WebGLRenderingContext) hookGL(self.WebGLRenderingContext.prototype);
            if (self.WebGL2RenderingContext) hookGL(self.WebGL2RenderingContext.prototype);
          } catch (e) {}
        }.toString() + ")();";
      function wrapWorkerScript(scriptURL) {
        const blob = new Blob(
          [prelude, "\ntry{importScripts(", JSON.stringify(String(scriptURL)), ");}catch(e){}"],
          { type: "text/javascript" }
        );
        return URL.createObjectURL(blob);
      }
      const WrappedWorker = function Worker(scriptURL, options) {
        return new NativeWorker(wrapWorkerScript(scriptURL), options);
      };
      WrappedWorker.prototype = NativeWorker.prototype;
      window.Worker = makeNative(WrappedWorker, "Worker");
    }
  } catch (e) {}

  // ==========================================
  // 6. CLIENT HINTS CONSISTENCY
  // ==========================================
  try {
    if (navigator.userAgentData) {
      const isMobile = isAndroid || isIPhone;
      const platformName = isMac ? "macOS" : (isAndroid ? "Android" : "Windows");

      // If iPhone, userAgentData should be undefined like real Safari
      if (isIPhone) {
        Object.defineProperty(navigator, 'userAgentData', {
          get: makeNative(function() { return undefined; }, 'get userAgentData'),
          configurable: true
        });
      }
    }
  } catch(e) {}

  // Override client hints from Python fingerprint when present
  try {
    if (MP_CLIENT_HINTS && navigator.userAgentData) {
      const ch = MP_CLIENT_HINTS;
      const brands = ch.brands || [
        { brand: "Chromium", version: (ch.uaFullVersion || "131").split(".")[0] },
        { brand: "Google Chrome", version: (ch.uaFullVersion || "131").split(".")[0] },
        { brand: "Not-A.Brand", version: "99" }
      ];
      const uaData = {
        brands: brands,
        mobile: !!ch.mobile,
        platform: ch.platform || TARGET_PLATFORM,
        getHighEntropyValues: makeNative(function getHighEntropyValues(hints) {
          return Promise.resolve({
            brands: brands,
            mobile: !!ch.mobile,
            platform: ch.platform || "",
            platformVersion: ch.platformVersion || "",
            architecture: ch.architecture || "",
            bitness: ch.bitness || "",
            model: ch.model || "",
            uaFullVersion: ch.uaFullVersion || "",
            fullVersionList: brands.map(b => ({ brand: b.brand, version: ch.uaFullVersion || b.version }))
          });
        }, 'getHighEntropyValues'),
        toJSON: makeNative(function toJSON() {
          return { brands: brands, mobile: !!ch.mobile, platform: ch.platform || "" };
        }, 'toJSON')
      };
      Object.defineProperty(navigator, 'userAgentData', {
        get: makeNative(function() { return uaData; }, 'get userAgentData'),
        configurable: true
      });
    }
  } catch(e) {}

  // WebRTC hardening when profile requests disable modes
  try {
    if (MP_WEBRTC_MODE === "disable") {
      const StubPC = makeNative(function RTCPeerConnection() {
        throw new DOMException("WebRTC disabled for this profile", "NotSupportedError");
      }, 'RTCPeerConnection');
      window.RTCPeerConnection = StubPC;
      if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = StubPC;
    } else if (MP_WEBRTC_MODE === "disable_non_proxied_udp") {
      // Light candidate filter — strip host candidates when possible
      if (window.RTCPeerConnection) {
        const OrigPC = window.RTCPeerConnection;
        window.RTCPeerConnection = makeNative(function RTCPeerConnection(config, constraints) {
          const cfg = Object.assign({}, config || {});
          if (!cfg.iceTransportPolicy) cfg.iceTransportPolicy = "relay";
          return new OrigPC(cfg, constraints);
        }, 'RTCPeerConnection');
        window.RTCPeerConnection.prototype = OrigPC.prototype;
        try { Object.keys(OrigPC).forEach(k => { try { window.RTCPeerConnection[k] = OrigPC[k]; } catch(e){} }); } catch(e){}
      }
    }
  } catch(e) {}

  // ==========================================
  // 7. WEB WORKER SPOOFING (CRITICAL FOR CREEPJS)
  // ==========================================
  try {
    const workerPreamble = `
      try {
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => ${TARGET_CORES}, configurable: true });
        ${TARGET_MEMORY !== undefined ? `Object.defineProperty(navigator, 'deviceMemory', { get: () => ${TARGET_MEMORY}, configurable: true });` : 'delete navigator.deviceMemory;'}
        Object.defineProperty(navigator, 'platform', { get: () => ${JSON.stringify(TARGET_PLATFORM)}, configurable: true });
        if (self.OffscreenCanvas) {
          const origGetContext = OffscreenCanvas.prototype.getContext;
          OffscreenCanvas.prototype.getContext = function(type, ...args) {
            const ctx = origGetContext.call(this, type, ...args);
            if (ctx && (type === 'webgl' || type === 'webgl2')) {
              const origGetParam = ctx.getParameter;
              ctx.getParameter = function(p) {
                if (p === 37445) return ${JSON.stringify(TARGET_GPU_VENDOR)};
                if (p === 37446) return ${JSON.stringify(TARGET_GPU_RENDERER)};
                return origGetParam.call(this, p);
              };
            }
            return ctx;
          };
        }
        if (self.Intl && self.Intl.DateTimeFormat) {
          const origRes = Intl.DateTimeFormat.prototype.resolvedOptions;
          Intl.DateTimeFormat.prototype.resolvedOptions = function() {
            const r = origRes.apply(this, arguments);
            r.timeZone = ${JSON.stringify(TARGET_TIMEZONE)};
            return r;
          };
        }
        Date.prototype.getTimezoneOffset = function() {
          const dUTC = new Date(this.toLocaleString('en-US', { timeZone: 'UTC' }));
          const dTZ = new Date(this.toLocaleString('en-US', { timeZone: ${JSON.stringify(TARGET_TIMEZONE)} }));
          return Math.round((dUTC - dTZ) / 60000);
        };
      } catch(e) {}
    `;

    const originalCreateObjectURL = URL.createObjectURL;
    URL.createObjectURL = makeNative(function createObjectURL(object) {
      if (object instanceof Blob && (object.type === 'application/javascript' || object.type === 'text/javascript' || object.type === '')) {
        try {
          const newBlob = new Blob([workerPreamble, object], { type: object.type || 'application/javascript' });
          return originalCreateObjectURL.call(this, newBlob);
        } catch(e) {}
      }
      return originalCreateObjectURL.call(this, object);
    }, 'createObjectURL');
  } catch(e) {}

  // ==========================================
  // 8. IFRAME REALM SPOOFING
  // ==========================================
  try {
    const origIFrameContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow')?.get;
    if (origIFrameContentWindow) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: makeNative(function() {
          const win = origIFrameContentWindow.call(this);
          if (win) {
            try {
              applyHardwareSpoofs(win.navigator, win.Navigator ? win.Navigator.prototype : null);
              applyScreenSpoofs(win);
              applyTimezoneSpoofs(win);
              if (win.WebGLRenderingContext) hookWebGLProto(win.WebGLRenderingContext.prototype);
              if (win.WebGL2RenderingContext) hookWebGLProto(win.WebGL2RenderingContext.prototype);
              applyWebRTCSpoofs(win);
              // Surfaces added after this hook was written were not reaching
              // iframes: an audio or rect fingerprint taken inside a frame
              // returned the host's, while the same read in the top document
              // returned the profile's. One page disagreeing with itself is
              // the tell these spoofs exist to prevent.
              applyAudioSpoofs(win);
              applyRectSpoofs(win);
              applyWebGPUSpoofs(win);
            } catch(e) {}
          }
          return win;
        }, 'get contentWindow'),
        configurable: true
      });
    }
  } catch(e) {}

  // ==========================================
  // 9. BULLETPROOF TIMEZONE & INTL SPOOFING
  // ==========================================
  function applyTimezoneSpoofs(targetScope) {
    try {
      const scope = targetScope || window;
      if (!scope.Intl || !scope.Date) return;

      function getTzOffsetMinutes(date, tz) {
        try {
          const dUTC = new Date(date.toLocaleString('en-US', { timeZone: 'UTC' }));
          const dTZ = new Date(date.toLocaleString('en-US', { timeZone: tz }));
          return Math.round((dUTC - dTZ) / 60000);
        } catch(e) {
          return 240;
        }
      }

      // 1. Intl.DateTimeFormat
      const OriginalDateTimeFormat = scope.Intl.DateTimeFormat;
      function PatchedDateTimeFormat(locales, options) {
        const opt = options ? Object.assign({}, options) : {};
        if (!opt.timeZone) {
          opt.timeZone = TARGET_TIMEZONE;
        }
        return new OriginalDateTimeFormat(locales, opt);
      }
      PatchedDateTimeFormat.prototype = OriginalDateTimeFormat.prototype;
      PatchedDateTimeFormat.supportedLocalesOf = OriginalDateTimeFormat.supportedLocalesOf;
      makeNative(PatchedDateTimeFormat, 'DateTimeFormat');
      scope.Intl.DateTimeFormat = PatchedDateTimeFormat;

      // 2. Intl.DateTimeFormat.prototype.resolvedOptions
      const originalResolvedOptions = OriginalDateTimeFormat.prototype.resolvedOptions;
      OriginalDateTimeFormat.prototype.resolvedOptions = function() {
        const res = originalResolvedOptions.apply(this, arguments);
        try {
          res.timeZone = TARGET_TIMEZONE;
        } catch(e) {}
        return res;
      };
      makeNative(OriginalDateTimeFormat.prototype.resolvedOptions, 'resolvedOptions');

      // 3. Date.prototype.getTimezoneOffset
      const originalGetTimezoneOffset = scope.Date.prototype.getTimezoneOffset;
      scope.Date.prototype.getTimezoneOffset = function() {
        return getTzOffsetMinutes(this, TARGET_TIMEZONE);
      };
      makeNative(scope.Date.prototype.getTimezoneOffset, 'getTimezoneOffset');

      // 4. Date.prototype.toString & toTimeString
      function formatTzDateString(d, tz, timeOnly) {
        const offsetMin = getTzOffsetMinutes(d, tz);
        const sign = offsetMin > 0 ? '-' : '+';
        const absMin = Math.abs(offsetMin);
        const h = String(Math.floor(absMin / 60)).padStart(2, '0');
        const m = String(absMin % 60).padStart(2, '0');
        const gmt = 'GMT' + sign + h + m;

        const parts = new OriginalDateTimeFormat('en-US', {
          timeZone: tz,
          weekday: 'short', month: 'short', day: '2-digit', year: 'numeric',
          hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
          timeZoneName: 'long'
        }).formatToParts(d);

        const map = {};
        for (const p of parts) map[p.type] = p.value;
        if (timeOnly) {
          return `${map.hour}:${map.minute}:${map.second} ${gmt} (${map.timeZoneName})`;
        }
        return `${map.weekday} ${map.month} ${map.day} ${map.year} ${map.hour}:${map.minute}:${map.second} ${gmt} (${map.timeZoneName})`;
      }

      const originalDateToString = scope.Date.prototype.toString;
      scope.Date.prototype.toString = function() {
        if (isNaN(this.getTime())) return originalDateToString.apply(this, arguments);
        return formatTzDateString(this, TARGET_TIMEZONE, false);
      };
      makeNative(scope.Date.prototype.toString, 'toString');

      const originalDateToTimeString = scope.Date.prototype.toTimeString;
      scope.Date.prototype.toTimeString = function() {
        if (isNaN(this.getTime())) return originalDateToTimeString.apply(this, arguments);
        return formatTzDateString(this, TARGET_TIMEZONE, true);
      };
      makeNative(scope.Date.prototype.toTimeString, 'toTimeString');

    } catch(e) {}
  }

  // ==========================================
  // 10. BULLETPROOF WEBRTC LEAK DEFENDER
  // ==========================================
  function applyWebRTCSpoofs(targetScope) {
    try {
      const scope = targetScope || window;
      if (!scope || !TARGET_IP) return;

      function sanitizeCandidateStr(str) {
        if (!str || typeof str !== 'string') return str;
        // Replace IPv4 pattern (excluding localhost / 0.0.0.0)
        let res = str.replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, (match) => {
          if (match.startsWith('127.') || match === '0.0.0.0') return match;
          return TARGET_IP;
        });
        // Replace IPv6 patterns with target IP
        res = res.replace(/[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7}/g, TARGET_IP);
        res = res.replace(/(?:[0-9a-fA-F]{1,4}:){1,7}:[0-9a-fA-F]{1,4}/g, TARGET_IP);
        res = res.replace(/2401:[0-9a-fA-F:]+/g, TARGET_IP);
        return res;
      }

      // 1. RTCIceCandidate Prototype
      if (scope.RTCIceCandidate) {
        const origCandDesc = Object.getOwnPropertyDescriptor(scope.RTCIceCandidate.prototype, 'candidate');
        if (origCandDesc && origCandDesc.get) {
          const origGet = origCandDesc.get;
          const newGet = function() {
            const val = origGet.call(this);
            return sanitizeCandidateStr(val);
          };
          makeNative(newGet, 'get candidate');
          Object.defineProperty(scope.RTCIceCandidate.prototype, 'candidate', {
            get: newGet,
            configurable: true,
            enumerable: true
          });
        }

        const origAddrDesc = Object.getOwnPropertyDescriptor(scope.RTCIceCandidate.prototype, 'address');
        if (origAddrDesc && origAddrDesc.get) {
          const origGet = origAddrDesc.get;
          const newGet = function() {
            const val = origGet.call(this);
            if (!val) return val;
            if (val.endsWith('.local') || val === '0.0.0.0' || val.startsWith('127.')) return val;
            return TARGET_IP;
          };
          makeNative(newGet, 'get address');
          Object.defineProperty(scope.RTCIceCandidate.prototype, 'address', {
            get: newGet,
            configurable: true,
            enumerable: true
          });
        }

        const origToJSON = scope.RTCIceCandidate.prototype.toJSON;
        if (origToJSON) {
          scope.RTCIceCandidate.prototype.toJSON = function() {
            const obj = origToJSON.call(this);
            if (obj) {
              if (obj.candidate) obj.candidate = sanitizeCandidateStr(obj.candidate);
              if (obj.address && !obj.address.endsWith('.local') && obj.address !== '0.0.0.0' && !obj.address.startsWith('127.')) {
                obj.address = TARGET_IP;
              }
            }
            return obj;
          };
          makeNative(scope.RTCIceCandidate.prototype.toJSON, 'toJSON');
        }
      }

      // 2. RTCSessionDescription Prototype (SDP)
      if (scope.RTCSessionDescription) {
        const origSdpDesc = Object.getOwnPropertyDescriptor(scope.RTCSessionDescription.prototype, 'sdp');
        if (origSdpDesc && origSdpDesc.get) {
          const origGet = origSdpDesc.get;
          const newGet = function() {
            const val = origGet.call(this);
            return sanitizeCandidateStr(val);
          };
          makeNative(newGet, 'get sdp');
          Object.defineProperty(scope.RTCSessionDescription.prototype, 'sdp', {
            get: newGet,
            configurable: true,
            enumerable: true
          });
        }

        const origSdpToJSON = scope.RTCSessionDescription.prototype.toJSON;
        if (origSdpToJSON) {
          scope.RTCSessionDescription.prototype.toJSON = function() {
            const obj = origSdpToJSON.call(this);
            if (obj && obj.sdp) obj.sdp = sanitizeCandidateStr(obj.sdp);
            return obj;
          };
          makeNative(scope.RTCSessionDescription.prototype.toJSON, 'toJSON');
        }
      }

      // 3. RTCPeerConnection Prototype getStats
      if (scope.RTCPeerConnection && scope.RTCPeerConnection.prototype.getStats) {
        const origGetStats = scope.RTCPeerConnection.prototype.getStats;
        scope.RTCPeerConnection.prototype.getStats = async function(...args) {
          const report = await origGetStats.apply(this, args);
          try {
            report.forEach((stat) => {
              if (stat.ip && !stat.ip.startsWith('127.') && stat.ip !== '0.0.0.0' && !stat.ip.endsWith('.local')) {
                stat.ip = TARGET_IP;
              }
              if (stat.address && !stat.address.startsWith('127.') && stat.address !== '0.0.0.0' && !stat.address.endsWith('.local')) {
                stat.address = TARGET_IP;
              }
            });
          } catch(e) {}
          return report;
        };
        makeNative(scope.RTCPeerConnection.prototype.getStats, 'getStats');
      }
    } catch(e) {}
  }

  // ==========================================
  // 11. AUDIOCONTEXT FINGERPRINT (PER-PROFILE DETERMINISTIC)
  // ==========================================
  // Measured before this existed: two profiles with different seeds, claiming
  // a GTX 1660 and an RTX 3060 respectively, both rendered
  //
  //     OfflineAudioContext -> oscillator -> compressor  sum = 172.6716509661
  //
  // byte for byte. The audio stack is the host's, so every profile on one
  // machine shared one audio fingerprint -- while canvas and WebGL differed
  // per profile. Two "different computers" with identical audio is how
  // profiles get linked to each other, which is worse than either of them
  // being identified alone.
  //
  // The noise is a pure function of (seed, sample index, original value), so
  // reading the same buffer twice gives the same answer. A value that moved
  // between reads would be a louder tell than the shared one, and would also
  // break anything doing real audio work. Amplitude is ~1e-7: inaudible, and
  // far below the threshold where playback is affected.
  function applyAudioSpoofs(scope) {
    try {
      const AB = scope.AudioBuffer;
      if (AB && AB.prototype && AB.prototype.getChannelData) {
        const orig = AB.prototype.getChannelData;
        const seen = new WeakMap();
        AB.prototype.getChannelData = makeNative(function(channel) {
          const data = orig.call(this, channel);
          try {
            let done = seen.get(this);
            if (!done) { done = new Set(); seen.set(this, done); }
            if (done.has(channel)) return data;   // already nudged; stay stable
            done.add(channel);
            for (let i = 0; i < data.length; i++) {
              if (data[i] !== 0) {
                data[i] += (getNoiseValue(PROFILE_SEED + i) - 0.5) * 1e-7;
              }
            }
          } catch (e) {}
          return data;
        }, 'getChannelData');
      }

      const AN = scope.AnalyserNode;
      if (AN && AN.prototype) {
        for (const name of ['getFloatFrequencyData', 'getByteFrequencyData']) {
          const fn = AN.prototype[name];
          if (!fn) continue;
          AN.prototype[name] = makeNative(function(array) {
            fn.call(this, array);
            try {
              for (let i = 0; i < array.length; i++) {
                const n = (getNoiseValue(PROFILE_SEED + i) - 0.5);
                array[i] += (name === 'getByteFrequencyData') ? 0 : n * 1e-4;
              }
            } catch (e) {}
          }, name);
        }
      }
    } catch (e) {}
  }

  // ==========================================
  // 12. CLIENTRECTS / DOMRECT (PER-PROFILE DETERMINISTIC)
  // ==========================================
  // Same measurement, same problem: two profiles claiming different hardware
  // both reported
  //
  //     getBoundingClientRect -> 10.093750, 20.187500, 30.296875, 40.390625
  //
  // identically, because sub-pixel layout metrics come from the host's
  // rendering stack. Another vector linking every profile on the machine.
  //
  // The nudge is deterministic in the ORIGINAL value, so an unchanged element
  // measures the same every time -- a rect that drifted between calls would
  // break drag-and-drop, sticky headers and hit-testing on real sites, and a
  // site polling one rect would see it move. It is kept at ~1e-3 of a pixel:
  // enough to separate two machines, far too small to disturb layout logic.
  function applyRectSpoofs(scope) {
    try {
      const nudge = (v) => {
        if (typeof v !== 'number' || !isFinite(v) || v === 0) return v;
        const k = Math.abs(Math.round(v * 1000));
        return v + (getNoiseValue(PROFILE_SEED + k) - 0.5) * 1e-3;
      };
      const patchRect = (r) => {
        try {
          const x = nudge(r.x), y = nudge(r.y);
          const w = nudge(r.width), h = nudge(r.height);
          return new scope.DOMRect(x, y, w, h);
        } catch (e) { return r; }
      };

      for (const proto of [scope.Element && scope.Element.prototype,
                           scope.Range && scope.Range.prototype]) {
        if (!proto) continue;
        if (proto.getBoundingClientRect) {
          const orig = proto.getBoundingClientRect;
          proto.getBoundingClientRect = makeNative(function() {
            return patchRect(orig.apply(this, arguments));
          }, 'getBoundingClientRect');
        }
        if (proto.getClientRects) {
          const origList = proto.getClientRects;
          proto.getClientRects = makeNative(function() {
            const list = origList.apply(this, arguments);
            try {
              const out = Array.prototype.map.call(list, patchRect);
              out.item = (i) => out[i] || null;
              return out;
            } catch (e) { return list; }
          }, 'getClientRects');
        }
      }
    } catch (e) {}
  }


  // ==========================================
  // 13. WEBGPU ADAPTER IDENTITY
  // ==========================================
  // WebGL and WebGPU describe the same card in different vocabularies and a
  // page can read both. Measured on this host, one profile said both of these
  // at once:
  //
  //   WebGL   ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ...)
  //   WebGPU  requestAdapter() -> null
  //
  // No Windows machine with an RTX 4060 has no WebGPU adapter. The null came
  // from our own launch configuration -- forcing --ozone-platform=x11, which
  // the window placement needs, left Dawn without one. Measured against stock
  // Chromium 151 on the same host, where an adapter IS returned, it described
  // the host truthfully and identically for every profile:
  //
  //   {vendor: "intel", architecture: "gen-12lp"}
  //
  // So the surface both contradicted the WebGL claim and linked every profile
  // on the machine to every other. The launcher now restores the adapter with
  // --enable-features=Vulkan; this rewrites what it says about itself.
  //
  // Only the four identity fields are replaced. The adapter itself is the
  // real one, so limits, features and requestDevice() keep working -- a
  // synthesised adapter that failed on first use would be a louder tell than
  // the one it replaced.
  function applyWebGPUSpoofs(scope) {
    try {
      if (!TARGET_WEBGPU || !TARGET_WEBGPU.vendor) return;
      const gpu = scope.navigator && scope.navigator.gpu;
      if (!gpu || typeof gpu.requestAdapter !== 'function') return;
      if (gpu.__mp_hooked) return;

      const FIELDS = ['vendor', 'architecture', 'device', 'description'];
      const disguise = (info) => {
        if (!info) return info;
        try {
          for (const key of FIELDS) {
            const value = TARGET_WEBGPU[key] != null ? TARGET_WEBGPU[key] : '';
            Object.defineProperty(info, key, {
              get: makeNative(function () { return value; }, `get ${key}`),
              configurable: true,
              enumerable: true
            });
          }
        } catch (e) {}
        return info;
      };

      const origRequestAdapter = gpu.requestAdapter;
      gpu.requestAdapter = makeNative(function requestAdapter() {
        return origRequestAdapter.apply(this, arguments).then(function (adapter) {
          if (!adapter) return adapter;
          try {
            // `info` is a prototype getter returning a live GPUAdapterInfo;
            // shadowing it with an own accessor leaves the rest of the
            // adapter untouched.
            disguise(adapter.info);
            if (typeof adapter.requestAdapterInfo === 'function') {
              const origInfo = adapter.requestAdapterInfo;
              adapter.requestAdapterInfo = makeNative(function requestAdapterInfo() {
                return origInfo.apply(this, arguments).then(disguise);
              }, 'requestAdapterInfo');
            }
          } catch (e) {}
          return adapter;
        });
      }, 'requestAdapter');

      try {
        Object.defineProperty(gpu, '__mp_hooked', { value: true, enumerable: false });
      } catch (e) {}
    } catch (e) {}
  }

  // Apply to main window
  applyTimezoneSpoofs(window);
  applyWebRTCSpoofs(window);
  applyAudioSpoofs(window);
  applyRectSpoofs(window);
  applyWebGPUSpoofs(window);

  // ==========================================
  // NAVIGATOR.PLUGINS + MIMETYPES CONSISTENCY
  // ==========================================
  // Purpose: Desktop Chrome exposes 3 built-in plugin entries.
  //          A completely empty navigator.plugins is an automation signal.
  // Scope:   MAIN world, document_start.
  // Rollback: Remove this block only — other spoofs are independent.
  try {
    // Only add plugins for desktop profiles (touch=0)
    if (TARGET_TOUCH === 0) {
      const fakePlugin = (name, filename, desc, mimeTypes) => {
        const p = Object.create(Plugin.prototype);
        Object.defineProperty(p, 'name', { get: () => name, enumerable: true });
        Object.defineProperty(p, 'filename', { get: () => filename, enumerable: true });
        Object.defineProperty(p, 'description', { get: () => desc, enumerable: true });
        Object.defineProperty(p, 'length', { get: () => mimeTypes.length, enumerable: true });
        mimeTypes.forEach((mt, i) => { p[i] = mt; });
        return p;
      };
      const fakeMime = (type, desc, suffixes) => {
        const m = Object.create(MimeType.prototype);
        Object.defineProperty(m, 'type', { get: () => type, enumerable: true });
        Object.defineProperty(m, 'description', { get: () => desc, enumerable: true });
        Object.defineProperty(m, 'suffixes', { get: () => suffixes, enumerable: true });
        return m;
      };
      const pdfMime = fakeMime('application/pdf', 'Portable Document Format', 'pdf');
      const pdfXMime = fakeMime('application/x-google-chrome-pdf', 'Portable Document Format', 'pdf');
      const plugin1 = fakePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime]);
      const plugin2 = fakePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [pdfXMime]);
      const plugin3 = fakePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format', [pdfMime]);
      const fakePL = Object.create(PluginArray.prototype);
      Object.defineProperty(fakePL, 'length', { get: () => 3, enumerable: true });
      [plugin1, plugin2, plugin3].forEach((p, i) => { fakePL[i] = p; });
      fakePL.item = i => fakePL[i] || null;
      fakePL.namedItem = n => [plugin1, plugin2, plugin3].find(p => p.name === n) || null;
      fakePL.refresh = () => {};
      makeNative(fakePL.item, 'item');
      makeNative(fakePL.namedItem, 'namedItem');
      makeNative(fakePL.refresh, 'refresh');
      Object.defineProperty(Navigator.prototype, 'plugins', {
        get: makeNative(() => fakePL, 'get plugins'),
        configurable: true, enumerable: true
      });
      const fakeML = Object.create(MimeTypeArray.prototype);
      Object.defineProperty(fakeML, 'length', { get: () => 2, enumerable: true });
      fakeML[0] = pdfMime; fakeML[1] = pdfXMime;
      fakeML.item = i => fakeML[i] || null;
      fakeML.namedItem = n => [pdfMime, pdfXMime].find(m => m.type === n) || null;
      makeNative(fakeML.item, 'item');
      makeNative(fakeML.namedItem, 'namedItem');
      Object.defineProperty(Navigator.prototype, 'mimeTypes', {
        get: makeNative(() => fakeML, 'get mimeTypes'),
        configurable: true, enumerable: true
      });
    }
  } catch(e) {}

  // ==========================================
  // WINDOW.CHROME SUPPLEMENT
  // ==========================================
  // Purpose: Real Chrome exposes window.chrome with loadTimes, csi, app.
  //          Missing or empty window.chrome is an automation indicator.
  // Scope:   MAIN world only; does not apply to workers.
  try {
    if (!window.chrome || typeof window.chrome !== 'object') {
      window.chrome = {};
    }
    if (!window.chrome.app) {
      window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        getDetails: makeNative(function() { return null; }, 'getDetails'),
        getIsInstalled: makeNative(function() { return false; }, 'getIsInstalled'),
        runningState: makeNative(function() { return 'cannot_run'; }, 'runningState'),
      };
    }
    if (!window.chrome.runtime) {
      window.chrome.runtime = { PlatformOs: {}, PlatformArch: {}, PlatformNaclArch: {} };
    }
    if (!window.chrome.loadTimes) {
      window.chrome.loadTimes = makeNative(function() {
        const t = performance.timing;
        return {
          requestTime: t.requestStart / 1000,
          startLoadTime: t.navigationStart / 1000,
          commitLoadTime: t.responseStart / 1000,
          finishDocumentLoadTime: t.domContentLoadedEventEnd / 1000,
          finishLoadTime: t.loadEventEnd / 1000,
          firstPaintTime: t.responseStart / 1000,
          firstPaintAfterLoadTime: 0,
          navigationType: 'Other',
          wasFetchedViaSpdy: false,
          wasNpnNegotiated: true,
          npnNegotiatedProtocol: 'h2',
          wasAlternateProtocolAvailable: false,
          connectionInfo: 'h2',
        };
      }, 'loadTimes');
    }
    if (!window.chrome.csi) {
      window.chrome.csi = makeNative(function() {
        return {
          startE: performance.timing.navigationStart,
          onloadT: performance.timing.loadEventEnd,
          pageT: performance.now(),
          tran: 15,
        };
      }, 'csi');
    }
  } catch(e) {}

  // ==========================================
  // NAVIGATOR.CONNECTION (NetworkInformation)
  // ==========================================
  // Purpose: Expose a realistic NetworkInformation object.
  //          Absence on desktop Chrome is a fingerprint gap.
  // Scope:   MAIN world only.
  try {
    if ('connection' in navigator) {
      const conn = navigator.connection;
      if (conn) {
        // Only override if already exists — don't create from scratch
        try {
          Object.defineProperty(conn, 'downlink', { get: makeNative(() => 10, 'get downlink'), configurable: true });
          Object.defineProperty(conn, 'effectiveType', { get: makeNative(() => '4g', 'get effectiveType'), configurable: true });
          Object.defineProperty(conn, 'rtt', { get: makeNative(() => 50, 'get rtt'), configurable: true });
          Object.defineProperty(conn, 'saveData', { get: makeNative(() => false, 'get saveData'), configurable: true });
        } catch(e) {}
      }
    }
  } catch(e) {}

  // ==========================================
  // STABLE MEDIA DEVICE IDs
  // ==========================================
  // Purpose: Real browsers return stable device IDs per origin after permission grant.
  //          Randomised IDs on every call is an automation signal.
  //          We intercept enumerateDevices and return seeded stable IDs.
  // Limitation: IDs change on permission revoke in real Chrome too.
  // Scope:   MAIN world.
  try {
    const MP_DEVICE_IDS = (typeof __MP_INLINE_CFG__ !== 'undefined' && __MP_INLINE_CFG__.mediaDeviceIds)
      ? __MP_INLINE_CFG__.mediaDeviceIds
      : { audioinput: null, audiooutput: null, videoinput: null };
    const MP_DEVICE_GROUPS = (typeof __MP_INLINE_CFG__ !== 'undefined' && __MP_INLINE_CFG__.mediaDeviceGroupIds)
      ? __MP_INLINE_CFG__.mediaDeviceGroupIds
      : { audioinput: null, audiooutput: null, videoinput: null };

    if (MP_DEVICE_IDS.audioinput && navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
      const origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
      navigator.mediaDevices.enumerateDevices = makeNative(async function() {
        try {
          const devices = await origEnum();
          return devices.map((dev) => {
            // Only substitute what the browser is ALREADY exposing. Without
            // media permission Chrome returns deviceId '' (and label '',
            // groupId '') for every device -- measured on stock Chromium 151.
            // Replacing that with a stable 64-char id produced a combination
            // no real browser emits, an id beside an empty label and empty
            // group, and handed every site the same persistent identifier for
            // the life of the profile. Spoofing that volunteers an identifier
            // where the host volunteers none is worse than not spoofing.
            const realId = dev.deviceId || '';
            const realGroup = dev.groupId || '';
            let id = realId;
            let group = realGroup;
            if (realId) {
              const byKind = MP_DEVICE_IDS[dev.kind];
              if (byKind) id = byKind;
            }
            if (realGroup) {
              const g = MP_DEVICE_GROUPS[dev.kind];
              if (g) group = g;
            }
            // 'default' and 'communications' are Chrome's own reserved ids;
            // renaming them breaks getUserMedia and is itself a tell.
            if (realId === 'default' || realId === 'communications') id = realId;
            return { kind: dev.kind, label: dev.label, deviceId: id, groupId: group };
          });
        } catch(e) {
          return origEnum();
        }
      }, 'enumerateDevices');
    }
  } catch(e) {}

})();
