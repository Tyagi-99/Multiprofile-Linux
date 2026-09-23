# Multiprofile Linux — Architecture Specification

Version: 1.9.0  
Status: Production Grade Linux-Native Multi-Profile Browser Platform  

---

## 1. Overview & Core Philosophy

Multiprofile Linux is an enterprise-grade, clean-room, native Linux platform for managing isolated browser profiles. The platform prioritizes:
1. **Real Isolation over Heuristic Spoofing**: Real per-profile user-data directories, isolated cookie jars, discrete network routes, and distinct process lifecycles.
2. **Deterministic Consistency**: Browser-level configurations (HTTP headers, Client Hints, WebGL, media devices) align with OS/platform profiles without internal contradictions.
3. **Safe Failure Semantics**: Configured network routes fail closed (BLOCK). A proxy outage or misconfiguration never silently falls back to host IP egress.
4. **Clean Decoupling**: Authentication credentials (e.g., GitHub Personal Access Tokens) are strictly isolated from network egress routing.

---

## 2. High-Level Component Topology

```
+-----------------------------------------------------------------------+
|                         Web SPA / Desktop UI                          |
|               (Vanilla JS, Tailwind CSS, Local Storage Token)         |
+-----------------------------------------------------------------------+
                                  |
                           HTTP / REST API
                                  v
+-----------------------------------------------------------------------+
|                    Flask API Server (Port 8765)                       |
|   - Authentication & Token Verification                               |
|   - Profile CRUD & Bulk Cloning (Config-only, Cookies, Full Session)   |
|   - Gateway Provider Management & Password Masking                    |
|   - Health & Observability Endpoints                                  |
|   - Backup Creation (Plaintext Zip / AES-256-GCM) & Restore           |
+-----------------------------------------------------------------------+
        |                                       |
        v                                       v
+------------------------+           +----------------------------------+
|  Control Plane         |           | Storage & Persistence Layer      |
|  - SQLite WAL control  |           | - ~/.local/share/multiprofile/   |
|    DB (recover-first)  |           |   ├── profiles.json (RLock)      |
|  - Durable job queue   |           |   ├── gateways.json (chmod 0600) |
|    (survives restart)  |           |   ├── clients.json               |
|  - Worker pool +       |           |   ├── templates.json             |
|    bounded launches    |           |   ├── cookies/<id>.json          |
|  - Profile state       |           |   ├── backups/<id>/              |
|    machine (validated) |           |   ├── chrome-profiles/<id>/      |
|  - Browser supervisor  |           |   └── control.db (SQLite WAL:    |
|    (crash watchdog)    |           |       profiles_runtime + jobs)   |
|  - Idle parking,       |           | - ~/.cache/multiprofile/         |
|    /proc metrics,      |           |     └── runtime.json             |
|    audit JSONL         |           +----------------------------------+
+------------------------+
        |
        v
+------------------------------------------------------------------------+
| Browser Launcher & Lifecycle (data plane, driven by the control plane)  |
| 1. NetworkConfig resolution                                            |
| 2. ProxyHealthManager pre-check (Fail-closed BLOCK)                     |
| 3. Hardware GPU probe (/dev/dri)                                        |
| 4. Sandbox args & Ozone X11 flags                                       |
| 5. Extension prep (inline config + proxy.json auth)                     |
| 6. Chromium process spawn (CDP on 127.0.0.1:<port>)                     |
| 7. CDP Identity Alignment (setUserAgentOverride / Client Hints)         |
| 8. Window Tiling (CDP setWindowBounds)                                 |
| 9. Supervisor watches the PID; on crash -> bounded restart + re-tile    |
+------------------------------------------------------------------------+
```

---

## 3. Subsystem Architecture

### 3.1 Network Isolation Layer (`multiprofile.network`)
- **`NetworkConfig`**: Structured egress definition stored per profile or computed from legacy fields. Contains `mode` (`DIRECT`, `HTTP_PROXY`, `HTTPS_PROXY`, `SOCKS5`, `REMOTE_GATEWAY`), `proxy` (`ProxyConfig`), `dns_policy`, `ipv6_policy`, `webrtc_policy`, and `proxy_fallback`.
- **`ProxyHealthManager`**: Probes TCP reachability and verifies HTTP egress prior to launching Chrome.
- **Fail-Closed Gate**: If a configured proxy fails reachability, `check_proxy_before_launch` returns `allowed=False` when `proxy_fallback=ProxyFallback.BLOCK` (the default). Silent fallback to DIRECT is strictly forbidden.
- **Credentials Isolation**: Passwords never enter Chrome CLI flags; credentials reside in the extension's `proxy.json` and are handled via `chrome.webRequest.onAuthRequired`.

### 3.2 Gateway Abstraction Layer (`multiprofile.gateway`)
- **`RemoteNetworkProvider`**: Abstract base class declaring `get_route()`, `health()`, and `provision()`.
- **`CustomGatewayProvider`**: Production-ready wrapper for user-provided SOCKS5/HTTP/HTTPS proxy endpoints.
- **`AzureGatewayProvider`**: **PROVIDER SCAFFOLD / NOT ACTIVE**. Manages endpoint health when host/port are provided manually, but automated VM/VPN provisioning is a scaffold. Does not claim automated cloud egress.
- **`WireGuardGatewayProvider`**: **PROVIDER SCAFFOLD / NOT ACTIVE**. Documents network namespace requirements (`ip netns`).

### 3.3 Browser Identity & CDP Layer (`multiprofile.browser`)
- **GPU Probing**: Probes `/dev/dri/renderD*` and NVIDIA devices. If available, configures ANGLE with hardware acceleration. If headless/unavailable, enables SwiftShader software rendering. Never simultaneously passes `--disable-gpu` while reporting hardware WebGL in JavaScript.
- **CDP Identity Override (`cdp_identity.py`)**: Connects to CDP on `127.0.0.1:<port>` and invokes `Network.setUserAgentOverride` with structured `userAgentMetadata` (brands, fullVersionList, architecture, bitness, platform, platformVersion). This guarantees HTTP headers (`Sec-CH-UA-*`) match JavaScript `navigator.userAgentData`.
- **Localhost CDP Security**: Remote debugging strictly binds to `127.0.0.1`, never `0.0.0.0`.

### 3.4 Storage & Secrets Architecture (`multiprofile.storage`)
- **Permissions**: Sensitive files (`gateways.json`, `.mp_enc` backups) are created with `0600` permissions.
- **Masking**: `load_gateways(safe=True)` and `ProxyConfig.masked_str()` strip passwords before returning data to the API or logs.
- **Concurrency**: `profiles.json` mutations are protected by threading locks (`threading.RLock`) and atomic file replacement.

### 3.5 Backup & Restore Subsystem (`multiprofile.backup`)
- **Plaintext Format**: ZIP archive with `manifest.json`, `cookies/`, and selective `user-data/` (excluding volatile cache trees: `GPUCache`, `Code Cache`, `Cache`).
- **Encrypted Format**: AES-256-GCM authenticated encryption. Key derived via PBKDF2-HMAC-SHA256 (600,000 iterations, 32-byte salt). Ciphertext bundle stores version, salt, IV, tag, and ciphertext (`.mp_enc`).
- **Manifest V2**: Every backup includes metadata detailing `version`, `createdAt`, `profileIds`, `clientIds`, checksums (`zip_sha256`), and encryption parameters.

### 3.6 Device Preset System (`multiprofile.devices`)
- **`DevicePreset`**: a coherent identity bundle per real device — User-Agent,
  Client Hints, viewport/screen geometry, DPR, touch, plus an explicit
  capability matrix declaring what a Linux Chromium host cannot reproduce
  (iOS-class engine, macOS-class GPU/fonts, WebGL renderer honesty).
- **`DevicePresetValidator`**: static checks (schema, UA format, Client-Hints
  coherence, geometry sanity) plus a genuine CDP round-trip against a real
  Chromium binary: geometry is applied through DevTools and the *page* reads
  it back. Statuses: `VALIDATED` / `PARTIAL` (honest capability limits) /
  `PENDING` (no binary) / `UNSUPPORTED`. See `docs/DEVICE_PRESETS.md`.
- **Launcher hook**: with `deviceId`, the launcher applies the preset's
  geometry/touch/UA via CDP; an unknown `deviceId` records a
  `device preset not found` note rather than crashing.

### 3.7 Fingerprint Inspector & Consistency Engine (`multiprofile.fingerprint`)
- **Inspector components**: `GET /api/profiles/<id>/fingerprint` reports
  per-component `expected` values (Navigator, Client Hints, Screen, Touch,
  …). When a device preset is attached, the Screen component pins exactly to
  the preset viewport × DPR (e.g. Pixel 9 → `411x923@2.626`). Note: the
  legacy top-level `fingerprint` block may still reflect the stored legacy
  desktop fingerprint; the inspector `components` are the preset-coherent view.
- **Consistency engine** (`multiprofile.consistency`): read-only per-domain
  checks (`geo`, `device`, `platform`, `mobile`, `browser`, `gpu_webgl`),
  each `PASS | WARNING | ERROR` with a human message; overall status is the
  worst domain. See `docs/PROFILE_CONSISTENCY.md`.

---

### 3.8 Window Tiling & Geometry (`multiprofile.browser.grid`)
- **Pure Math Calculation**: `GridCalculator` partitions the detected work area without margins or gaps when configured for edge-to-edge layout.
- **X11 / Wayland Bridge**: Forces `--ozone-platform=x11` so Chromium's native window handles honor CDP `Browser.setWindowBounds` across Linux compositors.
- **Idempotence**: Re-computing grid plans for $N$ profiles yields invariant geometry without coordinate drift or overlapping windows.

### 3.9 Control Plane: Durable Jobs, State Machine, Supervisor (v1.7.0)

**Control plane vs data plane.** The control plane is everything that decides
*what should be running* (SQLite WAL control DB, durable job queue, worker
pool, state machine, supervisor, parking). The data plane is the actual
Chromium processes plus the launcher that spawns them. The control plane
never assumes the data plane agrees with it — every supervisor pass
reconciles observed PIDs against recorded state.

- **Control DB** (`storage/db.py`): SQLite in WAL mode,
  `~/.local/share/multiprofile/control.db` (override:
  `MULTIPROFILE_CONTROL_DB`). Tables: `profiles_runtime` (state, pid,
  cdp_port, heartbeat) and `jobs` (type, status, attempts, payload).
- **`recover()`** (`storage/db.py`): runs FIRST at every boot, before any
  worker starts. Jobs stuck `running` are re-queued with `attempts + 1`
  (exhausted ones → `failed`, so poison jobs can't spin forever). Profiles
  stuck in transitional states (`starting`/`stopping`) can no longer be
  observed — their browser state is unknown — so they move to `error` with
  an audit note. An explicit restart/recover action is required to leave
  `error`.
- **Durable queue + workers** (`jobs/queue.py`, `jobs/workers.py`,
  `jobs/handlers.py`): `enqueue()` persists a job; a `ThreadPoolExecutor`
  pool claims jobs (`running`) and dispatches by type (`launch`, `stop`,
  `restart`, `park`, `health_check`, `consistency_check`) with a bounded
  launch semaphore. The legacy in-process queue is preserved unchanged at
  `jobs/legacy.py` with the same import surface.
- **State machine** (`storage/state.py`): `stopped → starting → running →
  stopping → stopped`, plus `idle`, `parked`, `error`. Every transition is
  validated (`validate_transition`); invalid ones raise instead of silently
  corrupting state. Every transition writes an audit note.
- **Supervisor** (`browser/supervisor.py`): crash watchdog thread. Each
  `interval_s` (default 10s) it checks every `running` profile's PID. On
  death: audit `supervisor_browser_died` → state `error` → bounded restart
  (max 3 attempts, exponential backoff) via the launcher → on success state
  `running` + full grid re-tile via CDP (`tile_running()`). On exhaustion
  the profile stays `error` with the reason recorded — the supervisor never
  spins forever. See `docs/SUPERVISOR.md`.
- **Startup wiring** (`api/server.py::create_app`): `recover()` →
  `start_workers()` → `start_supervisor()` (the last guarded by
  `supervisor_enabled` config). All three are fail-safe: the app boots even
  if the control DB can't open (degrades with a warning, no durable jobs),
  and a broken supervisor module can never take down the API. Tests set
  `MULTIPROFILE_NO_BACKGROUND=1` to keep daemon threads out of pytest.

### 3.10 Idle Parking, Metrics & Audit (v1.7.0)

- **Idle parking** (`parking.py`, config `[parking]`): profiles idle longer
  than `parking_idle_timeout_s` (default 1800s) may be parked — graceful
  stop, profile data kept, state `parked`. A running non-park job blocks
  parking; the in-flight `park` job itself doesn't self-block.
- **Health staggering** (config `[health]`): health checks are jittered and
  bounded (`health_max_concurrent`, default 4) so N profiles don't fire at
  once. See `docs/SCALING.md`.
- **Metrics** (`metrics.py`, config `[metrics]`, `api/metrics_routes.py`):
  point-in-time CPU/mem sampling from `/proc` (no new dependencies);
  samples cached at most 5s (`metrics_cache_ttl_s`). `GET
  /api/metrics/summary` returns per-running-profile samples plus process
  totals. Honest by construction: `PARTIAL` is reported when a PID is gone,
  never fabricated.
- **Audit + JSONL log** (`profile_log.py`): append-only JSONL profile log and
  an audit trail (`supervisor_browser_died`, `job_finished`,
  `profile_state_recovered`, …) surfaced in the console's Jobs/Audit views.

### 3.11 API Hardening (v1.7.0)

- **Pagination** (`api/pagination.py`): cursor-based keyset pagination on
  list endpoints. Response: `{"items", "next_cursor", "limit", "total"}`.
  Cursors are opaque base64url of `(sort_key, id)`; malformed cursors are
  rejected with 400 `invalid_input`.
- **Validation** (`api/validation.py`): invalid input is rejected with
  `400 {"error": {"code": "invalid_input", ...}}` instead of tracebacks or
  silent coercion.
- **Auth** (`api/auth.py`): localhost (`127.0.0.1`/`::1`) stays frictionless
  by deliberate tradeoff; remote (non-localhost) requests require a bearer
  token (`MULTIPROFILE_API_TOKEN` or token file). With no token configured,
  remote `/api/*` requests are rejected. The module docstring carries the
  honest assessment of what this does and doesn't protect against.
- **Live events** (`api/event_bus.py`, `api/events.py`, `api/sse.py`):
  in-process pub/sub bus emitting `profile.status` and `job.progress`;
  `GET /api/events/stream` re-emits them as Server-Sent Events
  (`text/event-stream`, `: ping` keep-alive, optional `max_events=N` for
  finite reads/tests). The console uses SSE with a polling fallback.
  **Not done:** multi-process event broadcast — the bus is per-process;
  see §5.

### 3.12 Network Namespaces — pointer (v1.7.0)

- `gateway/netns.py` is an **EXPERIMENTAL / NOT ACTIVE** scaffold for Linux
  network namespaces (default off, no launcher wiring). Roadmap, risks, and
  the activation checklist live in `docs/NETNS_ROADMAP.md`. Manual proxy
  routing via `custom` remains the production path.

## 4. Console (v1.7.0 restyle)

Vanilla JS SPA (`src/static/`): sidebar navigation, dense profile table with
cursor pagination, creation wizard, device library, Jobs and Audit views,
SSE live updates with polling fallback. The Export button downloads the
currently listed profiles as JSON with proxy passwords stripped.

## 5. Known Limitations (honest)

- **Multi-process event broadcast is NOT implemented.** The event bus is
  in-process; two API server processes do not share events. A deployment
  needing this must add a broker (Redis pub/sub) — the SSE envelope is
  already broker-agnostic.
- **Gateway providers (Azure, WireGuard) remain SCAFFOLD / NOT ACTIVE.**
  `provision()` returns `not_implemented`. Netns is experimental and off.
- **Single-host SQLite control plane.** WAL mode handles the app's own
  concurrency, but it is not a multi-host store. See `docs/SCALING.md` for
  the 100/500-profile guidance and when to add gateway hosts.
- **Auth is localhost-trust.** Behind a reverse proxy everything looks
  remote; deployments must terminate TLS and forward correctly. See
  `api/auth.py`.
- **No anti-bot evasion.** The platform does not attempt to be
  "undetectable"; device presets declare what a Linux Chromium host cannot
  reproduce.
