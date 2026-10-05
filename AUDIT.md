# Multiprofile Linux — Commercial Readiness Audit

**Scope:** Phase 0 recon, read-only. No source file was modified.
**Commit audited:** `a79a5ca` (main, matches `origin/main`)
**Date:** 24 September 2026
**Machine:** Ubuntu, kernel 7.0.0-28-generic, x86_64

---

## Executive summary

The engine is better than it looks and the packaging is worse. The launch
pipeline, proxy fail-closed logic and fingerprint consistency engine are
carefully built, honestly documented, and covered by 989 passing tests. A
clean clone builds and tests green on the first try.

Two things would stop a sale today. First, the browser extension publishes
each profile's proxy password and cookie jar to **every website that profile
visits** — a privacy product leaking the credential it exists to protect.
Second, the local API trusts anything on the machine and validates nothing
about who is calling, so any web page the operator opens can create, delete
and launch profiles behind their back. Both are fixable in days, not months.

Beyond that, the commercial layer does not exist yet: no licence file (the
repository is legally unusable by anyone), no CI, no code signing, no
auto-update, no billing, no entitlements. Those are greenfield, not repairs.

**Verdict: strong prototype, not a sellable product. Roughly 2–3 weeks to
close Critical and High findings; Phases 3–7 as written are multi-quarter.**

---

## Stack — what this actually is

The brief assumes Electron. **It is not Electron.** Nothing in this repository
is Electron, Node-in-the-renderer, or a Chromium fork. Several Phase 1
mandates therefore do not apply as written, and I have restated them.

| Layer | Reality | Evidence |
|---|---|---|
| Backend | Python + Flask, single process | `src/multiprofile/app.py:21` |
| Desktop shell | pywebview 6.2.1 (GTK/WebKit), **no JS↔Python bridge** | `src/multiprofile/app.py:66` — `create_window(title, url, …)`, no `js_api` |
| Frontend | Next.js 15.5.26 static export, React 19.3.0, Tailwind 4.3.3, TS 5.9.3 | `frontend/package.json`, verified in `node_modules` |
| Browser | **Stock Google Chrome**, driven over CDP. No fork, no patches | `src/multiprofile/browser/launcher.py:531` |
| Injection | MV3 content script, `world: "MAIN"` | `src/extension/manifest.json` |
| Runtime (tests) | CPython 3.12.13 (uv venv) | `.venv/bin/python` |
| Runtime (shipped) | System `python3` **3.14.4** | `packaging/debian/control` |
| Packaging | Hand-rolled `build-deb.sh` → `.deb`. Linux only | `build-deb.sh:53` |

**Process model:** one Flask process on `127.0.0.1:8765`, N child Chrome
processes, each with its own `--user-data-dir` and CDP port. There is no IPC
surface in the Electron sense — **the HTTP API is the entire attack surface.**
That is architecturally cleaner than a wide IPC bridge, and it makes finding
C2 below the whole ballgame.

**Windows and macOS do not exist yet.** There is no packaging, no
platform abstraction, and the window tiling depends on X11/XWayland
(`--ozone-platform=x11`) and `_NET_WORKAREA`. Treat cross-platform as new
development, not a port.

---

## Findings

Severity: **C**ritical (ship-stopper) / **H**igh / **M**edium / **L**ow.
Effort is one engineer, including tests.

| ID | Sev | Area | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| **C1** | Critical | Credential exposure | `src/extension/manifest.json:44-54`; `src/multiprofile/browser/launcher.py:300-310` | `proxy.json` (cleartext proxy **username + password**) and `cookies.json` (the profile's full cookie jar) are declared `web_accessible_resources` for `<all_urls>` with `use_dynamic_url: false`. Any visited site that learns the extension ID can `fetch()` both. Verified on disk across 8 profile extension directories. | 3–6h |
| **C2** | Critical | API auth / CSRF | `src/multiprofile/api/auth.py:57,90`; `src/multiprofile/api/validation.py:86`; 15 sites in `src/multiprofile/api/server.py` | Localhost is fully unauthenticated by design, **and** every write route parses the body with `get_json(force=True)`, ignoring `Content-Type`. No `Origin`, `Referer` or `Host` validation. A `text/plain` POST is a CORS *simple request* — no preflight — so any web page the operator opens can create/delete/launch profiles. With DNS rebinding (no Host check) it becomes full read of cookies and proxy credentials. **Proven below.** | 6–10h |
| **H1** | High | File permissions | `src/multiprofile/storage/profiles.py:120-124` | `profiles.json` is written with **no `chmod`** → `0664` on this machine, world-readable. The profile schema carries `proxyPass` (`profiles.py:79`). `gateways.py:65` and `pools.py:75` *do* chmod 0600 — this one was missed. | 1h |
| **H2** | High | Encryption at rest | `~/.local/share/multiprofile/gateways.json` | **17 of 17 gateways hold a cleartext password.** No encryption anywhere, no OS keychain (libsecret/DPAPI/Keychain). Phase 1 mandates this; it is currently absent, not partial. | 16–24h |
| **H3** | High | Dependency CVE | `npm audit` | `postcss ≤8.5.22` — 4 advisories incl. path traversal and arbitrary `.map` disclosure; `next` moderate. Both **build-time only** (static export ships no Node), which lowers real-world risk but not diligence risk. Fix needs Next 16 (semver-major). | 4–8h |
| **H4** | High | No CI / no quality gate | no `.github/`; `pyproject.toml` | Zero CI. No Python linter or type checker configured. `ruff` reports **415 findings**, including **106 blind `except:`** and **37 `try/except/pass`** — the shape that silently swallows the failures this product must surface. | 8–12h |
| **H5** | High | Repo hygiene | `git ls-files \| grep .deb` | **4 `.deb` binaries (2.7 MB) are committed**, despite `.gitignore` listing `dist/` and `*.deb` — they predate the rule. They reappear in every clean clone. | 1h (+ history rewrite if they must go) |
| **M1** | Med | False capability claim | `src/extension/manifest.json:4`; `src/multiprofile/fingerprint/audio.py:1-10` | Manifest advertises *"Protects against Canvas, **Audio**, WebGL…"*. `audio.py` honestly states no per-profile audio override is possible on Linux Chromium, and `evasion.js` contains **zero** audio hooks (no `OfflineAudioContext`, `createOscillator`, `getChannelData`). The code is honest; the marketing string is not. | 2h |
| **M2** | Med | Fingerprint gaps | `src/extension/evasion.js` | Not injected at all: **ClientRects, Battery, Geolocation, speechSynthesis, doNotTrack, port-scan/localhost blocking**. Confirmed by grep — 0 occurrences each. | see matrix |
| **M3** | Med | Runtime divergence | `.venv` vs `packaging/debian/control` | Tests run on **3.12.13**; the shipped `.deb` runs on system **3.14.4**. Nothing is tested on the version customers execute. | 2h (CI matrix) |
| **M4** | Med | **Legal** | repo root | **No `LICENSE` file.** README and `pyproject.toml` imply MIT; neither declares it. As published, the work is *all rights reserved* — no one may legally use, fork or redistribute it, and a buyer's counsel will flag this immediately. | 15 min |
| **M5** | Med | God modules | `api/server.py` (1295), `network.py` (884), `launcher.py` (838), `wizard-dialog.tsx` (841), `profiles-page.tsx` (818) | Nine files exceed 500 lines. `server.py` holds every route in one function body. | Phase 2 |
| **M6** | Med | Untested entry path | coverage report | `app.py` **0%**, `__main__.py` **0%**, `logging_setup.py` **0%**. The desktop launch path — the first thing a user touches — has no test. | 6h |
| **M7** | Med | Stale dev env | `.venv` | Installed `multiprofile-native==1.5.0` against 1.9.0 source. Tests pass via `PYTHONPATH`, masking the drift. | 15 min |
| **L1** | Low | Permissions | `audit.jsonl` 0664, `control.db` 0644 | Audit trail and control plane world-readable. | 1h |
| **L2** | Low | Observability | `logging_setup.py` | No structured logging, no levels policy, no rotation, no crash reporting. | Phase 2 |
| **L3** | Low | Coupling | 72 in-function relative imports | Deferred imports used to dodge cycles. Package imports cleanly today, but the smell is real. | Phase 2 |
| **L4** | Low | Positioning residue | `~/.local/share/multiprofile/wineprefix/` | Leftover Wine prefix in operator data while the package description says *"Does not require Wine."* Cosmetic, but it is in the demo path. | 15 min |

### C2 proof (ran against the real app factory)

```
POST text/plain + hostile Origin -> 200
  created: CSRF-created
  CORS headers on response: none
GET with foreign Host header -> 200 (200 = no Host validation)
DELETE with hostile Origin -> 404 (404 = reached the handler, not blocked)
```

A `404` on the DELETE is the point: the request **reached the handler**. With a
real profile ID it would have deleted it.

### What is genuinely well built

Credit where due — these are not common in a prototype:

- **Proxy credentials never touch the Chrome command line**, and
  `ProxyConfig.masked_str()` (`network.py:82`) keeps them out of logs. Verified:
  no log statement can emit a password. The C1 leak is a manifest mistake, not
  a careless codebase.
- **Fail-closed proxy policy** with `ProxyFallback.BLOCK` as the default, plus
  the `blocked_reason` path added in Phase D. A dead or unresolvable route
  refuses to launch rather than exposing the host IP.
- **The consistency engine** (`consistency.py`, 512 lines, 90% covered) checks
  IP/timezone/language coherence, device-preset vs viewport/DPR/touch,
  OS/UA/Client-Hints agreement and mobile coherence, with human-readable
  reasons. Phase 3.2's headline differentiator largely **exists**.
- **Honest PARTIAL reporting** in `audio.py` and `battery.py` — the code
  refuses to claim control it does not have. Keep this culture.
- `auth.py`'s header documents its own weaknesses without spin. Rare.

---

## Verification — commands actually run

**Clean clone → documented steps → green.** This is a real strength.

```
$ git clone https://github.com/Tyagi-99/Multiprofile-Linux.git repo
$ uv venv && uv pip install -e ".[all]"
 + multiprofile-native==1.9.0 … pywebview==6.2.1 … flask==3.1.3
$ uv run pytest tests/ -q
989 passed, 2 skipped in 67.01s
$ bash build-deb.sh
Built: dist/multiprofile-native_1.9.0_all.deb       # exit 0
```

**Coverage — 72% overall (7980 statements, 2198 uncovered).**

| Module | Cov | | Module | Cov |
|---|---|---|---|---|
| `browser/window_reaper.py` | 96% | | `api/server.py` | **60%** |
| `storage/pools.py` | 94% | | `jobs/legacy.py` | **44%** |
| `storage/db.py` | 93% | | `browser/health.py` | **31%** |
| `consistency.py` | 90% | | `browser/window_layout.py` | **29%** |
| `browser/grid.py` | 90% | | `proxy_util.py` | **26%** |
| `network.py` | 80% | | `storage/templates.py` | **25%** |
| `browser/launcher.py` | 74% | | `app.py` / `__main__.py` | **0%** |

**Dependency audits.**

```
$ uvx pip-audit -r requirements.txt          → No known vulnerabilities found
$ uvx pip-audit -r <full venv freeze>        → No known vulnerabilities found
$ npm audit  → {moderate: 1, high: 1, critical: 0}   # postcss, next (build-time)
```

**Lint.** `uvx ruff check src/ tests/` → **415 errors**, 212 auto-fixable.
Top rules: `BLE001` blind-except ×106, `I001` unsorted-imports ×61,
`S110` try-except-pass ×37, `F401` unused-import ×32.
Frontend `npm run typecheck` and `npm run lint` are **clean**.

---

## Feature gap analysis vs the Phase 3 matrix

**Have** = works today · **Partial** = exists but incomplete or unenforced · **Missing** = not present

### 3.1 Profile management
| | | | |
|---|---|---|---|
| Create / duplicate / bulk-create | **Partial** (no CSV) | Folders / tags / colors / starring | **Partial** (tags+colors; no folders/star) |
| Search + multi-filter | **Have** | Bulk actions | **Have** (launch/stop/park/delete) |
| Templates | **Have** (`storage/templates.py`, 25% cov) | Import/export encrypted bundle | **Partial** (`backup.py` AES-GCM; not profile-scoped) |
| Notes + custom fields | **Partial** (notes log shipped; no custom fields) | Status indicators | **Have** |
| Trash + restore | **Missing** | Profile transfer | **Missing** |
| Quick/disposable profile | **Missing** | | |

### 3.2 Fingerprint configuration
**Have (injected & verified):** User-Agent · platform/OS · hardware concurrency ·
device memory · timezone (`Intl.DateTimeFormat`) · language/locale ·
WebRTC (18 hooks) · Canvas (`toDataURL`/`toBlob`/`getImageData`/`measureText`) ·
WebGL image + metadata (`getParameter`) · font list · media devices ·
`navigator.webdriver` · screen/DPR · plugins/mimeTypes · touch points.

**Partial:** Audio — config generated, honestly marked PARTIAL, **not injected**
(M1). Geolocation — no injection.

**Missing:** ClientRects noise · Battery API · DNT flag · port-scan /
localhost-access blocking · speech-synthesis voice mask.

**Differentiators:** random coherent generator — **Have** (seed-derived,
`fingerprint/_core.py`). Consistency validation engine — **Have** and good.
One-click test links (browserleaks/pixelscan/creepjs/iphey/whoer) — **Missing**,
and this is cheap, high-visibility demo value.

### 3.3 Proxy & network
HTTP/HTTPS/SOCKS5 **Have** · SOCKS4 **Missing** · SSH tunnel **Missing** ·
proxy manager as first-class entity **Have** (gateways + pools) ·
bulk import `ip:port:user:pass` **Partial** (`proxy_util.py`, 26% cov) ·
health check latency/exit-IP **Have**, country/ISP/blacklist **Missing** ·
one proxy → many profiles **Have** (pools, sticky) · rotating proxy **Have** ·
vendor integrations (Bright Data / Oxylabs / IPRoyal / Smartproxy) **Missing** ·
auto timezone-from-IP **Partial** (validated, not auto-applied) ·
per-profile DNS **Partial** (`DNSPolicy` exists) · bandwidth tracking **Missing** ·
backup proxy / failover **Missing** · proxy-down UI state **Have**.

### 3.4 Browser cores
Chromium core **Partial** — uses whatever stock Chrome is installed; no
side-by-side versions, no pinning, no silent updates. Firefox core **Missing**.
Mobile emulation **Partial** (`fingerprint/mobile.py`, device presets).
Extension management **Missing**.

### 3.5 Automation & integration
Local REST API **Have** (and returns CDP port) · cloud API + keys + rate limits
**Missing** · Puppeteer/Playwright/Selenium examples **Missing** ·
SDKs **Missing** · no-code RPA builder **Missing** · task templates **Missing** ·
cookie warmup robot **Missing** · window synchronizer **Missing** (tiling
**Have**) · webhooks **Missing** · headless mode **Partial** (`--server-only`).

### 3.6 Team & collaboration
Workspaces **Partial** (grouping only, shipped Phase D) · roles/permissions
**Missing** · profile-level sharing **Missing** · access without password
exposure **Missing** · ownership transfer **Missing** · audit log **Have**
(`audit.jsonl`) · session locking **Missing** · team proxy pools **Have** ·
seat management **Missing** · 2FA policy **Missing**.
**Note:** there is no user model at all — auth is one shared static token.

### 3.7 Data, sync & storage
Cloud sync **Missing** · local-only mode **Have** (it is the only mode) ·
cookie import/export JSON **Have**, Netscape **Missing** · full session
persistence **Have** (real `--user-data-dir`) · password manager **Missing** ·
bookmarks/history **Missing** · conflict resolution **Missing** ·
backup **Have** (`backup.py`), point-in-time restore **Partial**.

### 3.8 Application UX
1000+ rows **Missing** — cursor pagination at 25/page, no virtualization,
`profiles.json` is a single file read fully into memory on every call
(`storage/profiles.py:127`). This will not hold at 1000 profiles; needs the
Phase 2 SQLite move. Bulk selection **Partial** (checkboxes, no shift-range) ·
tiling manager **Have** (genuinely good) · command palette **Have** ·
resource monitor **Have** (`metrics.py`) · multi-language UI **Missing**
(English only — and EN-only is a real revenue ceiling in this category).

---

## Ranked remediation plan

**Gate 1 — stop the bleeding (2–4 days).** Nothing else matters until these land.
1. **C1** — drop `cookies.json`/`proxy.json` from `web_accessible_resources`;
   move proxy auth into the service worker's own storage. Add a test asserting
   no credential-bearing file is web-accessible.
2. **C2** — require `Content-Type: application/json` (kills the simple-request
   CSRF), validate `Host` against a loopback allowlist (kills DNS rebinding),
   reject cross-origin `Origin` headers. Optionally a per-session token in the
   served HTML.
3. **H1** — `chmod 0600` on `profiles.json`; sweep every writer for the same.
4. **M4** — add the `LICENSE` file. Fifteen minutes, unblocks any conversation
   with a buyer.

**Gate 2 — make it defensible (1–2 weeks).**
5. **H2** keychain-backed encryption at rest · **H4** CI with lint+type+test
   gates · **H3** dependency upgrade · **H5** purge committed binaries ·
   **M3** test on the shipped Python · **M1** correct the audio claim.

**Gate 3 — then, and only then, Phases 2–3.**

---

## What would make an investor walk

Blunt, in the order they would find them.

1. **A privacy product that leaks the credential it protects (C1).** The
   moment a technical diligence reviewer opens `manifest.json` next to
   `launcher.py:308`, the conversation is about competence, not features. This
   is the single highest-value hour of work in the repository.
2. **No LICENSE (M4).** Counsel finds this in minutes. Right now nobody —
   including a buyer — has any legal right to the code.
3. **Any website can drive the app (C2).** Demonstrable live in a browser
   console. It reframes the product as unsafe to install.
4. **No CI, no signing, no auto-update, no billing, no entitlements.** The
   entire commercial layer of Phases 4 and 6 is greenfield. "Ship it and see"
   is not a distribution plan for a paid desktop app.
5. **Linux only.** The brief targets three platforms. Two do not exist, and
   the tiling engine is X11-bound. Say "Linux-first", not "cross-platform".
6. **No compliance posture in a category payment processors actively drop.**
   There is no acceptable-use policy, no abuse mechanism, no threat model. For
   anti-detect specifically, Stripe risk is a real, common failure mode.
7. **"1000+ profiles" is currently untrue.** `profiles.json` is read whole on
   every request. Do not demo it at scale until Phase 2 lands.

---

## Three things I would push back on

**1. Your two briefs contradict each other, and you must pick.**
The existing director brief states this is a clean-room program that *"does not
ship … stealth patches that fight a website's security"* and that *"Evasion is
out of scope."* Meanwhile the repository ships `src/extension/evasion.js`
(49 KB) under the manifest name **"Anti-Detect Fingerprint Defender"**, and
Phase 3.2 asks for canvas noise, WebGL noise and `navigator.webdriver` masking —
which is evasion by any reading. These cannot both be the public position.
This is not pedantry: it determines your marketing copy, your Phase 7
threat model, and how a payment processor classifies you. Decide first, then
I will make the code and the claims agree.

**2. Phase 3 is not one phase.** Firefox core alone is a multi-quarter project
(Multilogin's moat is expensive for a reason). A no-code RPA builder is its own
product. Side-by-side Chromium versions means hosting and updating browser
binaries. I would cut Phase 3 to what differentiates you — consistency engine,
proxy pools, test-link integrations, mobile emulation — and defer Firefox,
RPA and cloud sync until there is revenue justifying them.

**3. Do not build the commercial layer on the current data layer.** Phase 4
wants entitlements and seat management while profiles live in a single
world-readable JSON file loaded in full on every request. Phase 2's SQLite
migration should come *before* Phase 4, not after, or the billing code will be
written twice.

---

## Resolution status — after Phases 1 and 1.5

| ID | State | Where |
|---|---|---|
| **C1** credential exposure | **Fixed** | `f19c4ff` — `web_accessible_resources` empty; regression test |
| **C2** CSRF / DNS rebinding | **Fixed** | `3a3a93e` — Host + Origin + Content-Type guard |
| **H1** file permissions | **Fixed** | `41d5d8f` — `secure_write_json`, migration for existing files |
| **H2** encryption at rest | **Fixed (scoped)** | `71cd33a` — secrets only; whole-file encryption deferred, see below |
| **H3** dependency CVEs | **Fixed** | `8bbd4fc` — Next 16, `npm audit` 0 |
| **H4** no CI / no gates | **Fixed** | `4042875` — pipeline, ruff enforced, 94 findings → 0 |
| **H5** committed binaries | **Fixed** | `2a75ed0` — 4 `.deb` untracked |
| **M1** false audio claim | **Fixed** | `f19c4ff` — manifest corrected |
| **M4** no LICENSE | **Fixed** | `2a75ed0` — MIT added; see the ownership note in that commit |
| **M3** runtime divergence | **Fixed** | `4042875` — CI matrix 3.11/3.12/3.13 |
| M2, M5, M6, M7, L1–L4 | Open | Phase 2 |

### Found during the work, not in this audit

The audit was read-only, and three defects only appeared once a browser was
actually launched. That is the argument for ground rule 4.

| | Severity | State |
|---|---|---|
| **The extension never loaded at all.** Chrome refuses `--load-extension`, so fingerprint spoofing, cookie seeding and proxy auth were dead while the console reported profiles healthy | Critical | **Fixed** `60399d0` — replaced with CDP injection |
| **`--remote-allow-origins=*`** let any website drive a profile's browser over CDP | Critical | **Fixed** `aa6728f` |
| **The DNS containment flag broke every proxy.** `--host-resolver-rules=MAP *` caught the proxy's own address, so no proxied profile could browse | High | **Fixed** `aa6728f` |
| `load_profiles` iterated a corrupted store's dict keys; `load_gateways` crashed on scalar entries | Medium | **Fixed** `4fbb227` |

### Scope statements that must not be overstated

- **"No plaintext JSON on disk" is not true and is not claimed.** Proxy
  passwords are encrypted; profile names, tags and user agents stay
  readable so the files remain diffable and the Phase 2 storage move is not
  fighting an encrypted blob.
- **The secrets key falls back to a 0600 file** where no OS keychain exists.
  Reported by `backend()`, carried in `/api/health`, warned at startup.
- **Windows and macOS remain unbuilt.** Tiling is X11-bound. Linux-first.

### Verified by measurement, not assertion

```
DNS containment   strace port 53:  no proxy 76 syscalls / proxied 0
proxy auth        real 407 proxy:  2 challenges, 2 authenticated
fingerprint       host → profile:  cores 12→4, platform Linux x86_64→Win32
CDP lockdown      hostile Origin:  ACCEPTED → REFUSED 403
isolation         5 storage surfaces, profile B reads null on every one
suite             1121 passed, 2 skipped, twice consecutively
coverage          73.4% (gate 70%)
```

## Phase status

Phase 0 recon complete. Phases 1 and 1.5 complete: **zero Critical or High
findings open**. Awaiting approval before Phase 2.
