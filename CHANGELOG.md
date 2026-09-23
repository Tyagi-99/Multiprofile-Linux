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
