# Multiprofile Native API (1.1.0)

Base URL: `http://127.0.0.1:8765`

## Health
- `GET /api/health` — version, chrome_path, static_ok, extension_ok, profiles_writable, count

## Profiles
- `GET /api/profiles` — list
- `POST /api/profiles` — create (name, userAgent, userAgentId, isMobile, proxyType, customProxy, timezone, language, webrtcMode, seed, notes, startUrl, …)
- `PUT /api/profiles/<id>` — update
- `DELETE /api/profiles/<id>` — delete
- `POST /api/profiles/<id>/launch` — body `{url?, direct?}` — apply custom proxy unless `direct=true`
- `POST /api/profiles/<id>/stop`
- `POST /api/profiles/<id>/duplicate` — new id + new seed
- `GET /api/profiles/<id>/fingerprint` — report + warnings
- `POST /api/profiles/<id>/open-folder`

## Cookies
- `GET /api/profiles/<id>/cookies`
- `PUT /api/profiles/<id>/cookies` — body list or `{cookies:[…]}`
- `POST /api/profiles/<id>/cookies/import` — raw text, JSON list, or `{cookies:…}` (Chrome JSON or Netscape)
- `GET /api/profiles/<id>/cookies/export` — download JSON

## Proxy
- `POST /api/proxy/test` — body `customProxy` **or** `{protocol,host,port,username,password}`  
  Returns `{ok,success,latencyMs,ip,egress_ip,error,…}`

## AdsPower-style thin aliases
- `POST /api/v1/profile/start` `{profile_id}` → launch
- `POST /api/v1/profile/stop` `{profile_id}`
- `GET /api/v1/profile/list`

## Misc (SPA stubs)
- `GET /api/user-agents`
- `GET|POST /api/github-accounts` (stub)
- `GET /api/donation-config`
- `GET /api/ads`
