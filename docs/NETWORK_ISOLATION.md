# Multiprofile Linux — Network Isolation Specification

Version: 1.6.0  
Status: Production Grade Network Isolation  

---

## 1. Network Architecture & Egress Control

Each browser profile maintains its own explicit network topology defined by a `NetworkConfig` model. Profiles never share network configuration unless explicitly pointed to the same gateway.

```
Profile A (DIRECT)         ──> Host Network Stack  ──> Host Egress IP
Profile B (HTTP Proxy)     ──> HTTP Tunnel         ──> Proxy Egress IP
Profile C (SOCKS5 Proxy)   ──> SOCKS5 Tunnel       ──> Proxy Egress IP
Profile D (Dead Proxy)     ──X [BLOCKED AT LAUNCH] ──X (No Egress / Host IP Protected)
```

---

## 2. Network Modes

The platform supports 5 discrete egress modes (`NetworkMode`):

1. **`DIRECT`**: Profile routes traffic directly through the host network interface. No proxy flags are applied.
2. **`HTTP_PROXY`**: Chrome is configured with `--proxy-server=http://<host>:<port>`. Egress HTTP/HTTPS traffic traverses the proxy tunnel.
3. **`HTTPS_PROXY`**: Chrome connects via a TLS-wrapped proxy tunnel.
4. **`SOCKS5`**: Chrome is configured with `--proxy-server=socks5://<host>:<port>`. Full TCP connection multiplexing.
5. **`REMOTE_GATEWAY`**: Resolves an egress route through an entry in `gateways.json` (such as a managed proxy or custom cloud exit point).

---

## 3. Pre-Launch Health Gate & Fail-Closed Semantics

### 3.1 The Fail-Closed Invariant
A primary failure mode of multi-profile tools is **silent fallback**: when a proxy server drops offline or credentials expire, the browser falls back to the host interface, silently exposing the operator's real residential or datacenter IP address.

Multiprofile Linux enforces a strict **fail-closed invariant**:
> If a profile is configured with a proxy or gateway, a health probe must verify reachability before the Chromium process is spawned. If the check fails, launch is **ABORTED**.

### 3.2 Pre-Launch Workflow (`check_proxy_before_launch`)
1. **Inspection**: Checks if `network_config.needs_proxy_check()` is `True`. (Skipped for `DIRECT`).
2. **TCP Reachability**: Performs a non-blocking TCP socket connection against `<host>:<port>` with a configurable timeout (default: 8.0s).
3. **HTTP Egress Verification**: For HTTP/HTTPS proxies, fetches egress identity metadata (IP, country, ASN) via an isolated request before launching the browser.
4. **Policy Enforcement**:
   - `ProxyFallback.BLOCK` (**Default**): If probe fails, returns `allowed=False` with an explicit reason. Chrome process is never created.
   - `ProxyFallback.WARN`: Allows launch with an explicit warning (opt-in only).
   - `ProxyFallback.ALLOW`: Allows launch silently (opt-in only).

---

## 4. Leak Prevention Policies

### 4.1 DNS Leak Prevention (`DNSPolicy`)
- **Policy**: `DNSPolicy.PROXY_RESOLVED`
- **Mechanism**: When a proxy is configured, the launcher injects:
  ```bash
  --host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost"
  ```
  This forces Chromium to delegate domain name resolution directly to the remote proxy server rather than issuing local UDP DNS queries through the host's `/etc/resolv.conf`.

### 4.2 IPv6 Leak Prevention (`IPv6Policy`)
- **Policy**: `IPv6Policy.DISABLED`
- **Mechanism**: Many proxy configurations only tunnel IPv4 traffic. If dual-stack IPv6 is enabled on the host, Chromium might attempt direct IPv6 connections, bypassing the IPv4 proxy. Passing:
  ```bash
  --disable-ipv6
  ```
  disables IPv6 networking within the profile's network stack.

### 4.3 WebRTC IP Leak Prevention (`WebRTCPolicy`)
- **Browser Flags**:
  ```bash
  --force-webrtc-ip-handling-policy=disable_non_proxied_udp
  --enforce-webrtc-ip-permission-check
  ```
- **Extension Traps (`evasion.js`)**:
  - Traps `RTCIceCandidate.prototype.candidate` and `address` to sanitize host-revealing ICE candidates.
  - Sanitizes `RTCSessionDescription.prototype.sdp` to replace local network candidates.
  - Intercepts `RTCPeerConnection.prototype.getStats` to strip local adapter IP disclosures.

---

## 5. Credential Security in Transit & Process Table

Passing proxy credentials in CLI arguments (e.g., `--proxy-server=http://user:pass@host:port`) is insecure on Linux because credentials are visible to any user or monitoring tool via `/proc/<pid>/cmdline` or `ps aux`.

Multiprofile Linux protects credentials through two layers:
1. **CLI Flag Sanitization**: `ProxyConfig.chrome_proxy_server()` returns only `<scheme>://<host>:<port>`. Usernames and passwords are never included.
2. **Extension Authentication**: During profile launch, `_prepare_extension()` copies the extension template to the profile's dedicated directory and writes credentials to `extension/proxy.json`. The extension's background service listens to `chrome.webRequest.onAuthRequired` and supplies authentication headers dynamically inside the browser.

---

## 7. 1.6.0 Additions: Honest Network Diagnostics Surface

### 7.1 `POST /api/profiles/<id>/network/test` — real measurements only
The diagnostics endpoint returns a fixed, honest shape:

```json
{
  "profileId": "…", "mode": "DIRECT",
  "health": "HEALTHY | DEGRADED | DOWN",
  "egressIp": "… (measured) | null",
  "error": "… (real error) | null",
  "country": "… | null", "countryCode": "… | null",
  "timezone": "… | null", "dns": {…} | null,
  "ipv6": {…} | null, "latencyMs": 0, "lastTest": "…"
}
```

Rules, enforced by tests (`tests/test_integration_v16.py`):
- `health` is exactly one of `HEALTHY | DEGRADED | DOWN`.
- A non-`HEALTHY` result **must** carry the real `error` (e.g.
  `proxy unreachable (http://u:***@10.0.0.1:8080): [Errno 1] Operation not permitted`
  — note the credential is masked as `***` in the message).
- A `HEALTHY` result **must** carry a measured `egressIp`.
- `egressIp` is never a placeholder (`0.0.0.0`, `N/A`); the suite parses it as
  a real IP literal.
- The upstream geolocation provider (ip-api) may return HTTP 403; that
  surfaces as `DOWN` + the real error — still honest, never faked.

### 7.2 Per-profile diagnostics feed the consistency engine
The last diagnostic's country/timezone feed the consistency engine's `geo`
domain (`GET /api/profiles/<id>/consistency`). Until a diagnostic has run,
`geo` reports `WARNING` ("insufficient data") rather than guessing.

---

## 8. Verification & Automated Testing

The network isolation layer is validated in `tests/test_network.py` and `tests/test_backup_and_isolation.py`:
- `test_direct_mode_default`: Verifies direct mode needs no check and launches immediately.
- `test_block_on_dead_proxy`: Verifies that a closed TCP socket triggers immediate pre-launch abort.
- `test_warn_on_dead_proxy_allows_launch`: Verifies that explicit `WARN` fallback is honored.
- `test_proxy_credentials_not_in_flags`: Verifies that `chrome_network_flags()` contains no passwords.
- `test_dns_proxy_resolved_flag`: Verifies `--host-resolver-rules` emission.
- `test_ipv6_disabled_flag`: Verifies `--disable-ipv6` emission.
- `test_webrtc_proxy_only_flag`: Verifies WebRTC UDP restriction flags.
