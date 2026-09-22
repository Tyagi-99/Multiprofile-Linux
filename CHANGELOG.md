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
