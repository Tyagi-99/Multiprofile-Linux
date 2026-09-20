# Changelog

## 1.1.0 — 2026-09-21
### Added
- Deterministic fingerprint builder (`fingerprint.py`) + report API
- Cookies store with Chrome JSON / Netscape import-export
- Real proxy parse + test + Chrome `--proxy-server` application
- WebRTC mode mapping to Chrome flags + JS hardening
- Extension cookies/proxy apply in background service worker
- Profile fields: seed, language, webrtcMode, proxyHost/Port/User/Pass, notes, startUrl, fingerprint cache
- `duplicate_profile`, deep health, `/api/v1/profile/*` aliases
- SPA: language, webrtcMode, seed, fingerprint button; proxy test wired to backend

### Changed
- Version bump to 1.1.0 across package, deb, extension, docs
- Launch applies custom proxy unless `direct=true`

### Limits (documented)
- JS-level spoofing, not a Chromium fork
- Same IP correlates profiles without distinct proxies

## 1.0.0 — 2026-09-21
- Initial native Linux release
