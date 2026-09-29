# 03 — Feature Matrix

**How to read the columns.**

The **Multiprofile** column is verified: every ✅ was checked against the code
in this repository, and most were measured on a running build. Where a feature
half-exists, it says so rather than rounding up.

The **AdsPower / GoLogin / Multilogin** columns are **from public
documentation and general knowledge, not from testing**. No licence for any of
them was purchased, no build was installed, and nothing in those three columns
was verified by this audit. They are here because a matrix without them
answers the wrong question — but a ✅ in a competitor column means "they
advertise it", not "we confirmed it works". Treat any decision that turns on a
competitor cell as needing its own check.

**Priority** is this project's, not the brief's: P0 = ship-stopper, P1 =
needed for credible parity, P2 = differentiator. **Effort** is S (< 1 week),
M (1–3 weeks), L (3–6 weeks), XL (> 6 weeks).

Legend: ✅ works · 🟡 partial, qualified in the notes · ❌ absent · — not applicable

---

## 1. Browser engine

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| Patched Chromium (C++-level spoofing) | ❌ stock Chromium + CDP | ✅ | ✅ | ✅ | **P0** | XL |
| Tracks latest stable ≤ 2 weeks | 🟡 follows the host's Chromium | ✅ | ✅ | ✅ | P1 | M |
| Second engine (Firefox-based) | ❌ | 🟡 | ❌ | ✅ | P2 | XL |
| Per-profile engine version | ❌ one host engine | ✅ | ✅ | ✅ | P1 | L |
| Engine updates independent of app | ❌ distro-managed | ✅ | ✅ | ✅ | P1 | L |

**This is the finding that matters most.** Row 1 is **F3**, and it sets the
ceiling for everything in §2: JS-level hooks are observable through
`Function.prototype.toString`, and no amount of work below the engine closes
that. The three competitors all ship patched engines. A buyer comparing on
detection rate will find the difference.

---

## 2. Fingerprint management

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| UA + Client Hints, fully consistent | ✅ F1, measured on the wire | ✅ | ✅ | ✅ | P0 | — |
| OS emulation (Win/mac/Linux/Android/iOS) | ✅ 10 presets, F42 | ✅ | ✅ | ✅ | P0 | — |
| Canvas noise | ✅ per-seed | ✅ | ✅ | ✅ | P0 | — |
| WebGL vendor/renderer + noise | ✅ | ✅ | ✅ | ✅ | P0 | — |
| WebGL2 | ✅ | ✅ | ✅ | ✅ | P0 | — |
| WebGPU | ✅ F26, tracks the WebGL claim | 🟡 | 🟡 | ✅ | P1 | — |
| AudioContext | ✅ F24 | ✅ | ✅ | ✅ | P0 | — |
| ClientRects / DOMRect noise | ✅ F24 | ✅ | ✅ | ✅ | P0 | — |
| Font list per OS (not just JS) | 🟡 F25 — see below | ✅ | ✅ | ✅ | P1 | M |
| Screen, colour depth, DPR, avail | ✅ | ✅ | ✅ | ✅ | P0 | — |
| hardwareConcurrency, deviceMemory | ✅ | ✅ | ✅ | ✅ | P0 | — |
| Timezone, auto from IP | ✅ F22, F31 — engine-level | ✅ | ✅ | ✅ | P0 | — |
| Geolocation, auto from IP | ✅ F31 | ✅ | ✅ | ✅ | P0 | — |
| Language/locale, auto from IP | ✅ F31 | ✅ | ✅ | ✅ | P0 | — |
| WebRTC: disable / replace / real | ✅ F19 | ✅ | ✅ | ✅ | P0 | — |
| Media devices, realistic labels/ids | ✅ F23 | ✅ | ✅ | ✅ | P1 | — |
| Battery API | ✅ F27 | ✅ | 🟡 | ✅ | P1 | — |
| Speech voices | ✅ F29, per-OS sets | 🟡 | ❌ | ✅ | P2 | — |
| Permissions API | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Plugins / MIME types | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Bluetooth / USB / HID / Gamepad presence | ✅ F28 | 🟡 | 🟡 | ✅ | P2 | — |
| **TLS / JA3 / JA4 alignment** | ❌ **nothing** | ✅ | 🟡 | ✅ | **P0** | L |
| **HTTP/2 fingerprint alignment** | ❌ **nothing** | ✅ | 🟡 | ✅ | **P0** | L |
| Consistency in workers / iframes | ✅ F30, all five realms | ✅ | 🟡 | ✅ | P0 | — |
| Real-device fingerprint database | 🟡 curated presets, unweighted | ✅ | ✅ | ✅ | P1 | L |
| One-click regenerate | ✅ per-profile seed | ✅ | ✅ | ✅ | P1 | — |
| Built-in consistency checker + score | ✅ F32, pre-launch gate | 🟡 | ❌ | ✅ | P2 | — |

**The two ❌ rows are the most important gap after F3.** Nothing in this
product touches the TLS handshake or the HTTP/2 frame ordering, so a profile
claiming Chrome 151 on Windows presents Chromium-on-Linux's JA3/JA4 and
HTTP/2 SETTINGS. Those are checked by every serious anti-bot vendor and cannot
be reached from JavaScript at all — they need a patched network stack or a
proxy that rewrites the handshake. **This is not currently filed as a
finding**, because the audit's findings cover defects in what exists rather
than absences; it is filed here and in [`06_ROADMAP`](06_ROADMAP.md).

**The 🟡 on fonts** is F25's documented residual: metric-compatible backers
(Liberation, Carlito, Caladea) are renamed at scan time so `Arial`,
`Times New Roman` and `Courier New` resolve, but Windows faces with no backer
on this host — Calibri, Cambria, Segoe UI, Tahoma, Verdana and eight more —
are absent rather than faked, and the backers remain enumerable under their
own names because Chrome reads the font file's `name` table. Closing it means
shipping font files, which is a trademark question, not an engineering one.

---

## 3. Profile management

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| Create / clone / duplicate | ✅ | ✅ | ✅ | ✅ | P0 | — |
| Bulk create from template | ✅ F35, one transaction | ✅ | ✅ | ✅ | P0 | — |
| Import from a proxy list | ✅ F34, per-line reporting | ✅ | ✅ | ✅ | P1 | — |
| Export | ✅ F40, whole-account | ✅ | ✅ | ✅ | P1 | — |
| Scale target | 🟡 **150 stored / 20 running** | 10k+ | 10k+ | 10k+ | P2 | M |
| Groups / workspaces | ✅ clients | ✅ | ✅ | ✅ | P1 | — |
| Tags, colour labels, notes | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Custom fields | ❌ | ✅ | 🟡 | ✅ | P2 | S |
| Search, filter, sort | ✅ F9, SQL-side | ✅ | ✅ | ✅ | P0 | — |
| Virtualized list for 10k+ | ❌ **deliberate** — see below | ✅ | ✅ | ✅ | P2 | M |
| Templates / presets | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Cookie import/export (JSON, Netscape) | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Cookie robot / warm-up | ❌ | ✅ | ✅ | ✅ | P2 | M |
| Cloud sync, conflict resolution | ❌ | ✅ | ✅ | ✅ | P2 | XL |
| Versioning / rollback | ❌ | 🟡 | ❌ | ✅ | P2 | L |
| Transfer between users | ❌ no user concept | ✅ | ✅ | ✅ | P2 | L |
| **Trash with restore** | ✅ F36 | ✅ | ✅ | ✅ | P1 | — |
| Per-profile bookmarks / extensions / start URL | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Profile status (running, last opened) | ✅ + measured runtime, F37 | ✅ | ✅ | ✅ | P1 | — |

**The scale row is a decision, not a gap.** The operator set the target at
**150 stored, 20 running**, and virtualization was dropped deliberately: 150
rows of a plain table is not a rendering problem, and building for 10,000
would be work nobody asked for. What was *not* skipped is anything superlinear
at 150 — the quadratic list (F9), the unopenable hundredth profile, the
sequential launch (F33) were all real at this size and all fixed. The
server-side paging and id-resolution built for F9 are size-independent, so
moving the target later is configuration rather than rework.

---

## 4. Proxy and network

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| HTTP / HTTPS / SOCKS4 / SOCKS5 | ✅ | ✅ | ✅ | ✅ | P0 | — |
| Proxy auth | ✅ via CDP `Fetch`, off argv | ✅ | ✅ | ✅ | P0 | — |
| SSH tunnels | ❌ | ✅ | 🟡 | ✅ | P2 | M |
| Bulk proxy import, several formats | ✅ F34, 6 formats | ✅ | ✅ | ✅ | P1 | — |
| Proxy pool | ✅ F18, per-member assignment | ✅ | ✅ | ✅ | P1 | — |
| Rotation / sticky sessions | 🟡 pool assignment is sticky | ✅ | ✅ | ✅ | P2 | M |
| Proxy checker (IP, geo, latency) | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Blacklist status | ❌ | ✅ | 🟡 | ✅ | P2 | M |
| Auto-align TZ/geo/language to exit | ✅ F31 | ✅ | ✅ | ✅ | P0 | — |
| Same alignment with **no proxy** | ✅ **F43** — the host is the exit | ? | ? | ? | P0 | — |
| Fail-closed on unresolvable route | ✅ **F17** | ? | ? | ? | P0 | — |
| Proxy marketplace | ❌ | ✅ | ✅ | ✅ | P2 | L |
| WireGuard / cloud gateways | ✅ Azure, WireGuard, custom | ❌ | ❌ | 🟡 | P2 | — |
| Network namespace isolation | 🟡 experimental, off | ❌ | ❌ | ❌ | P2 | L |
| DNS leak prevention | ✅ SOCKS5h, remote DNS | ✅ | ✅ | ✅ | P0 | — |
| IPv6 handling | 🟡 untested | ✅ | 🟡 | ✅ | P2 | S |

The fail-closed row is worth its own line: **F17** found that a profile whose
route could not be resolved launched on the **host IP** and reported success.
Whether the competitors get this right was not tested — the `?` is honest.
Gateways and netns are the only rows where this product is ahead.

---

## 5. Team and collaboration

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| Workspaces / organisations | 🟡 workspaces, single-user | ✅ | ✅ | ✅ | P2 | L |
| Roles and permissions | ❌ | ✅ | ✅ | ✅ | P2 | L |
| Profile sharing with levels | ❌ | ✅ | ✅ | ✅ | P2 | L |
| Profile locking | ❌ | ✅ | ✅ | ✅ | P2 | M |
| Activity log per user | 🟡 per-profile, no users | ✅ | ✅ | ✅ | P2 | S |
| Member limits per plan | 🟡 declared, unenforced (F38) | ✅ | ✅ | ✅ | P2 | S |

There is **no user concept** anywhere in this product. Every row here is
blocked on identity — see [`08`](08_COMMERCIAL_ARCHITECTURE.md) §2.1. Profile
locking is the one that is a correctness feature rather than a convenience:
two people opening one profile from two IPs is exactly the signature the
product exists to avoid.

---

## 6. Automation and API

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| Local REST API with token auth | ✅ **91 routes**, scoped (F39) | ✅ | ✅ | ✅ | P0 | — |
| Returns CDP/WebSocket endpoint | ✅ | ✅ | ✅ | ✅ | P0 | — |
| Official SDKs (Python, Node) | ❌ | ✅ | ✅ | ✅ | P1 | M |
| Headless mode | 🟡 engine supports it, not exposed | ✅ | ✅ | ✅ | P1 | S |
| **No-code RPA builder** | ❌ | ✅ | ✅ | ✅ | P2 | XL |
| Synchronizer (mirror actions) | ❌ | ✅ | 🟡 | ✅ | P2 | L |
| Scheduler | ❌ | ✅ | ✅ | ✅ | P2 | M |
| Window tiling | ✅ F13 — X11 **and** Wayland | ✅ | 🟡 | ✅ | P1 | — |
| Webhooks / events | 🟡 SSE locally, no webhooks | ✅ | 🟡 | ✅ | P2 | M |
| CLI | ✅ | 🟡 | ❌ | 🟡 | P2 | — |

The API is this product's strongest column and is ahead of where the UI is:
91 routes with scoped tokens, a durable job queue and an event bus. The
missing SDKs are thin wrappers over it — a week each, and the highest
ratio of parity-per-effort on this page.

---

## 7. Extensions, platform, distribution

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| Per-profile extensions | ✅ | ✅ | ✅ | ✅ | P1 | — |
| Install from Web Store / .crx | 🟡 default-off by policy | ✅ | ✅ | ✅ | P2 | S |
| Team extension library | ❌ | ✅ | 🟡 | ✅ | P2 | M |
| Linux (.deb) | ✅ | 🟡 | 🟡 | 🟡 | P0 | — |
| Linux (.rpm / AppImage) | ❌ | ❌ | ❌ | ❌ | P2 | S |
| Windows | ❌ | ✅ | ✅ | ✅ | P2 | XL |
| macOS (Intel + Apple Silicon) | ❌ | ✅ | ✅ | ✅ | P2 | XL |
| **Signed builds** | ❌ **F12** | ✅ | ✅ | ✅ | **P1** | M |
| **Signed delta auto-update** | ❌ **F12** | ✅ | ✅ | ✅ | **P1** | M |
| Offline mode | ✅ fully local | 🟡 | 🟡 | 🟡 | P1 | — |
| Crash reporting (opt-in) | ❌ | ✅ | ✅ | ✅ | P2 | S |
| Telemetry (opt-in) | ✅ **none at all** | 🟡 | 🟡 | 🟡 | P1 | — |

Linux-first is this product's only structural differentiator: the other three
treat Linux as a port. Against that, **F12** is the row that blocks selling to
anyone — an unsigned `.deb` with no update channel is not something a
commercial buyer installs, and `build-deb.sh` cannot be exercised on the
development machine, so packaging changes ship unverified.

---

## 8. Account, billing, compliance

| Feature | Multiprofile | AdsPower | GoLogin | Multilogin | Pri | Effort |
|---|---|---|---|---|---|---|
| Auth, 2FA, device management | ❌ no user concept | ✅ | ✅ | ✅ | P2 | L |
| Subscription plans, trials, invoices | ❌ spec only, `docs/08` | ✅ | ✅ | ✅ | P2 | L |
| Payments (cards, UPI) | ❌ | ✅ | ✅ | ✅ | P2 | L |
| Admin dashboard | ❌ | ✅ | ✅ | ✅ | P2 | M |
| **Usage metering** | ✅ **F37**, runtime per profile | ✅ | 🟡 | ✅ | P2 | — |
| Plan limits, enforced | ✅ F38, marked enforced/declared | ✅ | ✅ | ✅ | P2 | — |
| **Scoped, revocable API tokens** | ✅ F39 | 🟡 | 🟡 | ✅ | P1 | — |
| **Data export / deletion** | ✅ F40 | 🟡 | 🟡 | ✅ | P1 | — |
| Terms / AUP | 🟡 engineering draft only | ✅ | ✅ | ✅ | P2 | S |
| KYC / abuse detection | ❌ nothing to detect from | 🟡 | 🟡 | ✅ | P2 | L |
| E2E encryption for synced data | — no sync | 🟡 | 🟡 | ✅ | P2 | XL |
| GDPR / DPDP readiness | ✅ F40 + `docs/07` | ✅ | ✅ | ✅ | P1 | — |

---

## 9. Where this leaves the product

**Ahead:** Linux-first packaging, no telemetry whatsoever, cloud gateway and
netns routing, a fail-closed router verified by test, scoped API tokens, a
pre-launch consistency score, and an honest usage meter. The API surface is
broader than the UI that sits on it.

**At parity:** the fingerprint vectors reachable from JavaScript — which, after
P1, is most of §2 — plus profile management, proxy handling and trash.

**Behind, in the order it matters:**

1. **The engine (F3).** Stock Chromium caps everything else. Three competitors ship patched builds.
2. **TLS/JA3 and HTTP/2.** Entirely absent, checked by every serious anti-bot vendor, unreachable from JavaScript.
3. **Distribution (F12).** Unsigned, no updates, no Windows or macOS.
4. **Teams, sync, RPA.** Real product surface, all blocked on there being no user concept.

The first two are the same kind of problem — they live below the layer this
product currently operates at — and together they are what "detectable" means
in practice. Everything in §5 and §6 is ordinary product work that could be
scheduled tomorrow; those two cannot.

---

*Sources:* the Multiprofile column is the code in this repository, with
measurements in [`04_FINGERPRINT_TEST_RESULTS`](04_FINGERPRINT_TEST_RESULTS.md)
and findings in [`02_AUDIT_REPORT`](02_AUDIT_REPORT.md). Competitor columns are
unverified public information, as stated at the top.
