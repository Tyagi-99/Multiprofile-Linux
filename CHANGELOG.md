# Changelog

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
