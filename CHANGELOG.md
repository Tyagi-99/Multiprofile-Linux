## Unreleased

- **A deleted profile can no longer stay "running".** `runtime.json` and
  `profiles_runtime` both kept rows after the profile was gone — 151 JSON
  entries and 42 runtime rows against 5 live profiles on the machine where
  the landing screen was found disagreeing with itself. Metrics already
  reads the database. The profile list, the parking check and the reaper
  now do too, a delete or trash drops the runtime row in the same
  transaction, and startup deletes the rows that are already there and
  rewrites the JSON cache from what remains.

- **The claimed Chrome version now comes from the installed engine** (audit
  finding F5, new `engine.py`, `fingerprint/_core.py`, `browser/launcher.py`,
  `browser/cdp_identity.py`, `devices/*`, `api/user_agents.py`): every UA pool,
  all seven device presets and the UA picker were written against Chrome 131,
  while the engine actually running them is whatever Chromium the host has —
  141 in CI, 151 on the dev machine, roughly twenty stable releases later. A
  page could tell twice over: by probing an engine feature that shipped after
  131, and by cohort alone, since Chrome 131 has negligible real-world share.
  - New `engine.py` asks the binary for its version once per process and owns
    the rewrites. Pool defaults and preset UAs are now templates realigned to
    that answer; `browser_version_policy` moved from `"pinned:131"` to
    `"match-engine"`.
  - **An explicit `profile.userAgent` is never renumbered** — an operator may
    be matching a fingerprint bought elsewhere. The launch notes report the
    disagreement instead, and the client hints follow the UA, so the two can
    never claim different browsers.
  - **An unknown engine invents nothing.** No binary, an unreadable binary or
    `MULTIPROFILE_CLAIM_ENGINE_VERSION=0` leaves the literals in place and
    records `source: "unavailable"`; the launch note calls the claim
    `UNVERIFIED` rather than reporting a pass.
  - The UA string carries the reduced `<major>.0.0.0` form and
    `Sec-CH-UA-Full-Version-List` carries the real build, as a real Chrome
    does. Both used to carry `131.0.0.0`, a patch version no Chrome release
    has ever had.
  - Fixed on the way: `_chrome_version()` matched only `Chrome/`, so the iPhone
    pool (`CriOS/`) always fell back to the 131 literal; and
    `GET /api/devices/<id>` served the raw catalogue, so the device page and
    the wire described different browsers.
  - Settings: `MULTIPROFILE_ENGINE_VERSION` pins a claimed version explicitly;
    `MULTIPROFILE_CLAIM_ENGINE_VERSION=0` turns alignment off. `/api/health`
    reports the resolved engine under `engine`.

- **Profiles no longer browse as the host machine** (audit finding F1,
  `fingerprint/_core.py`, `browser/cdp_identity.py`, `browser/cdp_inject.py`,
  `browser/launcher.py`, new `browser/identity_assert.py`): a profile created
  without a device preset — the one the quick-create button makes — sent the
  host's own `User-Agent` and `sec-ch-ua-*` headers while its JavaScript
  claimed to be Windows. Measured on the wire before the fix:
  `Mozilla/5.0 (X11; Linux x86_64) … Chrome/141` with
  `sec-ch-ua-platform: "Linux"` beside `navigator.platform: "Win32"`. That
  contradiction is something no real browser can produce, so the profile was
  *more* identifiable than an unmodified browser. Three causes, all fixed:
  - **No User-Agent existed to set.** `build_fingerprint` echoed
    `profile["userAgent"]`, which is empty on the quick-create path, so the
    launcher passed no `--user-agent` at all. Every fingerprint pool now names
    a default UA whose Chrome major matches the client-hint brands the same
    pool builds, and `--user-agent` is set on **every** launch.
  - **The override was discarded as it was applied.** `apply_ua_override()`
    sent `Network.setUserAgentOverride` down a one-shot WebSocket; Chromium
    scopes that override to the DevTools session that set it, so it vanished
    when the socket closed — while the ACK made the function return `True` and
    the launcher log "override applied". It now takes a **held** session and
    returns `False` whenever it does not have one; the one-shot helper that
    made the mistake possible is deleted rather than documented. The identity
    injector applies the same override per attached target, so tabs opened
    later are covered too, and the launcher holds one CDP session open for the
    browser's lifetime.
  - **Nothing ever checked.** New `identity_assert` module: after the identity
    is installed the launcher stands up a loopback server, makes the browser
    navigate to it, and compares the headers that arrive against the identity
    it intended. A **proven mismatch now fails the launch** (state `error`,
    `identity_mismatch`) instead of being reported as healthy; a probe that
    cannot run is recorded as unverified and is never treated as a pass.
    Settings: `identity_assert_enabled` / `MULTIPROFILE_IDENTITY_ASSERT`.
    The evidence travels with the launch result (`identityAssertion`) and in
    the runtime registry (`identity_ok`, `identity_detail`).

  Also fixed, found by the new assertion once the override started applying:
  `acceptLanguage` was passed as a finished header, so Chromium emitted
  `en-US,en;q=0.9;q=0.9` — malformed, and a tell in itself. It is now passed
  as the language list Chromium expects.

  Known residual: a tab's very first document request is issued before its
  per-target override lands, so `sec-ch-ua-platform` on that one request still
  reflects the host. Closing it means rewriting headers in the browser process
  (browser-level `Fetch` interception) and is tracked in the audit report, not
  papered over — the assertion measures a navigation and says so.

- **evasion.js no longer announces itself** (audit finding F7,
  `src/extension/evasion.js`, `config.py`, `browser/launcher.py`): the script
  opened with `console.log("[Evasion] evasion.js active on:", location.href)`,
  which ran on every page and in every frame — twelve lines above its own
  comment saying "No console.log — logging extension activity is a detection
  signal". A page cannot read past console output, so it was never directly
  exploitable, but it was a plain-text confession in any DevTools session,
  screen share, demo or support screenshot. The line now runs only when
  `MULTIPROFILE_EVASION_DEBUG=1` (setting `evasion_debug`, default off) puts
  `debug: true` in the injected config, and it was moved *after* the config is
  parsed so it can be switched on at all. With the flag off the page config
  gains no key and the browser produces no console output at all (verified
  live: 0 messages off, 1 per document on). It is kept rather than deleted
  because the count of those lines for one document is the only honest way to
  measure how many times the script ran — the measurement that caught F2 — and
  the launch notes say loudly when it is on.

- **The cleartext proxy password is no longer on disk — including the copies
  already there** (audit finding F4, `storage/xdg.py`, `app.py`,
  `browser/launcher.py`): the extension staging step wrote
  `<profile>/extension/proxy.json` (the proxy username and password in
  cleartext, decrypted straight out of the OS keychain) and
  `<profile>/extension/cookies.json` (the whole cookie jar) with a bare
  `write_text`, so the umask decided the mode — **0644, world-readable**,
  undoing the keychain encryption the launcher had just used. Every other
  writer in the codebase goes through `secure_write_json` (0600).
  - Removing the writer (see F2 above) stops new ones, but does nothing for
    the copies sitting in a user's data directory right now. New
    `purge_legacy_extension_staging()` **deletes** those directories — at
    startup for every profile, and at launch for the profile being launched,
    so a process that started before the sweep still cleans as it goes.
    Deleted rather than hardened: nothing reads them any more, and both files
    are copies (the canonical cookie jar is `cookies/<id>.json`, the canonical
    password is in the keychain), so a 0600 plaintext secret would be a
    plaintext secret kept for no reason. A directory that cannot be removed
    has its two secret files tightened instead, and the next sweep retries.
  - `test_file_permissions.py` gains a guard that the launcher contains
    exactly one `write_text` — the temp file it renames over Chrome's own
    `Preferences` — so a third umask-decided write cannot appear unnoticed.

- **evasion.js now reaches a page exactly once** (audit finding F2,
  `browser/launcher.py`, `browser/cdp_inject.py`, `browser/health.py`): the
  launcher still passed `--load-extension` and `--disable-extensions-except`
  and still staged a per-profile copy of the extension, *while* the CDP
  injector installed the same script. Google Chrome refuses those flags
  ("--disable-extensions-except is not allowed in Google Chrome, ignoring") —
  which is why CDP injection was written — but Chromium honours them, so on
  Chromium the script ran twice per document and on Chrome once. Measured on
  Chromium 141 with a real `http://` page: two `[Evasion] evasion.js active
  on: <url>` lines for one document before, one after. Two runs mean the
  canvas and WebGL noise is applied twice, so the rendered fingerprint is
  neither the one the config specifies nor reproducible across engines.
  - Both flags and the whole `_prepare_extension` staging step are removed.
    CDP is the only route. `src/extension/` stays as the *source* of
    `evasion.js` and is no longer loaded into any browser.
  - Exclusivity (`allow_webstore_extensions=False`, the default) now means
    plain `--disable-extensions`: there is no extension of ours to except.
  - Carried over from the staging step so they were not lost silently: the
    `__PROFILE_TIMEZONE__` / `__PROFILE_ALLOCATED_IP__` / `__PROFILE_SEED__`
    substitutions (`build_identity_script` takes a `replacements` mapping) and
    the explicit `webrtcMode`.
  - `health.extension_loaded` tested for the staging copy, which would have
    read False on every healthy profile once the copy was gone. It now reports
    the injector's `inject_ok` — whether the script actually reached the
    browser, which is what the field was always meant to mean.
  - The audit's own measurement for this finding was wrong and is corrected in
    the report: `toString(toDataURL).match(/copy = null/g).length == 2` counts
    2 for a *single* layer, and `evasion.js` already had an `alreadyShielded`
    guard that kept the canvas hook from stacking. The script's execution count
    per document is the honest measure; the finding held.

- **Supervisor restart budget now bounds crash loops** (`browser/supervisor.py`):
  `max_restart_attempts` lived in a local loop variable, so it only bounded
  consecutive *launch failures* within a single death. A browser that launched
  successfully and then died on the next watchdog tick got a brand-new budget
  every time and was relaunched forever (field symptom: `relaunch ... succeeded
  (attempt 1)` repeating every ~11s). The budget is now spent per **crash
  episode**: a restart counts as a recovery only once the relaunched browser
  survives `restart_stability_s` (new setting, default 60s,
  `MULTIPROFILE_SUPERVISOR_STABILITY_S`). Deaths inside that window continue
  the episode and spend the same budget, so a crash-looping profile is parked
  in `error` after 3 restarts and waits for an explicit operator restart;
  surviving the window resets the budget so unrelated crashes each get a full
  allowance. Exponential backoff now spans deaths too, and the supervisor warns
  at startup if the stability window is shorter than the check interval (where
  the budget could never bind). Two regression tests cover the loop and the
  reset.

## 1.9.0 (2026-09-24)

- **Premium console redesign**: the old single-file console (`src/static/app.js`) is replaced by a Next.js 15 / React 19 / TypeScript / Tailwind v4 static export, served from the same `src/static` directory and packaged into the deb exactly as before — no Node.js runtime needed on the machine. Hash routing keeps every deep link working; all Flask API contracts, the browser launcher, supervisor, 33 device presets (with honest support badges), fingerprints, proxies/gateways, jobs, audit, backups, and proxy fail-closed behaviour are preserved.
- **New 6-step New Profile wizard** (Basic → Device → Network → Fingerprint → Consistency → Review) with ad-hoc proxy testing, per-device fingerprint capability matrix, local pre-flight checks, and a double-confirm on errors. The quick-create dialog stays, with a "Full setup ›" cross-link into the wizard.
- **New sections**: Overview dashboard (real data), Profiles table (filters, cursor pagination, row selection, bulk actions, export with proxy passwords stripped), 10-tab profile detail, Device Library, Fingerprint Inspector, Network tabs (proxies/gateways/diagnostics), Jobs with live SSE updates, Audit log, and Settings (general, clients & templates, backups with passphrase restore, extensions status).
- **Global shell**: command palette (`Ctrl+K`), keyboard shortcuts, light/dark/system themes, toast notifications, bearer-token dialog, skip-to-content link, focus-trapped dialogs, and screen-reader announcements. The wizard is code-split; first-load JS ~196 kB.
- **Packaging**: all seven version markers synchronized on 1.9.0.

## 1.8.0 (2026-09-23)

- **Desktop logo**: original Multiprofile mark — four isolated browser panes in a tight grid, pine teal `#0e7c6b` on deep ink `#131a25`, flat and readable at 16px. Shipped as SVG (`packaging/icons/multiprofile.svg`) plus PNGs at 512/256/128/64/48/32/16 installed under `/usr/share/icons/hicolor/<size>/apps/multiprofile.png` via the deb; `packaging/debian/multiprofile.desktop` now uses `Icon=multiprofile` (replacing the generic terminal icon), and `postinst` refreshes the icon cache when `gtk-update-icon-cache` exists. The same artwork is used for `src/static/app_icon.png`, `favicon.ico`, the console sidebar, the login card, and the pywebview window icon (with a graceful fallback for older pywebview versions).
- **Light and dark mode**: one Light/Dark segmented toggle in the console top bar, persisted via `storeSet("mp_theme", "light"|"dark")`. First launch follows `prefers-color-scheme`; `data-theme` is set on `<html>` before paint so the window never flashes the wrong theme. Light keeps the current look (dark sidebar `#131a25`, light content `#f3f5f8`); Dark restyles content surfaces, cards, tables, inputs, and the drawer with the existing `--mp-*` tokens. No second font, no purple accent.
- **Profile grid**: explicit full-fill layouts for 1–3 profiles — 1 covers the whole work area; 2 side-by-side when wider than tall, otherwise stacked; 3 as one row on wide displays, otherwise a column (never a 2×2 with a blank cell). 4+ keep the balanced grids (2×2, 3×2, 3×3, 4×3). GridCalculator stays the tiling authority, CDP `Browser.setWindowBounds` applies, gap 0 / margin 0 / work-area clip unchanged. The whole grid is re-tiled after every launch and after every stop so remaining windows keep filling the work area.
- **Stop stays stopped**: `stop_profile` records an explicit user stop (`STOPPING` with a "user stop requested" note), SIGTERMs the pid, waits for it to die, then marks `STOPPED`, releases the slot, and re-tiles the survivors. The supervisor re-reads the runtime state before any restart and relaunches only an unexpected exit from `RUNNING` — a user stop is never treated as a crash, and no profile relaunches after Stop, Park, or exhausted retries until the user presses Launch.
- **Packaging**: all seven version markers synchronized on 1.8.0.

## 1.7.1 (2026-09-23)

- **Desktop webview fixes** (blank-window regression): `open_ui` now starts pywebview with `private_mode=False` and a persistent `storage_path` at `~/.local/share/multiprofile/webview`; added the `[hidden] { display: none !important; }` CSS guard; all `localStorage` access in the console goes through `storeGet`/`storeSet`/`storeDel` helpers only.
- **Packaging**: `packaging/debian/control` Version corrected to 1.7.1 (was stale at 1.5.0). All seven version markers now agree on 1.7.1.

## 1.7.0 (2026-09-23)

- **Browser Supervisor** (`browser/supervisor.py`): crash watchdog polling each
  profile's PID on a configurable interval; on death it audits, moves the
  profile to `error`, and performs a bounded restart (max 3 attempts, backoff)
  via the launcher followed by a full grid re-tile over CDP. Exhaustion leaves
  the profile in `error` with a reason — it never spins forever.
- **Durable Job Queue** (`jobs/`, `storage/db.py`): SQLite WAL-backed control
  plane with `profiles_runtime` + `jobs` tables. `recover()` runs first at
  every boot: jobs stuck `running` are re-queued (attempts+1, exhausted ones
  fail), profiles stuck in `starting`/`stopping` go to `error`. A worker pool
  claims and executes jobs with bounded concurrent launches. Legacy in-process
  queue preserved at `jobs/legacy.py` with the same import surface.
- **Profile State Machine** (`storage/state.py`): validated transitions across
  `stopped → starting → running → stopping → stopped`, plus `idle`, `parked`,
  `error`; every transition writes an audit note.
- **Idle Parking & Metrics** (`parking.py`, `metrics.py`, `profile_log.py`):
  park idle profiles (graceful stop, data kept); `/proc`-based CPU/mem
  sampling (5s cache cap); JSONL profile log + audit trail;
  `GET /api/metrics/summary` and per-profile metrics (new `[parking]`,
  `[health]`, `[metrics]` config sections).
- **API Hardening**: cursor pagination on list endpoints (`api/pagination.py`);
  invalid input rejected with 400 `{"error": {"code": "invalid_input"}}`
  (`api/validation.py`); remote (non-localhost) requests require a bearer
  token while localhost stays frictionless (`api/auth.py` — see the honest
  assessment in its docstring); live event stream `GET /api/events/stream`
  (SSE, with `max_events` for finite reads).
- **Console Restyle**: sidebar, dense profile table, creation wizard, device
  library, jobs/audit views, SSE with polling fallback; dead `exportProfiles`
  helper wired to an Export button (proxy passwords stripped on export).
- **Netns (experimental)**: `gateway/netns.py` scaffold for Linux network
  namespaces — EXPERIMENTAL / NOT ACTIVE, default off, no launcher wiring;
  see `docs/NETNS_ROADMAP.md`.
- **Startup wiring**: `create_app()` now runs `recover()` → `start_workers()`
  → `start_supervisor()` (config-guarded), fail-safe: the app boots even if
  the control DB cannot open. Set `MULTIPROFILE_NO_BACKGROUND=1` to keep
  daemon threads out of test processes.
- **Tests**: 835 passing / 9 skipped, incl. new `test_storage_jobs_supervisor.py`,
  `test_api_hardening.py`, `test_parking_metrics.py`, `test_frontend_smoke.py`,
  `test_netns.py`, and `test_startup_wiring.py` (startup sequence + SSE E2E).

## 1.6.0 (2026-09-23)

- **Device Presets (33, honest statuses)**:
  - 33 coherent device identity bundles: 20 Android phones, 7 iOS-class iPhones,
    2 iPadOS-class tablets, 1 Android tablet, 3 desktop (Windows/Linux/macOS-class).
  - `DevicePresetValidator` runs static checks plus a genuine CDP round-trip
    against a real Chromium binary: geometry is applied via DevTools and the
    *page* reads it back (e.g. Pixel 9: applied 411x923@2.626, page reports
    411x923).
  - Measured matrix: VALIDATED 2 (`win11-chrome`, `ubuntu-chrome`), PARTIAL 31
    (all checks pass; capped by declared capability limits, not failures),
    PENDING 0, UNSUPPORTED 0.
  - Capability matrix honestly declares host limits: real host WebGL renderer,
    iOS-class = Chromium not WebKit, macOS-class cannot reproduce macOS
    GPU/media/fonts, emulated sensors, platform font stacks missing on Linux.
- **Fingerprint Inspector & Consistency Engine**:
  - `GET /api/profiles/<id>/fingerprint` reports per-component `expected` values;
    device profiles pin Screen exactly to preset viewport x DPR and carry the
    preset UA / Client Hints. (Legacy top-level `fingerprint` may still reflect
    the stored legacy desktop fingerprint.)
  - `GET /api/profiles/<id>/consistency` runs read-only per-domain checks
    (`geo`, `device`, `platform`, `mobile`, `browser`, `gpu_webgl`), each
    PASS/WARNING/ERROR with a message; overall = worst domain.
- **Honest Network Diagnostics**:
  - `POST /api/profiles/<id>/network/test` returns `HEALTHY | DEGRADED | DOWN`
    with real measurements or real errors — never placeholders. Non-HEALTHY
    results carry the actual error; proxy credentials are masked (`***`) in
    error strings. ip-api 403s surface as DOWN + error.
- **Launcher Hardening**:
  - Fixed CDP `userAgentMetadata` brand shape (`fullVersionList` entries now use
    `{brand, version}` as CDP requires); verified live: all three emulation
    commands OK, page reports applied geometry, touch, and Android UA.
  - Unknown `deviceId` records a `device preset not found` note instead of a
    traceback; launching with no Chromium binary fails fast with
    `dependency_missing` instead of hanging.
- **Gateways (unchanged)**: Azure and WireGuard providers remain
  SCAFFOLD / NOT ACTIVE — `provision()` returns `not_implemented`, pinned by
  tests. Manual proxy routing via `custom` remains the production path.
- **Docs**: new `docs/DEVICE_PRESETS.md` (full 33-preset catalogue + measured
  matrix) and `docs/PROFILE_CONSISTENCY.md`; updated ARCHITECTURE (1.6.0),
  FINGERPRINT_MODEL, NETWORK_ISOLATION, REMOTE_GATEWAYS (scaffold re-verified).
- **Tests**: new `tests/test_integration_v16.py` (API E2E, launcher exception
  safety, honest-diagnostics shape) and `tests/test_release_v16.py` (version
  coherence, docs presence, secret-leak scan, gateway scaffold pins).

## 1.5.0

- **Network Isolation & Fail-Closed Pre-Launch Gate**:
  - Implemented `NetworkConfig` with modes (`DIRECT`, `HTTP_PROXY`, `HTTPS_PROXY`, `SOCKS5`, `REMOTE_GATEWAY`) and policies (`DNSPolicy`, `IPv6Policy`, `WebRTCPolicy`, `ProxyFallback`).
  - Added `ProxyHealthManager` pre-launch validation: TCP handshake + HTTP egress verification. Configured proxies fail closed (`ProxyFallback.BLOCK` default) to prevent silent host IP leaks.
  - Transparent migration from legacy proxy fields to `NetworkConfig`.
  - Credentials stripped from CLI flags and passed securely via per-profile extension `proxy.json` and `onAuthRequired`.
- **Remote Gateway Architecture**:
  - Modular `RemoteNetworkProvider` interface.
  - Production-ready `CustomGatewayProvider` for HTTP/SOCKS5 endpoints.
  - Transparent `AzureGatewayProvider` scaffold with explicit manual setup documentation (no false claims of automated cloud egress).
  - Storage in `gateways.json` with strict `0600` permissions and password masking in all API responses.
- **Identity & Fingerprint Consistency**:
  - Dual-tier alignment: CDP `Network.setUserAgentOverride` with Client Hints (`userAgentMetadata`) synchronizes wire HTTP headers with DOM properties.
  - Hardware GPU probing (`/dev/dri`) resolves real GPU vs SwiftShader without `--disable-gpu` self-contradictions.
  - Added desktop Chrome `navigator.plugins` (3 PDF plugins) and `window.chrome` (`loadTimes`, `csi`, `app`, `runtime`).
  - Added stable per-seed media device IDs (`audioinput`, `audiooutput`, `videoinput`).
  - Removed detection signal `console.log` from `evasion.js`.
- **Encrypted Backups & Manifest V2**:
  - Optional AES-256-GCM encrypted backups (`.mp_enc`) with PBKDF2-HMAC-SHA256 key derivation (600,000 iterations).
  - Plaintext backwards compatibility maintained.
  - Backup Manifest V2 includes version, creation timestamp, profile/client IDs, encryption metadata, and SHA-256 checksums without leaking passwords.
- **Profile Health & Observability**:
  - Live `ProfileHealth` model and `GET /api/profiles/<id>/health` and bulk `/api/health/profiles` endpoints.
  - Clone modes (`config_only`, `config_and_cookies`, `full_session`) with deterministic fresh seed generation.

## 1.4.6

- Do not silently launch GitHub Cloud Runner profiles on the home IP. Connecting a PAT does not provide an Azure/datacenter proxy; launch fails with instructions to use Custom Proxy or Direct.
- Apply canvas/WebGL noise inside Web Workers (CreepJS reads canvas there, which previously leaked the real GPU fingerprint across profiles).


## 1.4.5

- GitHub Cloud Accounts: Verify & Connect now calls `api.github.com/user`, stores the PAT (0600 file), and lists accounts. Requires classic PAT scopes `repo` and `workflow`. Cloud Runner proxy launch is still not implemented — use Direct or Custom Proxy for traffic.

## 1.4.4

- Disable Chrome CSD drop-shadows on tiled windows (`browser.custom_chrome_frame=false`) so adjacent tiles look flush. Geometry was already edge-to-edge; the purple strips were the client shadow drawn inside the outer bounds.

## 1.4.3

- Edge-to-edge tiling: default `gap=0` / `margin=0`. Remainder pixels are distributed so adjacent outer bounds touch and the grid consumes the full work area. No overlap.

## 1.4.2

- Force Chrome onto X11/XWayland (`--ozone-platform=x11`) so `Browser.setWindowBounds` actually tiles windows. On Wayland, CDP reported success while the compositor left windows cascaded.
- Pass grid slot geometry at launch (`--window-position/--window-size`) then re-apply via CDP.
- Merge `--disable-features` into a single flag (ChromeWhatsNewUI + WaylandWindowDecorations).

## 1.4.1

- Fix window tiling on Chrome 131+: add `--remote-allow-origins=*` (CDP was 403 Forbidden).
- Prefer primary monitor via `xrandr` so multi-monitor setups do not get a 5000px-wide work area.
- Ensure `python3-websocket` / `websocket-client` is available for CDP.

# Changelog

## 1.4.0 — 2026-09-21
### CDP window tiling (Approach A)
- **Localhost-only CDP** (`--remote-debugging-address=127.0.0.1`) on every launch; ephemeral port per profile; cleared on stop.
- **WindowLayoutManager** is the sole owner of slot geometry; `Browser.setWindowBounds` / `getWindowBounds` with ≤4px tolerance and max 3 retries.
- **WorkAreaProvider** fail-loud (xprop `_NET_WORKAREA` → xdpyinfo → `MULTIPROFILE_WORKAREA`); never silent 1920×1080.
- **Launch Selected** = best-effort tile; **`tileRequired`** / **`POST /api/windows/tile`** = fail-closed on missing work area; browsers are never auto-killed on tile failure.
- **SPA**: Tile Windows button; profile rows expose `launchState` / `cdpPort` from the in-memory runtime registry (no DB migration).
- **Exclusivity** remains ON by default (`--disable-extensions-except`).
- Unit tests for grid N∈{1..20}, work-area fail-loud, runtime transitions, idempotent plans. Live CDP smoke gated by `MULTIPROFILE_CDP_TILE_TESTS=1`.


## 1.3.0 — 2026-09-21
### Phase 2 browser runtime (anti-detect exclusivity preserved)
- **Exclusivity ON by default** (AdsPower-class): Chrome still launches with `--disable-extensions-except=<anti-detect>` so only the built-in anti-detect extension loads. Chrome Web Store / extra extensions are deferred (`MULTIPROFILE_ALLOW_WEBSTORE_EXTENSIONS=1` opt-in only — not recommended for production fingerprinting).
- **Sandbox probe**: drop hard-coded `--no-sandbox` when SUID `chrome-sandbox` or unprivileged user namespaces work; fall back with a logged reason only when needed. Exposed on `/api/health` as `sandbox`.
- **Concurrency**: default `MAX_CONCURRENT_PROFILES=20` (was 3) via central `config.get_settings()`. Bulk launch marks profiles **queued** while waiting for a slot; UI shows Queued / Failed / Running / Idle.
- **Window tiling**: non-overlapping tiles from real `_NET_WORKAREA` (fallback xdpyinfo/env); applied via `--window-position/--window-size` plus best-effort `xdotool`/`wmctrl` placement. Toggle with `MULTIPROFILE_WINDOW_TILING`.

## 1.2.0 — 2026-09-21
### Added
- In-process job queue (`/api/jobs`) for launch/stop/bulk with concurrency gate (`MAX_CONCURRENT_PROFILES`, default 3)
- Orphan reaper (15s background + `POST /api/maintenance/reap`) marks dead PIDs idle
- Safe delete: `DELETE /api/profiles/<id>?purgeStorage=1` (default) removes chrome-profiles data
- Optional API token via `MULTIPROFILE_API_TOKEN` (`Authorization: Bearer …`); `/api/health` remains open
- Clients CRUD (`/api/clients`) + profile `clientId` (delete client nulls refs, no cascade)
- Profile tags + filtered/paginated `GET /api/profiles?q=&clientId=&status=&tag=&limit=&offset=` (raw list when no query args)
- Templates CRUD + `POST /api/profiles/from-template`
- Consistency gate: create/update return `warnings`; launch returns 409 on CRITICAL mismatches unless `?force=1`
- Backup/restore plaintext zip + sha256 under XDG `backups/` (no encryption in MVP)
- Append-only audit log (`/api/audit`)
- Optional CDP: launch body `cdp:true` → `--remote-debugging-port` + `debugPort` in result
- SPA: client filter, bulk launch/stop via jobs, API token field, health badge, backup button

### Changed
- Version bump to 1.2.0 across package, deb, extension, docs

### Limits
- Backups are plaintext zip (stdlib); set a filesystem ACL if you need secrecy. `MULTIPROFILE_BACKUP_PASS` reserved, not implemented.
- Concurrency gate is in-process only (single API worker).
- Fingerprint spoofing remains JS-level + Chrome flags, not a Chromium fork.

## 1.1.0 — 2026-09-21
### Added
- Deterministic fingerprint builder + report API
- Cookies store with Chrome JSON / Netscape import-export
- Real proxy parse + test + Chrome `--proxy-server` application
- WebRTC mode mapping to Chrome flags + JS hardening
- Extension cookies/proxy apply in background service worker
- Profile fields: seed, language, webrtcMode, proxyHost/Port/User/Pass, notes, startUrl, fingerprint cache
- `duplicate_profile`, deep health, `/api/v1/profile/*` aliases
- SPA: language, webrtcMode, seed, fingerprint button; proxy test wired to backend

### Limits (documented)
- JS-level spoofing, not a Chromium fork
- Same IP correlates profiles without distinct proxies

## 1.0.0 — 2026-09-21
- Initial native Linux release
