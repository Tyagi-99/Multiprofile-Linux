# Multiprofile Linux — Network Isolation Specification

Version: 1.9.0  
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

> **On "fail-closed".** The default is fail-closed and that default is load
> bearing: `ProxyFallback.BLOCK` aborts the launch and the Chrome process is
> never created. But `WARN` and `ALLOW` are real, opt-in, per-profile
> settings, and **`WARN` launches the profile on the host IP** with a log
> warning. So "this product is fail-closed" is true of the default and not of
> every configuration, and a profile that must never touch the host
> connection has to actually be on `BLOCK`. `/api/health` and the profile
> detail endpoint report the effective mode.

## 3b. Routes this engine cannot use

### Authenticated SOCKS5 — refused at launch

**Chromium has never implemented the SOCKS5 username/password method
(RFC 1929).** It offers only `NO AUTHENTICATION REQUIRED` in its greeting, so
a SOCKS5 proxy that requires credentials refuses the handshake and every
connection dies before a request is made.

Measured on Chromium 151 against a local SOCKS5 proxy requiring credentials,
with the profile configured exactly as an operator would:

| observation | value |
|---|---|
| launch reported | **`ok: true`** |
| requests reaching the destination | **0** |
| SOCKS connections completed | **0** |
| SOCKS handshakes the proxy rejected | **40** |
| launch note | `proxy auth=armed` |

It failed **closed** — the destination saw nothing, so there was no host-IP
leak, which is the one good thing about it. But it failed **silently from the
operator's side**: a green launch, a note claiming proxy auth was armed, and a
browser that could not load a page. That note refers to the CDP
`Fetch.authRequired` handler, which answers an HTTP `407` from an HTTP proxy
and has nothing to do with SOCKS5.

This matters more than its size suggests, because **authenticated SOCKS5 is
what most commercial proxy providers sell** — it is a configuration an
operator is likely to try first.

The launch is now **refused with that explanation**, consistent with
`ProxyFallback.BLOCK` and F17: a launch that reports success and cannot browse
is worse than a refused launch with a reason. The workarounds are an HTTP or
HTTPS proxy with the same credentials, or an IP-authorised SOCKS5 endpoint
with no username.

The check is deliberately **not** inside the proxy health gate.
`MULTIPROFILE_PROXY_CHECK_ENABLED=0` answers "is this proxy up right now";
whether the engine can use this *kind* of route at all is a different
question, and putting the check inside the health gate meant disabling the
probe also disabled it. An IP-authorised SOCKS5 proxy is unaffected, and
there is a test for that so the gate cannot grow into blocking SOCKS5
generally.

---

## 4. Leak Prevention Policies

### 4.1 DNS Leak Prevention (`DNSPolicy`)
- **Policy**: `DNSPolicy.PROXY_RESOLVED`
- **Mechanism**: **No flag.** Chrome hands the hostname to an HTTP or SOCKS5
  proxy and lets the proxy resolve it, so a proxied profile emits no DNS of
  its own. `DNSPolicy.PROXY_RESOLVED` is a *description of that behaviour*,
  not something the launcher enforces.

> **Corrected 5 October 2026.** This section previously said the launcher
> injects `--host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE localhost"`. It
> does not, and it must not. Measured on Chrome 151 with `strace` counting
> `connect`/`sendto` to port 53, alongside a local proxy counting requests:
>
> | configuration | host DNS queries | proxy hits | page loaded |
> |---|---:|---:|---|
> | no proxy | 76 | 0 | no |
> | proxy, no resolver rule | **0** | 2 | **yes** |
> | proxy + the resolver rule | 0 | 0 | no |
>
> `MAP *` catches every hostname *including the proxy's own*, and
> `EXCLUDE localhost` spares only the literal name `localhost`, not
> `127.0.0.1`. The rule therefore stopped the profile reaching its proxy at
> all — every proxied profile was unable to browse — while the DNS it was
> meant to contain was already contained without it.
>
> `tests/test_dns_containment.py` asserts the flag is **absent** for every
> proxy scheme, and `test_proxy_resolved_policy_is_a_description_not_a_flag`
> pins the distinction.

### 4.2 IPv6 Leak Prevention (`IPv6Policy`)
- **Policy**: `IPv6Policy.DISABLED` for every proxied profile; IPv6 is left
  alone for a direct profile, which has no proxy for v6 traffic to go around.
- **Mechanism**: **No flag.** Chromium hands an IPv6 destination to the proxy
  instead of connecting to it, so a proxied profile does not reach one
  directly. `IPv6Policy` is a *description of that behaviour*, exactly like
  `DNSPolicy.PROXY_RESOLVED`.

> **Corrected 6 October 2026 (F57).** This section said the launcher passes
> `--disable-ipv6` and that this "disables IPv6 networking within the
> profile's network stack". **Chromium 151 ignores that flag**, and the claim
> was the *mechanism*, not a detail — so the document advertised a protection
> that did not exist.
>
> Measured against a destination bound to the host's own **global** IPv6
> address — not `::1`, so Chrome's loopback bypass cannot be the explanation —
> with a deliberately nonexistent flag as a control:
>
> | configuration | IPv6 destination reached |
> |---|---|
> | stock, no flag | yes |
> | stock, `--disable-ipv6` | **yes** |
> | stock, a flag that does not exist | yes |
>
> The flag has exactly the effect of a flag that is not there. It is removed:
> a no-op that advertises a protection is worse than no flag at all.
>
> **The protection itself holds**, and by the same route as the DNS one.
> Measured with a proxied profile and the same global-IPv6 destination, using
> an IPv4-only proxy so the two outcomes are distinguishable:
>
> ```
> proxy received CONNECT for   [<the host's global v6>]:PORT
> destination direct hits      0
> ```
>
> Chromium asked the proxy. `tests/test_route_leaks_live.py` pins that, and a
> second test asserts the dead flag stays absent.
>
> **This is the fourth instance of one pattern in this codebase** — F49's
> `switch-all-direct`, F50's `prefer_sandbox`, the `--host-resolver-rules`
> correction, and now this. Each time the *behaviour* claim was true and the
> *mechanism* cited was fiction, and each time the fiction was in a document
> an operator would rely on.

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
2. **CDP Authentication**: During profile launch, `cdp_inject.IdentityInjector` arms `Fetch.enable{handleAuthRequests: true}` and answers `Fetch.authRequired` with the credentials, which it holds **in memory only** for the lifetime of the browser. `ProxyAuth.__repr__` is overridden so a traceback cannot print the password either.

   This replaced an extension that was never loaded. The launcher used to stage a per-profile copy of the extension and write the decrypted password to `extension/proxy.json` — with a bare `write_text`, so the umask decided the mode and the file landed **0644 world-readable**, undoing the keychain encryption it had just used (audit finding F4). Google Chrome refuses `--load-extension` outright, so on Chrome the file was written and nothing ever read it; on Chromium the extension loaded and ran `evasion.js` a second time (F2). The staging step, the flags and both files are gone: the credential that must not leak is now never written to disk at all.

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

## 7b. Measured egress — `tests/test_route_egress_live.py`

Everything in §8 tests the **decision**: whether a launch is blocked, whether
a flag is emitted. Until 5 October 2026 nothing tested the **egress**, which
is what the parity roadmap asks for: *"this gate should be judged by measured
traffic, not only the app's health-check response."*

`tests/_route_lab.py` gives each hop its own loopback source address, so the
destination's `remote_addr` names the hop that reached it:

| route | destination observed | result |
|---|---|---|
| direct | `127.0.0.1` | ✅ |
| HTTP proxy | `127.0.0.2` | ✅ |
| SOCKS5 (IP-authorised) | `127.0.0.3` | ✅ |
| HTTP proxy + auth | `127.0.0.4` | ✅ (407 issued and answered) |
| SOCKS5 + auth | — | **refused at launch**, see §3b |

Each proxied route is also asserted **never** to have reached the destination
from `127.0.0.1`: "the proxy was used" and "the host address was never used"
are different claims and only the second is a leak test. And the SOCKS5 proxy
records the address type, which makes the DNS claim in §4.1 observable rather
than asserted — Chromium hands it the *hostname* (ATYP `0x03`), so the
profile emitted no DNS of its own.

**One lab artefact, stated plainly.** Chrome implicitly bypasses the proxy for
loopback destinations, so the fixture appends
`--proxy-bypass-list=<-loopback>` to whatever the launcher built. It
*appends*: every routing flag the product emits is still emitted. The
consequence is that these tests do not cover the bypass list itself.

### A mid-session proxy drop

The roadmap's sharpest requirement, because a pre-launch probe is
structurally unable to answer it: *"deliberately disconnect the proxy during
a session and prove what happens next. For profiles requiring a fixed route,
do not silently fall back to the host connection."*

Measured by killing the proxy with the browser still running:

| route | after the drop | verdict |
|---|---|---|
| SOCKS5 | **no request arrives at all** | ✅ |
| HTTP | one request arrives, **from the proxy's address** (`127.0.0.2`) | ✅ no host fallback |

**The host address never appears in either case**, which is the requirement.
A WebSocket attempt is also confirmed to go through the proxy rather than
around it — it is a separate code path in Chromium, not an HTTP request with
a different header.

**A limitation of the lab, stated rather than asserted around.** The lab
proxy is a dumb pipe: once it has relayed the first request it is a
bidirectional socket pair, so HTTP/1.1 keep-alive requests pass through its
logic untouched. The destination's own log shows this directly — absolute-form
paths (`http://127.0.0.1:PORT/dest?...`) that only a verbatim forward
produces. A real proxy parses every request on the connection, so "the proxy
is gone" and "the pipe is gone" are the same event there and not here.

Severing every tracked socket still left one request arriving from
`127.0.0.2` in origin form, meaning it reached the proxy's own handler after
the listener was closed. **That lab behaviour is unexplained.** It is recorded
rather than asserted around, because the security-relevant question is
answered either way: the source was the proxy, never the host. Proving
"requests stop entirely" for an HTTP proxy needs a faithful proxy, which is
outstanding.

### WebRTC, IPv6 and downloads — `tests/test_route_leaks_live.py`

Each is a separate path in Chromium, not an HTTP request wearing a different
hat, which is why the roadmap names them individually.

| path | measured | result |
|---|---|---|
| WebRTC, default | ICE gathers host candidates at all | ✅ control |
| WebRTC, `disable_non_proxied_udp` | **no routable address** in any candidate | ✅ |
| IPv6, direct profile | reaches a v6 destination | ✅ control |
| IPv6, proxied profile | **handed to the proxy**, 0 direct hits | ✅ |
| Download through a proxy | attachment fetched from `127.0.0.2` | ✅ |

Every one has a control beside it. A one-sided assertion passes on a host
where the capability is simply absent, which is the failure mode this lab
exists to avoid: the WebRTC test first proves candidates are gathered, the
IPv6 test first proves a direct profile can reach `::1`, and the download test
first proves the page itself went through the proxy — because the download
manager is a separate path from the renderer, so "the page was proxied" does
not imply "the file was".

The IPv6 tests use a destination on the host's **own global address** rather
than `::1`, because Chrome bypasses the proxy for loopback and a hit there
would prove nothing about routing. That address is read at runtime and never
written to a report, a log line or an assertion message.

**What is still outstanding.** No real third-party proxy over a real network:
no NAT, no MTU, no latency, and no provider that can fail in ways a local
process cannot. **Real-world proxy validation is therefore outstanding** and
nothing here should be read as covering it. Also not covered: a proxy whose
credentials are wrong rather than absent, and IPv6 egress *through* a
v6-capable proxy (the lab proxy is IPv4-only, which is what makes the
routing assertion above decidable).

---

## 8. Verification & Automated Testing

The network isolation layer is validated in `tests/test_network.py` and `tests/test_backup_and_isolation.py`:
- `test_direct_mode_default`: Verifies direct mode needs no check and launches immediately.
- `test_block_on_dead_proxy`: Verifies that a closed TCP socket triggers immediate pre-launch abort.
- `test_warn_on_dead_proxy_allows_launch`: Verifies that explicit `WARN` fallback is honored.
- `test_proxy_credentials_not_in_flags`: Verifies that `chrome_network_flags()` contains no passwords.
- `tests/test_dns_containment.py::test_no_resolver_rule_for_any_proxy_scheme`: verifies `--host-resolver-rules` is **not** emitted, for every proxy scheme. (This line previously cited a `test_dns_proxy_resolved_flag` asserting the opposite; no such test exists — it was replaced when the flag was removed.)
- `test_ipv6_disabled_flag`: Verifies `--disable-ipv6` emission.
- `test_webrtc_proxy_only_flag`: Verifies WebRTC UDP restriction flags.
