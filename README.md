# Multiprofile Linux 1.1.0

Native Ubuntu/Linux multi-profile browser manager (personal/lab use).

Clean-room host for authorized UI/extension assets. No Wine. No AdsPower proprietary code.

## What's in 1.1.0

### Phase 1 — Solid manager
- Isolated Chromium/Chrome profiles (`--user-data-dir`)
- Custom proxy parse + `--proxy-server` + extension auth (`proxy.json`)
- Real `POST /api/proxy/test` (TCP + optional HTTP egress)
- Cookies import/export (Chrome JSON + Netscape) under XDG `cookies/`
- WebRTC modes: `default` | `disable_non_proxied_udp` | `disable`
- Deep `/api/health`, duplicate profile, thin `/api/v1/profile/*` aliases

### Phase 2 — Fingerprint consistency foundation
- Deterministic `build_fingerprint(profile)` from seed + UA/isMobile
- Platform pools (Win/Mac/Linux desktop, Android/iPhone mobile) keep UA ↔ platform ↔ touch ↔ screen aligned
- Extension `evasion.js` overrides hardware/GPU/screen/languages/client-hints from Python config
- `GET /api/profiles/<id>/fingerprint` human-readable report + mismatch warnings

## Honest limits
- **Same egress IP correlates profiles** if you do not use distinct proxies.
- Spoofing is **JS-level + Chrome flags**, not a Chromium fork — advanced detectors can still find gaps.
- GitHub Cloud Runner proxy type is a UI stub (not implemented natively).
- Not a ban-evasion playbook; for personal/lab multi-profile isolation only.

## Stack
- Python 3.11+, Flask local API
- Optional pywebview (GTK/WebKit) desktop window
- System Chromium/Chrome for profile launch

## Dev quick start
```bash
cd multiprofile-native
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui]"
python -m multiprofile --server-only
# open http://127.0.0.1:8765/
```

## Tests
```bash
./scripts/test.sh
```

## Build .deb
```bash
./build-deb.sh
# => dist/multiprofile-native_1.1.0_amd64.deb
sudo dpkg -i dist/multiprofile-native_1.1.0_amd64.deb
```

## XDG paths
- `~/.config/multiprofile/`
- `~/.local/share/multiprofile/` (profiles.json, cookies/, chrome-profiles/)
- `~/.cache/multiprofile/`
