# Multiprofile Native API (1.2.0)

Base: `http://127.0.0.1:8765`

## Auth
If `MULTIPROFILE_API_TOKEN` is set, send `Authorization: Bearer <token>` on all `/api/*` except `GET /api/health`.

## Health
- `GET /api/health` — version, chrome_ok, static_ok, extension_ok, slots, reaped

## Profiles
- `GET /api/profiles` — raw list when no query args (compat)
- `GET /api/profiles?q=&clientId=&status=&tag=&limit=50&offset=0` → `{items,total,limit,offset}`
- `POST /api/profiles` — create; returns `{ok,profile,warnings}`
- `PUT /api/profiles/<id>` — update; returns warnings
- `DELETE /api/profiles/<id>?purgeStorage=1` — default purges chrome-profiles + cookies (`purgeStorage=0` keeps files)
- `POST /api/profiles/<id>/launch` — body `{url?,direct?,cdp?}`; CRITICAL consistency mismatches → **409** unless `?force=1`
- `POST /api/profiles/<id>/stop`
- `POST /api/profiles/<id>/duplicate`
- `GET /api/profiles/<id>/fingerprint`
- `POST /api/profiles/<id>/backup` — plaintext zip + sha256 under XDG `backups/`
- `POST /api/profiles/from-template` — `{templateId, name?, clientId?, count?}`

## Jobs
- `POST /api/jobs` — `{type: launch_profile|stop_profile|bulk_launch|bulk_stop, profileIds, url?, direct?, cdp?}`
- `GET /api/jobs` / `GET /api/jobs/<id>` / `POST /api/jobs/<id>/cancel`

## Clients / Templates
- `GET|POST /api/clients` · `PUT|DELETE /api/clients/<id>` (delete nulls profile.clientId, no cascade)
- `GET|POST /api/templates` · `PUT|DELETE /api/templates/<id>`

## Backups / Audit / Maintenance
- `GET /api/backups?profileId=`
- `POST /api/backups/restore` — `{backupPath|backupId, profileId?}`
- `GET /api/audit?limit=100`
- `POST /api/maintenance/reap`

## Cookies / Proxy / v1 aliases
Unchanged from 1.1.0 (`/api/profiles/<id>/cookies*`, `/api/proxy/test`, `/api/v1/profile/*`).
