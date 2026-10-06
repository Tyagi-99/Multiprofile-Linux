# SDKs — Python and Node

Two small clients for the local API. Four operations, plus the one that
matters: **attaching an automation framework to a profile the product
launched.**

Neither has dependencies — the Python one uses the standard library, the Node
one uses global `fetch` (Node 18+) — so either can be vendored into a scraper
without adding a package.

```
sdk/python/multiprofile_client/__init__.py
sdk/node/multiprofile.mjs
```

---

## 1. The thing to get right

```python
from multiprofile_client import Client

mp = Client()                            # http://127.0.0.1:8765
profile = mp.create("research-1")
session = mp.launch(profile["id"])
url = mp.wait_for_cdp(session)           # not time.sleep()

# Playwright
browser = playwright.chromium.connect_over_cdp(url)
```

```js
import { Client } from './multiprofile.mjs';

const mp = new Client();
const profile = await mp.create('research-1');
const session = await mp.launch(profile.id);
const url = await mp.waitForCdp(session);

// Puppeteer
const browser = await puppeteer.connect({ browserURL: url });
// Playwright
const browser = await chromium.connectOverCDP(url);
```

**Connect over CDP; do not launch Chromium yourself.** A browser you launch
through Playwright has none of the product's work in it — no fingerprint, no
route, no font view, no timezone, no worker identity. It is a plain Chromium
wearing your real identity. Every finding in
[`02_AUDIT_REPORT`](02_AUDIT_REPORT.md) is about making the *launched* profile
coherent, and attaching is how you get that.

`wait_for_cdp` / `waitForCdp` exists because `launch()` returns once the
process is up, which is not quite the moment DevTools starts accepting
connections. A framework that connects too early gets a confusing refusal.

---

## 2. Operations

| | Python | Node |
|---|---|---|
| list | `mp.list(limit=, cursor=)` | `mp.list({ limit, cursor })` |
| detail | `mp.get(id)` | `mp.get(id)` |
| create | `mp.create(name, **fields)` | `mp.create(name, fields)` |
| launch | `mp.launch(id, url=, direct=)` | `mp.launch(id, { url, direct })` |
| stop | `mp.stop(id)` | `mp.stop(id)` |
| delete | `mp.delete(id, permanent=)` | `mp.delete(id, { permanent })` |
| health | `mp.health()` | `mp.health()` |
| diagnose | `mp.doctor()` | `mp.doctor()` |

`create` passes extra fields straight through, so anything the API accepts
works: `proxyType`, `proxyHost`, `proxyPort`, `proxyUser`, `proxyPass`,
`userAgentId`, `deviceId`, `timezone`, `startUrl`, `tags`. **Unknown fields
are rejected** rather than stored, so a typo is a `400` and not a setting that
silently does nothing (F8).

**Call `doctor()` once at the start of a run.** It reports a blocked route or
an unsupported engine *before* a launch fails, which is cheaper than
diagnosing it from a refusal.

---

## 3. Errors — a refusal is not a failure

| | means |
|---|---|
| `LaunchBlocked` | the product refused, on purpose, with a reason |
| `ApiError` | the API answered with an error (`.status`, `.body`) |
| `ScopeError` | a valid token without the scope the route needs (403) |
| `MultiprofileError` | the server could not be reached at all |

`LaunchBlocked` is its own type because a fail-closed route decision is the
**expected** outcome of a configuration, not a bug: a dead proxy on a profile
set to `BLOCK`, a pool with no members, or an authenticated SOCKS5 proxy
Chromium cannot use (F56). A caller usually wants to handle that differently
from the server being down.

```python
from multiprofile_client import LaunchBlocked

try:
    session = mp.launch(profile_id)
except LaunchBlocked as e:
    print(f"not launching: {e.reason}")   # fix the config, do not retry
```

> **The API used to answer `500` for every refused launch**, which meant
> monitoring saw server errors during normal operation and retry logic
> retried something that could never succeed. It now answers **409** for a
> decision, **404** for a missing profile and **500** only for a genuine
> fault. A test derives the refusal list from the launcher's own source, so a
> new refusal code cannot be added without being classified.

---

## 4. Read `session.notes`

The launcher's own record comes back on the session, and it is where the
things worth knowing appear:

```
Engine: 151.0.7922.173 (system, digest e3fa1bd7c6c5)
identity claim 'windows' is EXPERIMENTAL on this host: fonts contradict it
IDENTITY FAULT [font_claim_unbacked] windows fonts with no metric-compatible …
```

A run that ignores these is a run that will not know why an account was
challenged.

---

## 5. Auth

Loopback needs no token. A non-loopback caller needs one on every route
except `GET /api/health`:

```python
mp = Client("http://10.0.0.5:8765", token="mp_...")
```

```js
const mp = new Client({ baseUrl: 'http://10.0.0.5:8765', token: 'mp_...' });
```

**There is no TLS.** A token sent to a non-loopback interface travels in
cleartext — tunnel it over SSH or WireGuard rather than exposing it on a LAN.
Mint scoped, revocable tokens with `POST /api/tokens`; see
[`API.md`](API.md) §Auth.

---

## 6. Tests

```bash
pytest tests/test_sdk_python.py          # against a real server
pytest tests/test_sdk_node.py            # starts a server, runs node --test
```

Both run against `create_app()`'s real routes rather than a mock, because a
mocked SDK test proves the SDK agrees with the author's idea of the API —
which is the thing most likely to be wrong. The Node suite is driven from
`pytest` so it is covered by the same invocation as everything else, rather
than needing a separate CI job someone forgets to add. It skips when Node is
absent.

---

*Companions:* [`API.md`](API.md) for the routes · [`QUICKSTART`](QUICKSTART.md)
for the console workflow · [`10_RELEASE_READINESS`](10_RELEASE_READINESS.md)
