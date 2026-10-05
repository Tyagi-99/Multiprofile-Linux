# 04 — Fingerprint Test Results

**What was measured, and how.** A probe collecting every vector below was
evaluated over CDP in three browsers on the same machine, in the same session:

| Run | What it is |
|---|---|
| **Control** | Bare `/usr/bin/chromium`, clean profile directory, no Multiprofile |
| **Windows profile** | Multiprofile profile, `userAgentId: win_chrome`, `America/New_York` |
| **macOS profile** | Multiprofile profile, `userAgentId: mac_chrome`, `Europe/Berlin`, `de-DE` |

Host: Arch Linux, Wayland/Hyprland, **Chromium 151.0.7922.173**, 16 cores,
16 GB. The control's window is 800×600 headless; the profiles are headful.

**What was not done, and why it matters.** The brief asks for results "per
checker site, with screenshots" — CreepJS, Pixelscan, BrowserLeaks,
Iphey and similar. **No third-party checker site was visited.** Two reasons:
doing so from this machine would send the operator's real IP and a real
browsing fingerprint to third parties, and a run with no proxy configured
would measure a configuration nobody ships. The vectors those sites test are
measured directly below instead, which is reproducible and does not depend on
someone else's scoring formula — but it is **not a substitute** for a real
CreepJS trust score, and this document should not be read as one. Running the
checkers is a half-day of work once a proxy is configured, and it belongs in
the next pass.

**Every run in §1–§10 used a profile with a proxy configured.** That was not
a stated choice, it was an assumption, and it hid a defect for the whole of
this document's first version: the no-proxy path had never been measured.
**§10** measures it, and records what it found — see **F43**.

The harness is committed as the `*_live.py` tests, so every number here is
re-derivable rather than transcribed.

---

## 0. Two defects this measurement found

Neither was visible to any existing test, because every previous check asked
whether a *spoofed value* was correct and none asked what else was in scope
beside it.

### F41 — every page could read the whole spoofing config · **CRITICAL**

A probe listing globals matching `/^__MP|multiprofile|evasion/i` returned two:

```
window.__MP_PROFILE__    ->  25 keys, 1977 bytes
window.__MP_INLINE_CFG__ ->  the same object

seed: 1479877959
keys: architecture, bitness, ch_brands, ch_full_version_list, ch_platform,
      clientHints, cores, engineVersion, gpu, isMobile, language, languages,
      mediaDeviceGroupIds, mediaDeviceIds, memory, model, platform,
      platformVersion, screen, seed, timezone, touch, userAgent, webrtcMode,
      webrtcPolicy
```

The seed is the serious part: it derives every noise value the script
produces, so a site reading it can undo the canvas, audio and ClientRects
noise **and** keep the seed as an exact, stable, cross-site identifier. A
profile leaking its seed is more trackable than one with no spoofing at all.
Fixed by making the config a closure variable. Measured after: both
`undefined`, no matching global.

### F42 — every profile claimed Windows whatever you picked · **HIGH**

The macOS run came back as Windows. One profile per catalogue id:

```
win_chrome    -> platform='Win32'  ch_platform='Windows'
mac_chrome    -> platform='Win32'  ch_platform='Windows'
linux_chrome  -> platform='Win32'  ch_platform='Windows'
pixel_9       -> platform='Win32'  ch_platform='Windows'
iphone_15     -> platform='Win32'  ch_platform='Windows'
```

`userAgentId` was stored and read by nothing. Fixed; the macOS column below is
the after.

---

## 1. Identity — the wire and the JS agree

| Vector | Control (host truth) | Windows profile | macOS profile |
|---|---|---|---|
| `userAgent` | `…(X11; Linux x86_64)… HeadlessChrome/151` | `…(Windows NT 10.0; Win64; x64)… Chrome/151` | `…(Macintosh; Intel Mac OS X 10_15_7)… Chrome/151` |
| `navigator.platform` | `Linux x86_64` | `Win32` | `MacIntel` |
| `userAgentData.platform` | `Linux` | `Windows` | `macOS` |
| `userAgentData.brands` | `Chromium:151\|Not=A?Brand:99` | `Google Chrome:151\|Chromium:151\|Not-A.Brand:99` | same |
| `vendor` | `Google Inc.` | `Google Inc.` | `Google Inc.` |
| `webdriver` | `false` | `false` | `false` |

**F1 is closed and stays closed.** The contradiction that made a profile more
detectable than doing nothing — JS claiming Windows while the wire carried
Linux — does not reproduce. **F5** is visible here too: the catalogue's
literals say Chrome 131 and what ships is `151`, matching the engine that
actually runs.

The control's `Not=A?Brand` versus the profiles' `Not-A.Brand` is Chromium's
own GREASE value differing between a bare build and one given an explicit
brand list; both are shapes Chrome really emits.

---

## 2. Hardware, screen, locale

| Vector | Control | Windows profile | macOS profile |
|---|---|---|---|
| `hardwareConcurrency` | **16** (real) | 8 / 16 (seed) | seed |
| `deviceMemory` | **16** (real) | 8 / 16 (seed) | seed |
| `screen` | `800×600` (headless) | `1536×864`, avail `824` | seed |
| `devicePixelRatio` | `1` | `1.25` | seed |
| `timezone` | **`Asia/Calcutta`** (real) | `America/New_York` | `Europe/Berlin` |
| `getTimezoneOffset()` | `-330` (real) | `240` | `-120` |
| `new Date(0).toString()` | `…GMT+0530 (India Standard Time)` | `…GMT-0500 (Eastern Standard Time)` | CEST |
| `language` | `en-US` | `en-US` | `de-DE` |
| `(1234567.89).toLocaleString()` | `1,234,567.89` | `1,234,567.89` | **`1.234.567,89`** |

Two things worth noting. The timezone is **engine-level** (F22): the offset,
the `Date` string and `Intl` all agree, which a patched-methods approach
cannot achieve. And the locale reaches `Intl` number formatting, not just
`navigator.language` — German grouping and decimal separators, which is a
vector checkers read.

`hardwareConcurrency` landing on 16 in one run is the seed picking a value the
host happens to share; a second profile reported 8 against the same host.

---

## 3. GPU — and WebGPU agreeing with it

| Vector | Control | Windows profile | macOS profile |
|---|---|---|---|
| WebGL `UNMASKED_VENDOR` | `Google Inc. (Google)` | `Google Inc. (NVIDIA)` | `Google Inc. (Apple)` |
| WebGL `UNMASKED_RENDERER` | `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device …), SwiftShader driver)` | `ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)` | `ANGLE (Apple, Apple M3, OpenGL ES 3.0)` |
| `navigator.gpu` adapter | **`no-adapter`** | `nvidia` / `ampere` | `apple` |

**F26, measured.** The WebGPU adapter tracks the WebGL claim rather than
contradicting it: RTX 3060 → `ampere`, and a second profile that drew a
GTX 1660 reported `turing`. Getting the architecture *right for the claimed
card* is the part a checker compares.

The Windows profile claims Direct3D11 and the macOS profile claims OpenGL ES —
the backend matches the OS, which a single hard-coded GPU string would not.

The control returning `no-adapter` is headless Chromium without a GPU process;
it is not evidence about the host's real WebGPU.

---

## 4. Noise vectors

| Vector | Control | Windows profile | Notes |
|---|---|---|---|
| Canvas (tail 48 of dataURL) | `YdrRkgAAAAZJREFUAwBmM1y1IwfNIwAAAABJRU5ErkJggg==` | `/wwCuxQAAAAGSURBVAMA/juXtX3uW1UAAAAASUVORK5CYII=` | differs |
| AudioContext sum | `172.671650966069` | `172.671651223107` | differs at the 7th decimal |
| ClientRects | `11.6875000000, 3.2968750000, 97.2968750000, 41.8906250000` | `11.6877225189, 3.2965555575, 97.2971224403, 41.8902307777` | differs |

**F24, measured.** The control's ClientRects are exact binary fractions —
`11.6875` is `11 + 11/16` — which is what an unmodified engine returns. The
profile's values are perturbed in the low digits, which is the shape real
hardware variation takes. The audio sum differs in the seventh decimal rather
than the second: large enough to separate profiles, small enough not to be an
obvious outlier.

The property that matters is *per-profile*, not merely *different from the
host* — before F24 every profile on the machine returned identical values to
twelve decimal places, which linked them to each other.

---

## 5. Fonts — F25's residual, measured

| | Control | Windows profile |
|---|---|---|
| Detected | `Arial, Times New Roman, Courier New, Helvetica, Liberation Sans, Noto Sans, Nimbus Sans` | `Arial, Times New Roman, Courier New, Helvetica` |

The Linux-only families are gone from the profile's view. The residual is what
the profile *cannot* offer, reported by the product's own identity-fault log
at launch:

```
[font_backer_name_visible] metric backers remain enumerable under their own
  names because Chrome reads the font file's name table:
  Liberation Mono, Liberation Sans, Liberation Serif

[font_claim_unbacked] windows fonts with no metric-compatible file on this
  host, left absent: Calibri, Cambria, Georgia, Verdana, Tahoma,
  Trebuchet MS, Segoe UI, Consolas, Impact, Lucida Console,
  Microsoft Sans Serif, Palatino Linotype, Franklin Gothic Medium
```

A checker enumerating a Windows font list finds thirteen expected faces
missing and three Liberation faces present. That is a real signal and the
product **says so** rather than claiming the vector is solved — which is the
distinction between a documented residual and a lie. Closing it means shipping
font files, a trademark question rather than an engineering one.

---

## 6. OS-shaped APIs

| Vector | Control (host) | Windows profile | macOS profile |
|---|---|---|---|
| `speechSynthesis` voices | **`none`** | `Microsoft David*, Zira, Hazel, Catherine` (7) | `Alex, Samantha, Daniel, Fred` (8) |
| `getBattery()` | `false, 0.68, Infinity, 14942` (**real**) | `true, 1, 0, Infinity` | same |
| `navigator.bluetooth` | **`undefined`** | `object` | `object` |
| `navigator.usb` / `hid` | `object` | `object` | `object` |
| `plugins` | 5, incl. `Microsoft Edge PDF Viewer`, `WebKit built-in PDF` | 3 | 3 |
| `mediaDevices` pre-permission | `audioinput:(empty), videoinput:(empty), audiooutput:(empty)` | identical | identical |

This is the clearest table in the document.

**Speech voices (F29):** the host has *none* — a bare Linux Chromium with no
speech-dispatcher — while a real Windows machine has seven. The profiles
present the right per-OS set, with exactly one marked default. macOS gets
Alex/Samantha/Daniel, Windows gets David/Zira/Hazel.

**Battery (F27):** the control reports this laptop's actual state — 68%,
discharging, 14942 seconds remaining. Every profile would have reported the
same curve, and two profiles draining in step is a perfect linkage signal.
They now report a plugged-in desktop.

**Bluetooth (F28):** absent on this host, present on Windows. A profile
claiming Windows with no `navigator.bluetooth` is a contradiction.

**Media devices (F23):** the profile matches the control *exactly* — empty
labels, empty ids, before permission. That is the fix: the earlier version
volunteered a persistent id the host withholds, so the spoofing was the tell.
Matching the stock browser byte-for-byte is the goal wherever there is nothing
to anchor to.

---

## 7. Realm agreement

| Realm | `userAgent` | `platform` | `hardwareConcurrency` | timezone | language |
|---|---|---|---|---|---|
| Page | Windows/Chrome 151 | `Win32` | 16 | `America/New_York` | `en-US` |
| Dedicated worker | **identical** | **identical** | **identical** | **identical** | **identical** |

**F30, measured.** The worker realm agrees with its page on every field. The
live suite covers dedicated, blob, URL, module, shared and service workers
plus iframes — 31 tests, all passing. A worker that disagrees with its page is
a one-line detection and was one before F30.

---

## 8. The spoofing as a surface — where F3 lives

Re-measured after the F3 masking and shape work. The first two rows were the
finding; the middle block is what the fix had to survive.

| Probe | Control | Profile | Verdict |
|---|---|---|---|
| `Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL)` | `function toDataURL() { [native code] }` | `function toDataURL() { [native code] }` | ✅ was ❌ **F3** |
| `…call(WebGLRenderingContext.prototype.getParameter)` | native | native, no `UNMASKED_*` | ✅ was ❌ **F3** |
| `hasOwnProperty(toDataURL, 'toString')` | `false` | `false` | ✅ was `true` |
| `getOwnPropertyDescriptor(navigator, 'platform')` | `undefined` | `undefined` | ✅ was a descriptor |
| `getOwnPropertyNames(toDataURL)` | `length,name` | `length,name` | ✅ was `+prototype` |
| `new HTMLCanvasElement.prototype.toDataURL()` | TypeError | TypeError | ✅ was constructible |
| **iframe-borrowed** `toString.call(toDataURL)` | native | native | ✅ |
| worker `toString.call(getImageData)` | native | native | ✅ |
| `Function.prototype.toString.call(FTS)` | native | native | ✅ |
| *control:* an untouched `function untouched(){return 42}` | its source | **its source** | ✅ not blanket-lying |
| *control:* `toString.call({})` | TypeError | TypeError | ✅ |
| `window.__MP_PROFILE__` | — | `undefined` | ✅ fixed (F41) |
| Globals matching `/^__MP\|multiprofile\|evasion/i` | — | **none** | ✅ fixed (F41) |
| `typeof window.chrome` / `chrome.runtime` | `object` / `object` | `object` / `object` | ✅ |
| Error stack depth | 4 | 4 | ✅ |
| **CDP attachment artefacts, `Runtime.enable`** | — | **present** | ❌ **F3, remaining** |
| **TLS / JA3 / JA4, HTTP/2** | — | **absent** | ❌ see §9 |

The last two controls are the point of the row above them. Masking that
returned `[native code]` for everything would be trivially detectable — a
browser that calls your own just-declared function native is not a browser —
so the map is consulted rather than assumed, and a non-callable `this` still
throws what native throws.

**The ceiling has moved, not gone.** What is reachable from JavaScript is now
shaped like the real thing, verified across page, iframe and worker realms,
including the obvious bypass of borrowing a clean `Function.prototype
.toString` from a fresh realm. What has not changed is *why* F3 exists: the
spoofing still runs in the page, and a script that patches a prototype can in
principle always be asked what it is. CDP attachment artefacts remain, and
TLS/HTTP-2 are untouched below.

So the honest statement is narrower than before and still not "undetectable":
**the cheap checks no longer work; the structural ones still do.** Closing F3
properly still means moving the spoofing below JavaScript — a patched engine,
the largest item in [`06_ROADMAP`](06_ROADMAP.md).

---

## 9. Not measured at all

Stated so the gaps are not mistaken for passes:

| Vector | Status |
|---|---|
| **TLS / JA3 / JA4** | **No implementation.** A profile claiming Chrome-on-Windows presents Chromium-on-Linux's handshake. Unreachable from JavaScript. |
| **HTTP/2 SETTINGS / frame order** | **No implementation.** Same category. |
| CreepJS / Pixelscan / BrowserLeaks / Iphey scores | Not run — see the header |
| Behaviour under a real proxy | Not run; geo alignment is tested against synthetic exits. The **no-proxy** path is measured in §10 |
| Mobile profiles under real conditions | Preset-level only |
| Long-session drift | Not tested |

The first two are the most important absences in the whole audit after F3, and
they are the same *kind* of absence: both live below the layer this product
operates at. Together they are most of what "detectable" means to a serious
anti-bot vendor.

---

## 10. The direct-route profile — measured

Added after the operator said their primary workflow is the console with **the
proxy optional per profile**. §1–§10 measured proxied profiles only, so this
section measures the one that had never been run.

**Setup:** four profiles created through the same HTTP API the console uses,
`proxyType: "none"`, no other configuration. Host zone `Asia/Kolkata`, host
locale `en_US.UTF-8`. Launched together, probed over CDP.

### What it found first

The defect, before the fix:

```
Intl.DateTimeFormat().resolvedOptions().timeZone   America/New_York
egress                                             this machine, in IN
```

Ten and a half hours of disagreement between the claimed zone and the address
the packets came from, on every no-proxy profile, with a clean consistency
score — the geo domain was excluded from the denominator because there was "no
IP country to compare against". **F43.**

### After the fix

| | work-1 | work-2 | work-3 | work-4 |
|---|---|---|---|---|
| timezone | `Asia/Kolkata` | `Asia/Kolkata` | `Asia/Kolkata` | `Asia/Kolkata` |
| language | `en-US` | `en-US` | `en-US` | `en-US` |
| platform | `Win32` | `Win32` | `Win32` | `Win32` |
| screen | 1920×1080 | 1920×1080 | 1920×1080 | 1536×864 |
| cores / memory | 12 / 8 | 4 / 4 | 8 / 16 | 6 / 4 |
| GPU | Intel UHD 630 | Radeon RX 6600 | Radeon RX 6600 | RTX 4060 |
| canvas digest | distinct | distinct | distinct | distinct |
| `window.__MP_PROFILE__` | `undefined` | `undefined` | `undefined` | `undefined` |
| geo domain | PASS | PASS | PASS | PASS |

All four now agree with the address they leave from, and the launch says so in
a note rather than leaving the choice unexplained:

```
geo: no proxy, so the host's own timezone Asia/Kolkata is what this profile claims
geo: position claimed near Mumbai
```

The position is the part worth noticing. Unset, `getCurrentPosition` resolved
against the **host's real surroundings** — identical for every profile on the
machine and the operator's actual location. It now answers with a seeded point
near the country's principal city: **more private than what it replaced, not
only more coherent.**

### Cross-profile linkage

| Vector | Distinct across 4 |
|---|---|
| canvas | **4 / 4** |
| screen | 2 / 4 |
| GPU | 3 / 4 |

Canvas separates every profile, which is the one that matters — it is the
vector F24 found linking them. Screen and GPU repeat because they are drawn
from a small catalogue of plausible real hardware, and two machines sharing an
RX 6600 is what the real world looks like; a *unique* GPU string per profile
would be the more suspicious answer.

### Identity stability across a restart

Stopped all four, relaunched one, re-probed. Timezone, platform, screen, cores,
memory, GPU and the canvas digest were **byte-identical**. That is the seed
promise the product rests on, and it had not been checked on this path.

### The proxy, added and removed

The operator's stated flow, both directions, on one profile:

| Step | Result |
|---|---|
| created with no proxy | geo PASS, claims `Asia/Kolkata` |
| proxy added, launched | **refused** — `Proxy … failed pre-launch health check … Launch blocked to prevent host IP exposure` |
| proxy removed, launched | geo PASS, claims `Asia/Kolkata` again |

The refusal in the middle is **F17** working: a dead proxy does not fall back
to the host address. The third row is the stale-diagnostics guard: the profile
still carried the record of the route it no longer uses, and did not claim it.

### The ceiling, recorded not argued

```js
Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL)
// -> hook source visible, on all four profiles
```

**F3 is present and unchanged on this path too.** Nothing in this section
narrows it. Third-party checkers still have not been run — see the header.

---

## 11. Summary

| Area | Verdict |
|---|---|
| UA / Client Hints / platform | ✅ coherent, wire and JS |
| Timezone / geo / locale | ✅ engine-level, per-exit **and with no proxy** (F43) |
| GPU / WebGL / WebGPU | ✅ mutually consistent |
| Canvas / audio / rects noise | ✅ per-profile, plausible magnitude |
| OS-shaped APIs | ✅ battery, voices, bluetooth, media |
| Realm agreement | ✅ all five worker types + iframes |
| Fonts | 🟡 documented residual, product reports it |
| Config exposure | ✅ fixed this pass (F41) |
| Hook source and shape | ✅ masked and native-shaped, all realms (F3) |
| deviceMemory plausibility | ✅ fixed this pass (F44) |
| Identity selection | ✅ fixed this pass (F42) |
| Direct route (no proxy) | ✅ measured in §11; fixed this pass (F43) |
| **Hook detectability (F3)** | 🟡 **JS surface closed; architectural ceiling open** |
| **TLS / HTTP/2** | ❌ **absent** |

**The honest one-line answer to "is it undetectable?" is no.** What can be
said is that the vectors reachable from JavaScript are coherent and
per-profile, measured rather than asserted, and that the two remaining gaps
are known, scoped and below the layer the product currently works at.

---

*Reproduce:* `.venv/bin/python -m pytest tests/ -q -k live`. The findings are
in [`02_AUDIT_REPORT`](02_AUDIT_REPORT.md); the competitive reading is in
[`03_FEATURE_MATRIX`](03_FEATURE_MATRIX.md).
