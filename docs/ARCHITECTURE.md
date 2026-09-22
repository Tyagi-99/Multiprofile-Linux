# Multiprofile Linux — Architecture Specification

Version: 1.5.0  
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
|  Job Queue & Executor  |           | Storage & Persistence Layer      |
|  - In-process deque    |           | - ~/.local/share/multiprofile/   |
|  - Concurrency Gate    |           |   ├── profiles.json (RLock)      |
|    (Semaphore)         |           |   ├── gateways.json (chmod 0600) |
|  - Slot & Window Tile  |           |   ├── clients.json               |
|    Assignment          |           |   ├── templates.json             |
+------------------------+           |   ├── cookies/<id>.json          |
        |                            |   ├── backups/<id>/              |
        v                            |   └── chrome-profiles/<id>/      |
+------------------------------------+   - ~/.cache/multiprofile/       |
| Browser Launcher & Lifecycle       |     └── runtime.json             |
| 1. NetworkConfig resolution        +----------------------------------+
| 2. ProxyHealthManager pre-check                                        
|    (Fail-closed BLOCK)                                                 
| 3. Hardware GPU probe (/dev/dri)                                       
| 4. Sandbox args & Ozone X11 flags                                      
| 5. Extension prep (inline config + proxy.json auth)                    
| 6. Chromium process spawn (CDP on 127.0.0.1:<port>)                   
| 7. CDP Identity Alignment (setUserAgentOverride / Client Hints)        
| 8. Window Tiling (CDP setWindowBounds)                                 
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

### 3.6 Window Tiling & Geometry (`multiprofile.browser.grid`)
- **Pure Math Calculation**: `GridCalculator` partitions the detected work area without margins or gaps when configured for edge-to-edge layout.
- **X11 / Wayland Bridge**: Forces `--ozone-platform=x11` so Chromium's native window handles honor CDP `Browser.setWindowBounds` across Linux compositors.
- **Idempotence**: Re-computing grid plans for $N$ profiles yields invariant geometry without coordinate drift or overlapping windows.
