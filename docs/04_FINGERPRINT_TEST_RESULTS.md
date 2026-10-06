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

**Third-party checkers: run on 5 October 2026.** §1–§10 were written when
none had been visited, and the header used to say so. They have now been
visited, on one host, with one profile, on a direct route — see **§11**, which
records what they found and the two contradictions that came out of it. The
original reasons for not visiting them were real and still constrain what is
published here: a run from this machine sends the operator's real IP and a
real fingerprint to a third party, so **§11 is redacted** — no IP addresses,
no fingerprint IDs, no font hashes. The vectors those sites test are also
measured directly below, which is reproducible and does not depend on someone
else's scoring formula.

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
| CreepJS / Pixelscan / BrowserLeaks scores | **Run 5 October 2026 — see §11.** One host, one profile, direct route, redacted. Iphey still not run |
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
narrows it. Third-party checkers were run on 5 October 2026 and are recorded in **§11**; they found two contradictions this document had not, both now fixed.

---

## 11. Third-party checkers — 5 October 2026, one host, one profile

**Redaction.** This section carries no IP addresses, fingerprint IDs, font
hashes or full vector dumps. The raw captures stay outside the repository.
Both an IPv4 and an IPv6 address appeared in them and neither is reproduced.

**Setup.** Arch Linux, Wayland/Hyprland, system Chromium 151.0.7922.173, one
Multiprofile profile with **no device preset** on a **direct route** (no
proxy), against a clean stock Chromium control with a throwaway profile on
the same host, same engine build, same session.

### 11.1 The two contradictions, and what they were

| Surface | Product profile | Stock control | Verdict |
|---|---|---|---|
| page `userAgentData` | Google Chrome 151, **Windows 11** | Chromium 151, Linux x86_64 | spoof applied |
| **service worker** `userAgentData` | Chromium 151, **Linux** | Chromium 151, Linux x86_64 | **contradiction** |
| `screen.width/height` | **1366 × 768** | 2560 × 1600 | spoof applied |
| **CSS `device-width/height`** | **2560 × 1600** | 2560 × 1600 | **contradiction** |

Both are **fixed**, with regression tests that fail without the fix:

- The worker leak was **not** every worker type. Probing each realm
  separately (`tests/test_worker_realms_live.py`) splits it: dedicated
  workers — blob, URL and module — were already correct, because a dedicated
  worker runs in its owner document's renderer and inherits the page's
  `Network.setUserAgentOverride`. Shared and service workers are their own
  targets in their own processes and inherited nothing. CreepJS reports from
  a `ServiceWorkerGlobalScope` where one is available, which is why it saw
  this and a Blob-worker spot check did not. Fixed by sending the override to
  those two target types.
- The screen contradiction was **every profile without a device preset**.
  `evasion.js` can only define `screen.*` as getters; CSS `device-width` is
  resolved by the compositor and no script can reach it. Fixed with
  `Emulation.setDeviceMetricsOverride` carrying `screenWidth`/`screenHeight`
  and a zeroed viewport — measured on Chromium 151 to move CSS
  `device-height` 1600 → 1440 while leaving `innerWidth`, `innerHeight`,
  `outerWidth` and `outerHeight` byte-identical, which is what makes it safe
  to apply outside the preset path.

### 11.2 Checker headlines, and why none of them is a gate

| Checker | Product | Stock control |
|---|---|---|
| pixelscan.net | "fingerprint is **inconsistent**", "**masking detected**", "no automated behavior detected" | **identical verdict** |
| creepjs.org/checker | 1 inconsistency; **Fonts 51/100** | — |
| CreepJS (abrahamjuliot) | 31% "like headless", 0% headless, **20% stealth** | 38% "like headless", 0% headless, **0% stealth** |

**Pixelscan returned the same verdict for clean stock Chromium**, so that
headline does not discriminate between this product and an unmodified browser
on this host and cannot be read as a pass or a failure of either. This is the
single most important line in this section.

The CreepJS numbers are the useful pair: the product scores *better* on
"like headless" (31% vs 38%) and *worse* on "stealth" (20% vs 0%). That is
**F3's ceiling as a number rather than an argument** — the spoofing is doing
its job and is itself visible. It is one run on one host; it is not a trend.

### 11.3 Fonts — the claim is still unbacked, and the product says so

CreepJS loaded **1 of 51** probe fonts for the product and **6 of 51** for
stock Chromium, so the masking leaves the profile *sparser* than an
unmodified browser, not more ordinary. The launcher already records this
without being asked, as identity faults visible in `/api/health`:

```
font_claim_unbacked   windows fonts with no metric-compatible file on this
                      host, left absent: Calibri, Cambria, Georgia, Verdana,
                      Tahoma, Trebuchet MS, Segoe UI, Consolas, Impact,
                      Microsoft Sans Serif, Palatino Linotype,
                      Franklin Gothic Medium
font_backer_name_visible
                      metric backers remain enumerable under their own names
                      because Chrome reads the font file's name table:
                      Liberation Mono, Liberation Sans, Liberation Serif
```

So the honest statement is the one the fault channel already makes: **a
Windows profile on this host does not have Windows fonts**, aliases resolve
but the files' internal name tables do not change, and twelve expected faces
are absent. Installing metric-compatible files would change the first fault,
not the second.

### 11.5 Open: two font families the profile's own fontconfig will not match

**Still open. Not fixed, and three hypotheses eliminated rather than one
asserted.** Investigated 6 October 2026; the characterisation below is much
sharper than the first version of this section, which only recorded that
stock showed more fonts than the product.

#### What it is

A profile claiming Linux cannot resolve `Liberation Serif` or
`Liberation Mono` by name, although both are installed on the host and both
appear in the profile's own font list. Measured as widths of one string under
each generic — a family that resolves changes the width, a family that does
not falls through to the generic:

| requested | product | stock Chromium | reading |
|---|---|---|---|
| `Liberation Sans` | `957/957/957` | `957/957/957` | resolves in both |
| **`Liberation Serif`** | **`821/957/911`** | `911/911/911` | **unresolved** in the product |
| **`Liberation Mono`** | **`821/957/911`** | `821/821/821` | **unresolved** in the product |
| `Calibri` (genuinely absent) | `821/957/911` | `821/957/911` | unresolved in both |
| generics `mono/sans/serif` | `821/957/911` | `821/957/911` | identical baselines |

`821/957/911` *is* the generic triple, so the two families measure exactly
like `Calibri`, which is not installed at all. The baselines are identical in
both browsers, which removes the obvious explanation — a face whose metrics
match the generic it is tested against.

#### What has been ruled out

**The product.** Stock Chromium reproduces it exactly when pointed at the
profile's own config:

```
stock Chromium, HOST fontconfig:     Serif resolved,   Mono resolved
stock Chromium, PROFILE fontconfig:  Serif UNRESOLVED, Mono UNRESOLVED
```

So nothing else the product does — CDP overrides, the injected script, the
launch flags — is involved. It is the generated `fonts.conf`.

**Chromium.** `fontconfig` fails the same way on its own, so the browser is
only reporting it:

```
$ FONTCONFIG_FILE=<profile>/fonts.conf fc-match 'Liberation Serif'
  -> Liberation Sans          (fallback, not a match)
$ ... fc-match 'Liberation Mono'
  -> Liberation Sans          (fallback, not a match)
```

**"Matching is broken in general."** It is not. Under the same config:

| requested | matched |
|---|---|
| `Liberation Sans` | ✅ |
| `Noto Color Emoji` | ✅ |
| `JetBrainsMono Nerd Font` | ✅ |
| `Noto Naskh Arabic` | ✅ |
| `Liberation Serif` | ❌ |
| `Liberation Mono` | ❌ |

**The plan and the file set.** `plan_for_platform` puts 4 of 4 files for each
of the three Liberation families into the plan, the private directory
contains all twelve, and `fc-list` under the profile config enumerates all
three families with their correct primary names. So the files are there and
fontconfig can *see* them; it will not *match* two of them.

#### Hypotheses tested and rejected

| hypothesis | test | result |
|---|---|---|
| the missing host `conf.d` metric-alias rules weaken exact matching | added `30-metric-aliases.conf` to the config | **no change** |
| a stale or partial per-profile font cache | cleared `<cachedir>` and re-matched | **no change** |
| the `<rejectfont><glob>/usr/share/fonts/*` rules also match the private directory's **symlink targets**, which do point there | removed the whole `rejectfont` block | **no change** |

The third was the most promising — the private entries really are symlinks
into `/usr/share/fonts`, and the config really does reject that glob — which
is exactly why it is recorded as tested rather than left as a plausible
cause.

#### Why it matters, and why it is not a gate

The three Liberation faces ship in one package, so a Linux desktop with one
of them is an unusual machine. The restriction may therefore be making the
**native** claim less ordinary than an unmodified browser, which is the
opposite of its purpose — and native is now the default.

It is a **divergence, not a contradiction**: nothing here is impossible, and
`compare_manifests` classifies it that way, so it does not fail the identity
gate.

#### The next concrete step

`FC_DEBUG=4 fc-match 'Liberation Serif'` under the profile config, to see
which matching stage discards the candidate. Everything above was reached
without it; it is the obvious next instrument.

**Reproduction, without the product at all:**

```bash
FONTCONFIG_FILE=<profile>/fonts.conf fc-match 'Liberation Serif'   # -> Liberation Sans
FONTCONFIG_FILE=<profile>/fonts.conf fc-list  | grep 'Liberation Serif'  # -> present
```

---

### 11.6 Resolved: the page applied the canvas noise twice

**Closed 6 October 2026.** Recorded here as *open* the same day; the entry
below replaces that one and corrects where it pointed.

`tests/test_worker_realms_live.py::test_the_realm_renders_canvas_the_same_way`
asserts that every worker realm hashes a canvas render identically to its
page. It failed about one run in six, with the page disagreeing with all five
workers while the workers agreed with each other.

The previous entry hypothesised a **worker-side** race — a worker given the
identity script twice, or not at all. That was wrong, and the shape of the
data already said so: if the five worker realms agree with each other and only
the page differs, the page is the outlier. What it got right was the final
sentence: *"double application somewhere remains the leading candidate."*

#### The instrument that settled it

A canvas hash tells you two realms differ. It cannot tell you *how*, which is
why eight runs of hashes produced three eliminated hypotheses and no cause.
So the measurement was changed: fill a canvas with a **uniform opaque grey**,
read it back, and report the **mask** — which pixels moved, on which channel,
in which direction. No text is drawn, so nothing in the mask comes from the
engine's font stack.

`pixelHash` is integer arithmetic over canvas position, so the mask can be
**predicted in Python from the profile's seed** and every realm compared
against the prediction rather than against each other. The Python port was
cross-checked against the JavaScript in Node first: for seed 1 at (0,0) both
give `654898832`, and both select 34 of 400 pixels for seed 12345.

A second application nudges the same pixels on the same channels in the same
directions, so against a base of 128 it reads **±2 instead of ±1** — which
makes "applied twice" distinguishable from "wrong seed" and from "not applied"
in one read, where a hash makes them all look alike.

#### What it found, in one line

```
settle=0.6  top  cv=885035865  APPLIED TWICE
```

Bit-exact against the two-pass prediction. Not noise over noise producing
something unpredictable — one clean second application. Confirmed from the
other side by a counter appended to the injected script by the harness:
`runs=2` in one document, 1 of 11 samples at that timing.

#### Two causes, both of them stale comments

**The guard was dead.** `evasion.js` protected against stacking with

```js
const alreadyShielded = Object.prototype.hasOwnProperty.call(origToDataURL, 'toString');
```

— true of a hooked function, false of a native one. That held when it was
written. The **F3 native-masking fix then moved the claimed source into a
WeakMap precisely so that nothing is written onto the hooked function**, which
left this check reading a property that no longer exists, constant false, and
the guard silently disarmed. The comment above it still described the
mechanism that used to make it work.

**The injector sent both ways of applying the identity to one document.** For
a page that was already open it sent
`addScriptToEvaluateOnNewDocument(runImmediately: True)` — which applies the
script to the document open *now* — **and** `Page.reload`, which replaces that
document with one that runs the registered copy from its first line. Whether
both landed in the same document depends on whether the reload commits before
the immediate evaluation is dispatched, which is exactly why it was
intermittent. The comment on the reload still read *"addScriptToEvaluateOnNewDocument
only affects the NEXT document"*, which stopped being true when
`runImmediately` was added beside it.

This is the **same class as F49, F50, `--host-resolver-rules` and F57** — the
behaviour claim true, the mechanism cited fiction — with one difference worth
noting: here the fiction was load-bearing. The guard did not merely describe
itself wrongly; it stopped working, and nothing said so.

#### The fixes

- The two applications are now **exclusive**: `runImmediately` is sent only
  when the page will *not* be reloaded. The reload is preferred where it
  applies, because it runs the page under the identity from document start
  rather than patching a document that has already run site script.
- If the reload **fails**, the script is applied to the live document
  directly, and the status says so. Previously the status appended *"reloaded
  a pre-existing page to apply identity"* when the command was **sent**, so a
  failed reload still reported success — the one place an operator would look
  to find out. It is now recorded when the reload answers.
- `evasion.js` asks the **behaviour** instead of inspecting a property: draw a
  known opaque value where `pixelHash` says a nudge lands, read it back, and
  see whether something already moved it. There is no property left to
  inspect and that is deliberate — a hook built to be indistinguishable from a
  native one cannot find itself by looking. A page could run this same probe,
  so it reveals nothing new.

#### After

24 consecutive samples at the timing that reproduced it: **no** double
application, **no** `runs=2`, every realm bit-exact against the one-pass
prediction. 12 consecutive runs of the realm suite and the new suite pass.

The flake rate was 1 in 6, so 24 clean samples is strong evidence and not
proof; what carries the conclusion is that the mechanism is identified, the
unit tests pin both halves of it, and the live mask test now checks an
**absolute** property rather than agreement between realms.

---

### 11.7 Two more worker canvas gaps, found by the same instrument

Both **deterministic**, both invisible to every probe that existed, because
every probe read from (0,0) and compared realms against each other.

**The worker noised the wrong pixels.** The page keys noise to canvas
*position*; the worker script always keyed it to (0,0). Reading from (0,0) —
which all of them did — hides it completely:

| read | page | worker |
|---|---|---|
| `getImageData(0,0,20,20)` | noise at (0,0) | noise at (0,0) ✅ |
| `getImageData(5,7,20,20)` | noise at (5,7) | noise at **(0,0)** ❌ |

12 of 12 runs, all five worker types. A page and a worker reading the same
rect of the same drawing therefore returned different pixels.

**The worker's PNG export carried no noise at all.** The page hooks
`OffscreenCanvas.convertToBlob`; the worker script hooked only
`getImageData`. Three profiles with different seeds, same drawing:

| profile seed | page `convertToBlob` | worker `convertToBlob` |
|---|---|---|
| 111111 | 378539371 | **3394833805** |
| 999999 | 116989659 | **3394833805** |
| derived | 2291625482 | **3394833805** |

The page's moves with the seed. The worker's is one value for every profile on
the machine: the host's raw render. That is a **stable cross-profile
identifier available to any page that asks a worker to export a canvas**, and
it contradicts the worker's own page in a single comparison — which is worse
than the divergence it was found alongside.

Both fixed in `build_worker_identity_script`. `tests/test_canvas_noise_live.py`
covers both, and was confirmed to fail on the pre-fix code with the diagnosis
in the assertion message rather than a bare hash mismatch.

---

### 11.9 Resolved: a page disagreed with its own iframes, every time

**Closed 6 October 2026.** Not found by a failing test — nothing was failing.
Found by asking the question §11.6 raised: *which other spoofs are additive,
and does anything else apply them twice?* No probe in the suite compared a
page against an iframe on any of these surfaces.

Most spoofs are substitutions and are idempotent. Three are not: audio noise
nudges every sample, rect noise nudges every coordinate, WebGL `readPixels`
nudges every pixel, and each copy of the script keeps its own record of what
it has already nudged.

Two paths reach an iframe — the injector's new-document script in the frame's
own realm, and the parent walking `contentWindow` (`evasion.js` section 8).
Section 8 predates the first; its own comment records the measurement that put
the audio and rect calls there. Both now run:

| surface | page | iframe (srcdoc, about:blank, URL — all three) |
|---|---|---|
| audio | 124.043476408231 | **124.043477541301** |
| rect x | 11.687566983437 | **11.687633966873** |
| rect y | 3.2967806701… | **3.2966863403…** |

**The noise was causing the contradiction it exists to prevent**, on every
load rather than one in six.

After the guard:

| surface | page | iframe (all three) |
|---|---|---|
| audio | 124.043475512859 | 124.043475512859 ✅ |
| rect | 11.687041395616 \| 3.2973366818… | identical ✅ |

That the injected script reaches frame realms is measured, not assumed:
canvas noise is applied only by that script and never by section 8, and it
was already correct inside all three frame kinds. Section 8 is now a fallback
that asks whether the frame already carries the identity and stops if it does.

`tests/test_iframe_additive_live.py` checks the rect **absolutely** as well as
by agreement. The nudge is deterministic in the original coordinate and Blink
has already quantised that coordinate to 1/64 px — two orders coarser than the
5e-4 px nudge — so the pre-noise value is recoverable from the read and both
predictions computable from it. Agreement alone cannot catch two realms being
wrong together, which is precisely what hid §11.7's two defects.

---

### 11.10 The residual canvas disagreement was the engine's, and a control proved it

**Closed 6 October 2026.** After §11.6, §11.7 and §11.9, CI still failed three
canvas assertions in `shared` and `service` only, while they passed locally.

The mask instrument settled it immediately: **every mask check passed in CI,
in all six realms**, so the noise was applied exactly once at the right
coordinates everywhere, and whatever differed lay underneath it. Every
surviving failure drew text; every passing check drew flat grey.

Text metrics localised it to one row — a family that exists nowhere:

| family | page / dedicated worker | shared / service |
|---|---|---|
| `__NoSuchFontAnywhere__` | 170.672 (serif) | 170.625 (sans) |

Chromium's last-resort family is a per-process preference, and an own-process
worker does not get the page's. Every divergent family had exactly those two
values — none resolved, and each process fell back to its own default.

**Stock Chromium, same host, same probe, no profile and no fontconfig
override, gives the identical split.** Engine behaviour, not a product tell.
The suite had been asserting the opposite without saying so, and got away with
it only because `12px serif` resolves on this host and does not in CI.

Two of my own readings were wrong on the way, and both are worth keeping:

- I first read it as the **font restriction leaking past the page** — the F53
  shape. Disproved: under a Windows claim, every rejected Linux family
  measured exactly like the absent control in the page, a dedicated worker, a
  shared worker and a service worker alike.
- I then wrote a test asserting a restricted family is hidden in every realm,
  and **removed it as unsound**. "Hidden" was inferred from matching the
  absent control, and a family that *does* resolve matches that control
  whenever its metrics match the realm's last-resort font — so `Liberation
  Sans`, resolved in an own-process worker whose fallback is sans, was
  reported hidden. Width alone cannot separate the two. Proving the
  restriction holds needs a channel distinguishing *resolved* from *fell
  back*; `measureText` is not one. This sits beside §11.5 as a second thing
  the font surface will not tell us by width probing.

The fix is to the probes. `identity_probe.py` and
`tests/test_worker_realms_live.py` now hash flat shapes — which is what those
assertions are about — and keep the text render as `textHash`, recorded and
not compared. Text metrics are compared separately, only for families that
resolved in both realms.

---

### 11.8 What this section does not establish

- **One host, one profile, one route.** No proxy, no device preset, no second
  distro, no second display system.
- **No account-site evidence.** None of these checkers predicts whether a
  real site accepts the browser.
- **No matched cross-OS reference.** A Windows claim was not compared against
  a genuine Windows browser of the same version; BrowserLeaks recorded a TLS
  fingerprint with no matched reference trace, so no transport mismatch is
  proven either way.

---

## 12. Summary

| Area | Verdict |
|---|---|
| UA / Client Hints / platform | ✅ coherent, wire and JS |
| Timezone / geo / locale | ✅ engine-level, per-exit **and with no proxy** (F43) |
| GPU / WebGL / WebGPU | ✅ mutually consistent |
| Canvas / audio / rects noise | ✅ per-profile, plausible magnitude |
| Canvas noise, applied **once** | ✅ fixed this pass (F60); checked against the seed's predicted mask, not realm agreement |
| Canvas noise, worker surfaces | ✅ fixed this pass (F59); offset reads and `convertToBlob` were both wrong |
| OS-shaped APIs | ✅ battery, voices, bluetooth, media |
| Realm agreement | ✅ all five worker types + iframes; additive surfaces fixed this pass (F61) |
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
