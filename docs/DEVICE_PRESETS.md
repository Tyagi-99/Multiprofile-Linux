# Multiprofile Linux — Device Presets

Version: 1.6.0  
Status: Honest Emulation Identities (measured, not claimed)

---

## 1. What device presets are

A device preset is a **coherent identity bundle** for one real-world device: User-Agent
string, Client Hints (platform, mobile, model, brand versions), viewport/screen
geometry, DPR, touch capability, and an explicit capability matrix describing
what a Linux Chromium host can and cannot reproduce about that device.

Creating a profile with `deviceId` (e.g. `pixel-9`) pins the profile's identity
components to the preset. The launcher applies the geometry, touch, and UA
overrides to the live browser through CDP.

## 2. The preset catalogue (33 presets)

All 33 presets, as declared in `src/multiprofile/devices/`:

| Preset ID | Category | Platform | Viewport@DPR | UA (truncated) |
|---|---|---|---|---|
| `pixel-9` | MOBILE | Android | 411x923@2.626 | `Mozilla/5.0 (Linux; Android 14; Pixel 9)…` |
| `pixel-9-pro` | MOBILE | Android | 336x748@3.0 | `Mozilla/5.0 (Linux; Android 14; Pixel 9 Pro)…` |
| `pixel-9-pro-xl` | MOBILE | Android | 448x997@3.0 | `Mozilla/5.0 (Linux; Android 14; Pixel 9 Pro XL)…` |
| `pixel-8` | MOBILE | Android | 411x914@2.626 | `Mozilla/5.0 (Linux; Android 14; Pixel 8)…` |
| `pixel-8-pro` | MOBILE | Android | 384x855@3.5 | `Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro)…` |
| `pixel-7` | MOBILE | Android | 411x914@2.626 | `Mozilla/5.0 (Linux; Android 14; Pixel 7)…` |
| `pixel-7-pro` | MOBILE | Android | 411x891@3.502 | `Mozilla/5.0 (Linux; Android 14; Pixel 7 Pro)…` |
| `galaxy-s24` | MOBILE | Android | 360x780@3.0 | `Mozilla/5.0 (Linux; Android 14; SM-S921B)…` |
| `galaxy-s24-ultra` | MOBILE | Android | 411x891@3.502 | `Mozilla/5.0 (Linux; Android 14; SM-S928B)…` |
| `galaxy-s23` | MOBILE | Android | 360x780@3.0 | `Mozilla/5.0 (Linux; Android 13; SM-S911B)…` |
| `galaxy-s23-ultra` | MOBILE | Android | 411x882@3.502 | `Mozilla/5.0 (Linux; Android 13; SM-S918B)…` |
| `galaxy-a55` | MOBILE | Android | 411x891@2.626 | `Mozilla/5.0 (Linux; Android 14; SM-A556B)…` |
| `galaxy-a35` | MOBILE | Android | 411x891@2.626 | `Mozilla/5.0 (Linux; Android 14; SM-A356B)…` |
| `oneplus-12` | MOBILE | Android | 412x906@3.497 | `Mozilla/5.0 (Linux; Android 14; CPH2581)…` |
| `oneplus-12r` | MOBILE | Android | 389x855@3.251 | `Mozilla/5.0 (Linux; Android 14; CPH2609)…` |
| `oneplus-11` | MOBILE | Android | 411x918@3.503 | `Mozilla/5.0 (Linux; Android 13; CPH2449)…` |
| `xiaomi-14` | MOBILE | Android | 436x971@2.75 | `Mozilla/5.0 (Linux; Android 14; 23127PN0CG)…` |
| `xiaomi-14-ultra` | MOBILE | Android | 411x914@3.502 | `Mozilla/5.0 (Linux; Android 14; 24030PN60G)…` |
| `redmi-note-13-pro` | MOBILE | Android | 444x986@2.75 | `Mozilla/5.0 (Linux; Android 13; 2312DRA50G)…` |
| `nothing-phone-2` | MOBILE | Android | 411x918@2.627 | `Mozilla/5.0 (Linux; Android 13; A065)…` |
| `iphone-16` | MOBILE | iOS-class | 393x852@3.0 | `Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac…` |
| `iphone-16-plus` | MOBILE | iOS-class | 430x932@3.0 | `Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac…` |
| `iphone-16-pro` | MOBILE | iOS-class | 402x874@3.0 | `Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac…` |
| `iphone-16-pro-max` | MOBILE | iOS-class | 440x956@3.0 | `Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac…` |
| `iphone-15` | MOBILE | iOS-class | 393x852@3.0 | `Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac…` |
| `iphone-15-pro` | MOBILE | iOS-class | 393x852@3.0 | `Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac…` |
| `iphone-15-pro-max` | MOBILE | iOS-class | 430x932@3.0 | `Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac…` |
| `ipad-pro-11` | TABLET | iPadOS-class | 834x1212@2.0 | `Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)…` |
| `ipad-air` | TABLET | iPadOS-class | 820x1180@2.0 | `Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)…` |
| `galaxy-tab-s9` | TABLET | Android | 800x1280@2.0 | `Mozilla/5.0 (Linux; Android 13; SM-X710)…` |
| `win11-chrome` | DESKTOP | Windows | 1920x1080@1.0 | `Mozilla/5.0 (Windows NT 10.0; Win64; x64)…` |
| `ubuntu-chrome` | DESKTOP | Linux | 1920x1080@1.0 | `Mozilla/5.0 (X11; Linux x86_64)…` |
| `macos-chrome` | DESKTOP | macOS-class | 1440x900@2.0 | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)…` |

## 3. Validation method (what "VALIDATED / PARTIAL / PENDING" means)

Every preset is validated by `DevicePresetValidator.validate()` (`src/multiprofile/devices/validator.py`):

- **Static checks** (always run): schema, UA format, Client-Hints coherence,
  viewport sanity, mobile/touch flags matching category, DPR bounds, screen ==
  viewport × DPR (±1 px), browser compatibility, fingerprint cross-consistency.
- **Runtime check** (needs a real Chromium binary on the host): a genuine CDP
  round-trip — `Emulation.setDeviceMetricsOverride` + touch + UA are applied
  through DevTools, then `Runtime.evaluate` reads back the *page's* geometry.
  Applied `411x923` must read back `411x923`. This is a real measurement, not
  a flag assertion.
- **Capability-matrix cap**: even with every check passing, a preset whose
  declared capability matrix contains `PARTIAL`/`UNAVAILABLE` entries (things a
  Linux host genuinely cannot reproduce) is capped at **PARTIAL**. Without a
  real binary the runtime check reports `NOT_RUN` and the preset stays
  **PENDING**.

Status meanings:

| Status | Meaning |
|---|---|
| `VALIDATED` | Every check passed **and** every declared capability is `SUPPORTED`. |
| `PARTIAL` | Checks pass, but one or more capabilities are honestly declared `PARTIAL` (host limitations, see §5). |
| `PENDING` | Runtime check never ran (no real Chromium binary on this host). |
| `UNSUPPORTED` | A check failed. |

## 4. Measured validation matrix

Measured **2026-09-23** with the real host binary `/opt/meta-chromium/chrome`
(Chromium 152.0.7977.82):

- **VALIDATED: 2** — `win11-chrome`, `ubuntu-chrome`
- **PARTIAL: 31** — all Android, iOS-class, iPadOS-class presets and
  `macos-chrome` (every static + runtime check passed; capped by declared
  capability limitations, not by failures)
- **PENDING: 0**
- **UNSUPPORTED: 0**

Examples of genuine runtime evidence (from `binary_evidence`):
- `pixel-9`: "real CDP round-trip via /opt/meta-chromium/chrome: applied
  411x923@2.626, page reports 411x923"
- `iphone-16`: "real CDP round-trip … applied 393x852@3.0, page reports 393x852"
- `macos-chrome`: "real CDP round-trip … applied 1440x900@2.0, page reports 1440x900"

Query live status any time with `GET /api/devices/<id>/validate` or list all
presets with `GET /api/devices`.

## 5. Honest capability limitations

These are declared per-preset in `capability_matrix` and are the reason 31
presets are `PARTIAL` rather than `VALIDATED`:

- **WebGL**: the renderer string is the **real host renderer** (ANGLE on the
  host GPU / SwiftShader). A Linux host cannot reproduce an Adreno, Apple
  Silicon, or Windows DirectX GPU string.
- **Fonts**: San Francisco (iOS), Segoe UI (Windows), and other platform font
  stacks are not present on Linux; fallbacks approximate metrics but do not
  match.
- **Media**: iOS WebKit media behaviours (inline video policies, HEVC handling)
  and platform codec sets are not reproducible in Chromium on Linux.
- **Sensors**: orientation/motion data is emulated via CDP, not real hardware.
- **Engine (iOS-class / iPadOS-class)**: iOS/iPadOS presets are **iOS-class
  identities running Chromium, not WebKit**. The UA, Client Hints, viewport,
  and touch are applied; the engine remains Chromium.
- **macOS-class**: cannot reproduce macOS GPU/media/font characteristics on
  Linux (GPU strings, CoreText rendering, media stack).
- **Canvas**: canvas behaviour is the real Chromium canvas; see
  `docs/FINGERPRINT_MODEL.md`.
- **Battery**: the Battery Status API reflects the host machine, not the
  emulated device.

## 6. No anti-detection claims

Device presets produce **coherent, declared identities** — UA, Client Hints,
viewport, DPR, and touch all agree with each other. They are **not** an
anti-detection system, and this document makes no undetectability claims.
Detection-oriented fingerprinting looks at far more signals than an identity
bundle can control (see `docs/FINGERPRINT_MODEL.md` §5, limitations).

## 7. Launch-time emulation: how the overrides actually persist

When a profile with `deviceId` is launched, the launcher applies the
preset as one CDP plan (`Emulation.setDeviceMetricsOverride`,
`Emulation.setTouchEmulationEnabled`, `Network.setUserAgentOverride` with
client-hints metadata) and then **holds the CDP session open for the
browser's lifetime** (a daemon thread closes it when the browser process
exits). This is not an implementation detail — it is required by Chromium:

- **Session-scoped overrides.** `Network.setUserAgentOverride` and
  `Emulation.setTouchEmulationEnabled` belong to the DevTools *session*
  that set them. Verified empirically against real Chromium: the override
  is ACKed and visible while the session is open (even from a second
  concurrent session), but the moment the setting session disconnects,
  page readback reverts to host values (`maxTouchPoints` → 0, UA → host).
  A one-shot "connect, send, close" helper can therefore never make
  UA/touch emulation persist.
- **Target-persistent override.** `Emulation.setDeviceMetricsOverride`
  (viewport/DPR/mobile) survives a disconnect — it is the only plan step
  that does.
- **Belt and braces for UA.** The preset UA is additionally passed via the
  `--user-agent=` launch flag (unless the profile sets an explicit UA),
  which is persistent for both HTTP headers and `navigator.userAgent`.

Measured end state after launch (Pixel 9, real headed Chromium, read back
on a *fresh* CDP connection after `launch_profile` returned):
`innerWidth/innerHeight = 411x923`, `navigator.maxTouchPoints = 1`,
`navigator.userAgent` = the preset's Android/Pixel 9 string. The held
session is an internal handle, not a user-facing API; stopping the profile
closes it.

## 8. REST API

- `GET /api/devices` — list all presets (summary).
- `GET /api/devices/<id>` — full preset incl. capability matrix.
- `GET /api/devices/<id>/validate` — run the validator now (honest status +
  per-check results + runtime evidence).
- `POST /api/profiles` with `{"deviceId": "<id>"}` — create a profile pinned
  to a preset. Unknown IDs are rejected with a clear 400 (`unknown deviceId`).
