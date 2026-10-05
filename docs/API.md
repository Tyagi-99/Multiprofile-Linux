# Multiprofile Native API

Base: `http://127.0.0.1:8765`

## Auth

**Loopback needs no token.** This is deliberate: the console must work, and
code already running on this machine owns it anyway.

**Non-loopback callers need a bearer token** on every `/api/*` route except
`GET /api/health`. Two kinds work:

- A token minted with `POST /api/tokens` — named, scoped, revocable, hashed at rest. This is the one to use.
- The legacy single token from `MULTIPROFILE_API_TOKEN` or `$XDG_CONFIG_HOME/multiprofile/api_token`. It still works and carries every scope.

Scopes: `read`, `launch`, `write`, `admin`. `write` and `launch` each imply
`read`; `admin` implies all. A missing or bad token is **401** with an
identical body in every case; a valid token without the needed scope is
**403** with `{error:{code:"insufficient_scope", required, granted}}`.

There is **no TLS**. A token sent to a non-loopback interface travels in
cleartext — tunnel it (SSH, WireGuard) rather than exposing it on a LAN.

Writes additionally require a loopback `Host`, a matching `Origin` when one is
sent, and `Content-Type: application/json`.

## Health
- `GET /api/health` — version, chrome_ok, static_ok, extension_ok, slots, reaped

## Profiles
- `GET /api/profiles` — raw list when no query args (compat)
- `GET /api/profiles?q=&clientId=&status=&tag=&limit=&cursor=` → `{items,total,limit,next_cursor}`; `limit` max 500. Rows omit `fingerprint`.
- `GET /api/profiles/<id>` — one profile, **with** its fingerprint
- `GET /api/profiles/ids?...` — just the ids matching a filter (cap 5000), for resolving a filter to a selection
- `GET /api/profiles/capacity` → `{fleet:{stored,capacity,free}}`
- `POST /api/profiles` — create; **409 `fleet_full`** when the fleet is at capacity
- `PUT /api/profiles/<id>` — update; returns warnings
- `POST /api/profiles/<id>/duplicate` · `POST /api/profiles/<id>/clone`
- `GET /api/profiles/<id>/fingerprint`
- `POST /api/profiles/<id>/backup` — plaintext zip + sha256 under XDG `backups/`

### Creating many at once
- `POST /api/profiles/from-template` — `{templateId, name?, clientId?, count?}`. `count` is validated (**400** if not a positive integer) and bounded by the fleet capacity (**409 `fleet_full`**, carrying `stored`/`capacity`/`free`/`requested`). The batch is one transaction: all or nothing.
- `POST /api/profiles/import` — `{proxies, namePrefix?, proxyType?, tags?, clientId?, poolId?, startUrl?}`. One profile per proxy line. Bad and duplicate lines are reported **by line number** rather than failing the batch; **207** when some rows failed.

### Launching
- `POST /api/profiles/<id>/launch` — body `{url?,direct?,cdp?}`; CRITICAL consistency mismatches → **409** unless `?force=1`
- `POST /api/profiles/<id>/stop`

## Deleting, trash and restore

> **Changed:** `DELETE` no longer destroys anything by default. It used to
> remove the row and `rmtree` the profile's `chrome-profiles/<id>` directory —
> the cookies and logins — with no way back.

- `DELETE /api/profiles/<id>` — **moves the profile to the trash.** Every file is kept. **409 `profile_running`** if its browser is actually alive (a stale record does not block it).
- `DELETE /api/profiles/<id>?permanent=1` — the old behaviour, by name. `&purgeStorage=0` keeps the files.
- `GET /api/trash` → `{items,count,diskBytes,retentionDays}`; each item carries `deletedAt` and `diskBytes`
- `POST /api/profiles/<id>/restore` — back into the fleet; **409 `fleet_full`** if there is no room, since a restore is a create
- `DELETE /api/trash/<id>` — permanent, files included
- `POST /api/trash/empty`

Trashed profiles do not count against the fleet capacity. They are purged
automatically after `MULTIPROFILE_TRASH_RETENTION_DAYS` (default 30; `0`
disables), reconciled when the trash is read.

## Usage and limits
- `GET /api/usage?period=day|week|month|all` → `{total,byProfile,byClient}`; the metered quantity is **seconds spent running**
- `GET /api/usage/sessions?period=&limit=` — the individual sessions behind those totals
- `GET /api/plan` → `{plan,limits,usage,recognised}`. Each limit reports `value`, `planValue`, `source` (`plan` or `env`) and **`enforced`** — only `maxProfiles` and `maxConcurrent` are enforced; the rest are declarative and say so.

## Tokens
All of these need the `admin` scope, including the listing.

- `GET /api/tokens?includeRevoked=0` — names, scopes, `createdAt`, `lastUsedAt`, `revokedAt`. Never secrets or hashes.
- `POST /api/tokens` — `{name, scopes}` → `{token:{id,name,scopes,token}}`. **The `token` value is returned once and cannot be recovered.**
- `DELETE /api/tokens/<id>` — revoke. The row is kept so the audit trail outlives the credential.

## Account data
- `GET /api/account/export` — everything, as one JSON download. Proxy passwords and token hashes are **not** in it; the document lists the redacted field names.
- `GET /api/account/deletion-preview` — what a deletion would remove, without removing it
- `POST /api/account/delete` — `{confirm:"DELETE EVERYTHING"}`, exactly. Removes browser directories, cookie jars, the database and its WAL sidecars, the audit log and all tokens. Irreversible.

## Jobs
- `POST /api/jobs` — `{type: launch_profile|stop_profile|bulk_launch|bulk_stop, profileIds, url?, direct?, cdp?}`
- `GET /api/jobs` / `GET /api/jobs/<id>` / `POST /api/jobs/<id>/cancel`

A `bulk_launch` starts its profiles in parallel, bounded by
`MAX_CONCURRENT_PROFILES`.

## Clients / Templates
- `GET|POST /api/clients` · `PUT|DELETE /api/clients/<id>` (delete nulls `profile.clientId`, no cascade)
- `GET|POST /api/templates` · `PUT|DELETE /api/templates/<id>` — `GET` answers with a bare array, not an envelope

## Backups / Audit / Maintenance
- `GET /api/backups?profileId=`
- `POST /api/backups/restore` — `{backupPath|backupId, profileId?}`. A restore is not checked against the fleet capacity: refusing someone's own backup over a soft limit is the worse failure. The fleet may end up over its limit, and *creating* is refused until it is trimmed.
- `GET /api/audit?limit=100`
- `POST /api/maintenance/reap`

## Cookies / Proxy / v1 aliases
`/api/profiles/<id>/cookies*`, `/api/proxy/test`, `/api/v1/profile/*`.
