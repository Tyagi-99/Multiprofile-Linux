# Multiprofile Linux

[![Version](https://img.shields.io/badge/version-1.5.0-blue.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Ubuntu-orange.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)]()
[![Tests](https://img.shields.io/badge/tests-202%20passed-brightgreen.svg)](tests/)

A robust, enterprise-grade, clean-room native Linux platform for managing isolated browser profiles. Designed for software verification, testing laboratories, automation engineers, and multi-tenant environments requiring real per-profile network isolation, storage segregation, and deterministic hardware identities.

> **Clean-room architecture**: 100% native Linux. No Wine. No proprietary binaries. No automated anti-bot evasion exploits. Built on legitimate isolation primitives, deterministic configuration models, and standard Chrome DevTools Protocol (CDP) interfaces.

---

## Key Capabilities (v1.5.0)

### 1. Robust Network Isolation & Fail-Closed Safety
- **Explicit Route Topology**: Every profile defines its own egress path via `NetworkConfig` (`DIRECT`, `HTTP_PROXY`, `HTTPS_PROXY`, `SOCKS5`, or `REMOTE_GATEWAY`).
- **Pre-Launch Health Gate**: `ProxyHealthManager` probes TCP socket connectivity and verifies HTTP egress before spawning Chromium.
- **Fail-Closed by Default (`ProxyFallback.BLOCK`)**: Dead, timed-out, or unreachable proxies **abort launch immediately**. Chromium is never spawned against a failed route, eliminating silent fallback to the operator's real IP.
- **Leak Elimination Policies**:
  - **DNS**: Forces remote resolution via `--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost"`.
  - **IPv6**: Disables dual-stack bypass via `--disable-ipv6`.
  - **WebRTC**: Restricts UDP egress via `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` alongside DOM candidate traps.
- **Credential Protection**: Passwords are never placed in CLI arguments (`ps aux` safe) and are dynamically authenticated via `chrome.webRequest.onAuthRequired`.

### 2. Dual-Tier Identity & Fingerprint Consistency
- **Deterministic Identity Model**: Profile hardware and screen properties are deterministically derived from a stable, persistent `seed`. Identical hardware parameters are maintained across restarts and reboots.
- **HTTP Wire / DOM Synchronization**: Employs CDP `Network.setUserAgentOverride` with structured Client Hints (`userAgentMetadata`). Initial HTTP request headers (`Sec-CH-UA`, `Sec-CH-UA-Platform`, etc.) strictly match JavaScript DOM properties.
- **Hardware-Aware GPU Probing**: Probes `/dev/dri/renderD*` and NVIDIA devices at runtime. Uses ANGLE hardware acceleration when present and SwiftShader software rendering when headless, eliminating contradictory `--disable-gpu` flags.
- **Complete Browser Surface**: Desktop profiles provide standard Chrome PDF plugins (`navigator.plugins.length === 3`), realistic `window.chrome` objects (`loadTimes`, `csi`, `app`, `runtime`), and stable seeded media device IDs.

### 3. Edge-to-Edge Window Tiling
- **Pure-Math Grid Calculator**: Partitions the detected display work area (`_NET_WORKAREA`) into balanced grids (e.g. 2×2 for 4 profiles, 3×3 for 9 profiles, 5×4 for 20 profiles).
- **Zero Gap & Zero Margin**: Remainder pixels are distributed across columns and rows to ensure 100% work-area coverage with zero overlapping geometry.
- **X11 / Wayland Bridge**: Launches with `--ozone-platform=x11` so Chromium's native window handles strictly respect CDP `Browser.setWindowBounds`.

### 4. Encrypted Backups & Manifest V2
- **AES-256-GCM Encryption**: Optional authenticated backup encryption (`.mp_enc`) with PBKDF2-HMAC-SHA256 key derivation (600,000 iterations, 32-byte salt).
- **Plaintext Backwards Compatibility**: Automatically detects and restores legacy plaintext `.zip` archives.
- **Manifest V2**: Every archive includes structured metadata detailing format version, profile IDs, client IDs, timestamps, and SHA-256 checksums while never logging or storing passphrases.

### 5. Multi-Mode Profile Cloning
- **`config_only`**: Duplicates settings, network configuration, and fingerprint targets with a newly generated seed.
- **`config_and_cookies`**: Clones configuration plus authentication session state into the new profile.
- **`full_session`**: Full filesystem clone of `user-data/` with complete local storage and preferences.

### 6. Decoupled Remote Gateway Abstraction
- **Abstract Provider Interface**: `RemoteNetworkProvider` separates network routing from account credentials.
- **Custom Gateways**: Active support for self-hosted or commercial HTTP/SOCKS5 proxy pools.
- **Gateways Storage**: Persisted in `~/.local/share/multiprofile/gateways.json` with strict POSIX `0600` permissions and automated password masking across all API responses.
- **Clear Cloud Boundaries**: Transparent status declarations for cloud providers (see [docs/REMOTE_GATEWAYS.md](docs/REMOTE_GATEWAYS.md)). GitHub PAT tokens are strictly decoupled from browser network egress.

---

## Architectural Topology

```
+───────────────────────────────────────────────────────────────────────+
|                         Web SPA / Desktop UI                          |
|             (Vanilla JS, Tailwind CSS, Local Storage Token)           |
+───────────────────────────────────────────────────────────────────────+
                                   │
                            HTTP / REST API
                                   ▼
+───────────────────────────────────────────────────────────────────────+
|                     Flask API Server (Port 8765)                      |
|  - Token Auth & Rate Limiting                                         |
|  - Profile CRUD & Bulk Cloning (config_only, cookies, full_session)   |
|  - Gateway CRUD & Password Masking                                    |
|  - Profile Health & Observability Engine                              |
|  - Plaintext / AES-256-GCM Backup & Restore                          |
+───────────────────────────────────────────────────────────────────────+
        │                                       │
        ▼                                       ▼
+───────────────────────+           +───────────────────────────────────+
| Job Queue & Concurrency|           | Storage & Persistence (XDG)       |
| - In-process Deque    |           | ~/.local/share/multiprofile/      |
| - Semaphore Gating    |           |   ├── profiles.json (RLock)       |
| - Slot Tile Assignment|           |   ├── gateways.json (chmod 0600)  |
+───────────────────────+           |   ├── clients.json                |
        │                           |   ├── templates.json              |
        ▼                           |   ├── cookies/<id>.json           |
+───────────────────────────────────+   ├── backups/<id>/               |
| Browser Launcher & Lifecycle      |   └── chrome-profiles/<id>/       |
| 1. NetworkConfig Resolution       | ~/.cache/multiprofile/            |
| 2. ProxyHealthManager (Fail-Close)|   └── runtime.json                |
| 3. Hardware GPU Probe (/dev/dri)  +───────────────────────────────────+
| 4. Sandbox Args & Ozone X11 Flags                                      
| 5. Extension Prep (proxy.json auth)                                    
| 6. Chromium Spawn (127.0.0.1:<port>)                                  
| 7. CDP Identity Alignment (setUserAgentOverride / Client Hints)        
| 8. Window Tiling (CDP setWindowBounds)                                
+───────────────────────────────────────────────────────────────────────+
```

---

## Documentation

Comprehensive architecture and engineering specifications:
- **[System Architecture](docs/ARCHITECTURE.md)**: Detailed component breakdown, threading, and storage design.
- **[Network Isolation](docs/NETWORK_ISOLATION.md)**: Fail-closed safety gates, proxy probing, and DNS/IPv6/WebRTC leak policies.
- **[Fingerprint Model](docs/FINGERPRINT_MODEL.md)**: Deterministic seed generation, dual-tier CDP synchronization, and hardware probing.
- **[Remote Gateways](docs/REMOTE_GATEWAYS.md)**: Gateway abstractions, honest provider status declarations, and manual cloud setup guides.
- **[REST API Reference](docs/API.md)**: Complete endpoint documentation.

---

## Quick Start

### Prerequisites
- Linux (Ubuntu 20.04+, Debian 11+, Fedora, Arch)
- Python 3.11+
- System Chromium or Google Chrome installed
- Optional: `uv` package manager (recommended)

### Installation

```bash
# Clone the repository
git clone https://github.com/Tyagi-99/Multiprofile-Linux.git
cd Multiprofile-Linux

# Setup virtual environment and dependencies using uv
uv venv
source .venv/bin/activate
uv pip install -e ".[all]"

# Alternatively using standard pip
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

### Running the Application

```bash
# Start API backend and Web UI (default: http://127.0.0.1:8765)
python3 -m multiprofile --server-only

# Or launch with native desktop GTK window (requires pywebview)
python3 -m multiprofile
```

---

## Configuration & Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `MAX_CONCURRENT_PROFILES` | `20` | Maximum simultaneous Chromium processes |
| `MULTIPROFILE_API_TOKEN` | unset | If set, mandates `Authorization: Bearer <token>` on API endpoints |
| `MULTIPROFILE_PROXY_CHECK_TIMEOUT` | `8` | Timeout in seconds for pre-launch proxy reachability probes |
| `MULTIPROFILE_PROXY_CHECK_ENABLED` | `true` | Set to `false` to disable pre-launch health gates |
| `MULTIPROFILE_GPU_MODE` | `auto` | GPU mode: `auto` (probe `/dev/dri`), `angle` (force HW), or `software` (SwiftShader) |
| `MULTIPROFILE_STATIC` | auto | Override path to frontend static assets |
| `MULTIPROFILE_EXTENSION` | auto | Override path to bundled extension template |

---

## Test Suite & Validation

The test suite validates profile isolation, fail-closed network policies, credential safety, backup roundtrips, and tiling geometry:

```bash
# Run the full test suite
uv run pytest tests/ -v --tb=short

# Run network isolation tests specifically
uv run pytest tests/test_network.py -v

# Run backup, isolation, and security tests
uv run pytest tests/test_backup_and_isolation.py -v
```

---

## XDG File Paths

The platform stores all state in compliance with the XDG Base Directory Specification:
- **`~/.config/multiprofile/`**: Global configuration files.
- **`~/.local/share/multiprofile/`**:
  - `profiles.json`: Profile definitions and configuration metadata.
  - `gateways.json`: Stored gateway routes (`0600` permissions, passwords masked).
  - `clients.json`: Organization client metadata.
  - `templates.json`: Profile templates.
  - `cookies/<id>.json`: Isolated per-profile cookie jars.
  - `chrome-profiles/<id>/`: Dedicated Chromium `user-data` directory and extension copies.
  - `backups/<id>/`: Plaintext `.zip` and encrypted `.mp_enc` archives.
  - `audit.jsonl`: Append-only security audit log.
- **`~/.cache/multiprofile/`**:
  - `runtime.json`: Ephemeral process runtime tracking.

---

## Honest Technical Boundaries

1. **Linux Font Subsystem**: Linux renders fonts using FreeType/Fontconfig, which produces slightly different antialiasing sub-pixel geometry compared to DirectWrite (Windows) or CoreText (macOS).
2. **Audio Hardware Latency**: AudioContext buffer timing and sample rate clocks reflect the host ALSA/PulseAudio/PipeWire drivers.
3. **OS-Level Network Sockets**: TCP window sizes, initial congestion windows, and SYN packet options remain characteristic of the host Linux kernel.
4. **Cloud Egress**: Automated provisioning of cloud VMs (e.g. Azure, WireGuard) requires external infrastructure orchestration. See [docs/REMOTE_GATEWAYS.md](docs/REMOTE_GATEWAYS.md).

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
