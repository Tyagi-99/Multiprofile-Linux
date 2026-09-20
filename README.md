# Multiprofile Linux 1.2.0

Native Ubuntu/Linux multi-profile browser manager (personal/lab use).

Clean-room host for authorized UI/extension assets. No Wine. No AdsPower proprietary code.

## What's in 1.2.0

### Ops / safety
- Job queue + concurrency gate (`MAX_CONCURRENT_PROFILES=3`) so RAM stays bounded
- Orphan reaper for dead Chrome PIDs
- Safe delete with optional storage purge
- Optional `MULTIPROFILE_API_TOKEN` for `/api/*` (health stays open)
- Plaintext zip backups + sha256 checksums under XDG `backups/`
- Append-only audit log

### Organization
- Clients (`clients.json`) and profile `clientId`
- Tags + search/pagination on profiles
- Templates + create-from-template (1..N)

### Consistency
- Fingerprint warnings on create/update
- Launch blocked (409) on CRITICAL mismatches unless `?force=1`
- Optional CDP debugging port on launch (`cdp: true`)

## Honest limits
- **Same egress IP correlates profiles** if you do not use distinct proxies.
- Spoofing is **JS-level + Chrome flags**, not a Chromium fork.
- Backups are **plaintext zip** (no encryption in MVP).
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

## Env vars
| Variable | Default | Meaning |
|----------|---------|---------|
| `MAX_CONCURRENT_PROFILES` | `3` | Max simultaneous Chrome launches |
| `MULTIPROFILE_API_TOKEN` | unset | If set, require `Authorization: Bearer <token>` on `/api/*` except GET `/api/health` |
| `MULTIPROFILE_STATIC` | auto | Override static UI path |
| `MULTIPROFILE_EXTENSION` | auto | Override extension path |

## Tests
```bash
./scripts/test.sh
```

## Build / install .deb
```bash
./build-deb.sh
# => dist/multiprofile-native_1.2.0_amd64.deb
sudo dpkg -i dist/multiprofile-native_1.2.0_amd64.deb
```

## XDG paths
- `~/.config/multiprofile/`
- `~/.local/share/multiprofile/` — `profiles.json`, `clients.json`, `templates.json`, `cookies/`, `chrome-profiles/`, `backups/`, `audit.jsonl`
- `~/.cache/multiprofile/` — `runtime.json`
