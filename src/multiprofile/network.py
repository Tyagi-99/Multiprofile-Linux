"""Network isolation layer for Multiprofile Native.

Every profile has an explicit NetworkConfig that determines its egress path.
The ProxyHealthManager probes the route before any Chrome launch.

DESIGN INVARIANT:
    A proxy that fails the pre-launch health check BLOCKS the launch.
    Chrome is never spawned against a misconfigured or dead route.
    Silent fallback to DIRECT is NEVER permitted when a proxy is configured.
    The only exception is when the profile explicitly sets
    proxy_fallback=ProxyFallback.ALLOW  (opt-in per-profile, default BLOCK).
"""
from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("multiprofile.network")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class NetworkMode(str, Enum):
    """Explicit egress mode for a profile."""
    DIRECT = "DIRECT"
    HTTP_PROXY = "HTTP_PROXY"
    HTTPS_PROXY = "HTTPS_PROXY"
    SOCKS5 = "SOCKS5"
    REMOTE_GATEWAY = "REMOTE_GATEWAY"


class ProxyFallback(str, Enum):
    """What to do when the configured proxy fails the pre-launch health check."""
    BLOCK = "BLOCK"    # Default: abort launch, never expose host IP
    WARN = "WARN"      # Allow launch but surface a warning in the UI
    ALLOW = "ALLOW"    # Allow launch silently (not recommended)


class DNSPolicy(str, Enum):
    """DNS resolution policy for this profile."""
    DIRECT = "DIRECT"         # System resolver (default for DIRECT network mode)
    PROXY_RESOLVED = "PROXY_RESOLVED"  # DNS resolved by proxy server
    BLOCK_UNSAFE = "BLOCK_UNSAFE"     # Block all but loopback when proxy dead


class IPv6Policy(str, Enum):
    """IPv6 reachability policy."""
    ENABLED = "ENABLED"       # Allow IPv6 (system default)
    DISABLED = "DISABLED"     # Pass --disable-ipv6 to Chrome
    PROXY_ONLY = "PROXY_ONLY" # Rely on proxy to strip IPv6 (same as DISABLED for now)


class WebRTCPolicy(str, Enum):
    """WebRTC IP exposure policy."""
    DEFAULT = "default"
    PROXY_ONLY = "disable_non_proxied_udp"
    DISABLED = "disable"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ProxyConfig:
    """Parsed proxy endpoint — no raw credentials in logs."""
    scheme: str = "http"        # http | https | socks5 | socks4
    host: str = ""
    port: int = 3128
    username: str | None = None
    password: str | None = None

    def masked_str(self) -> str:
        """Safe string for logs — never exposes password."""
        auth = f"{self.username}:***@" if self.username else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def chrome_proxy_server(self) -> str:
        """Value for Chrome --proxy-server flag (no credentials)."""
        if self.scheme in ("socks5", "socks5h"):
            return f"socks5://{self.host}:{self.port}"
        if self.scheme == "socks4":
            return f"socks4://{self.host}:{self.port}"
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            # password intentionally omitted — masked in API responses
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProxyConfig":
        return cls(
            scheme=d.get("scheme") or "http",
            host=d.get("host") or "",
            port=int(d.get("port") or 3128),
            username=d.get("username") or None,
            password=d.get("password") or None,
        )


@dataclass
class NetworkConfig:
    """Complete network configuration for one profile."""
    mode: NetworkMode = NetworkMode.DIRECT
    proxy: ProxyConfig | None = None
    gateway_id: str | None = None       # ID in gateways.json for REMOTE_GATEWAY mode
    dns_policy: DNSPolicy = DNSPolicy.DIRECT
    ipv6_policy: IPv6Policy = IPv6Policy.ENABLED
    webrtc_policy: WebRTCPolicy = WebRTCPolicy.DEFAULT
    proxy_fallback: ProxyFallback = ProxyFallback.BLOCK  # NEVER change default to ALLOW
    # Set when a route was REQUIRED but could not be resolved -- today only by
    # a proxy pool that has no member to give this profile. It exists because
    # needs_proxy_check() is False when proxy is None: a config carrying a
    # proxy mode with no proxy would sail through the gate and launch on the
    # host IP. An unresolved route must block, so it is stated here rather
    # than encoded as an empty proxy. Never clear this to "try anyway".
    blocked_reason: str | None = None

    def needs_proxy_check(self) -> bool:
        """Return True if a proxy health check must pass before launch."""
        return self.mode in (
            NetworkMode.HTTP_PROXY,
            NetworkMode.HTTPS_PROXY,
            NetworkMode.SOCKS5,
            NetworkMode.REMOTE_GATEWAY,
        ) and self.proxy is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "proxy": self.proxy.to_dict() if self.proxy else None,
            "gatewayId": self.gateway_id,
            "dnsPolicy": self.dns_policy.value,
            "ipv6Policy": self.ipv6_policy.value,
            "webrtcPolicy": self.webrtc_policy.value,
            "proxyFallback": self.proxy_fallback.value,
            "blockedReason": self.blocked_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "NetworkConfig":
        if not d:
            return cls()
        try:
            mode = NetworkMode(d.get("mode") or "DIRECT")
        except ValueError:
            mode = NetworkMode.DIRECT
        proxy = None
        if d.get("proxy"):
            proxy = ProxyConfig.from_dict(d["proxy"])
        try:
            dns = DNSPolicy(d.get("dnsPolicy") or "DIRECT")
        except ValueError:
            dns = DNSPolicy.DIRECT
        try:
            ipv6 = IPv6Policy(d.get("ipv6Policy") or "ENABLED")
        except ValueError:
            ipv6 = IPv6Policy.ENABLED
        try:
            webrtc = WebRTCPolicy(d.get("webrtcPolicy") or "default")
        except ValueError:
            webrtc = WebRTCPolicy.DEFAULT
        try:
            fallback = ProxyFallback(d.get("proxyFallback") or "BLOCK")
        except ValueError:
            fallback = ProxyFallback.BLOCK
        return cls(
            mode=mode,
            proxy=proxy,
            gateway_id=d.get("gatewayId"),
            dns_policy=dns,
            ipv6_policy=ipv6,
            webrtc_policy=webrtc,
            proxy_fallback=fallback,
        )

    @classmethod
    def from_legacy_profile(cls, profile: dict[str, Any]) -> "NetworkConfig":
        """Migrate legacy scattered proxy fields into a NetworkConfig.

        Legacy fields: proxyType, customProxy, proxyHost, proxyPort,
                       proxyUser, proxyPass, webrtcMode.
        This is called on read so existing profiles automatically gain
        a structured NetworkConfig without any migration step.
        """
        ptype = (profile.get("proxyType") or "none").lower().strip()
        custom = (profile.get("customProxy") or "").strip()

        if ptype in ("none", "", "direct") and not custom:
            return cls(
                mode=NetworkMode.DIRECT,
                webrtc_policy=_map_webrtc(profile.get("webrtcMode") or "default"),
            )

        if ptype == "github":
            # GitHub token ≠ network route — treat as DIRECT until a real gateway is configured
            return cls(
                mode=NetworkMode.DIRECT,
                webrtc_policy=_map_webrtc(profile.get("webrtcMode") or "default"),
            )

        # Parse proxy from the legacy fields
        proxy: ProxyConfig | None = None
        if custom:
            from .proxy_util import parse_proxy as _parse
            parsed = _parse(custom, ptype if ptype not in ("none", "direct") else "custom")
            if parsed:
                proxy = ProxyConfig(
                    scheme=parsed.get("scheme") or "http",
                    host=parsed.get("host") or "",
                    port=int(parsed.get("port") or 3128),
                    username=parsed.get("username") or None,
                    password=parsed.get("password") or None,
                )
        elif profile.get("proxyHost") and profile.get("proxyPort"):
            scheme = "socks5" if "socks" in ptype else "http"
            proxy = ProxyConfig(
                scheme=scheme,
                host=str(profile["proxyHost"]),
                port=int(profile["proxyPort"]),
                username=profile.get("proxyUser") or None,
                password=profile.get("proxyPass") or None,
            )

        if proxy is None:
            return cls(
                mode=NetworkMode.DIRECT,
                webrtc_policy=_map_webrtc(profile.get("webrtcMode") or "default"),
            )

        if "socks" in ptype or (proxy and proxy.scheme == "socks5"):
            mode = NetworkMode.SOCKS5
        elif "https" in ptype:
            mode = NetworkMode.HTTPS_PROXY
        else:
            mode = NetworkMode.HTTP_PROXY

        return cls(
            mode=mode,
            proxy=proxy,
            dns_policy=DNSPolicy.PROXY_RESOLVED,
            ipv6_policy=IPv6Policy.DISABLED,
            webrtc_policy=_map_webrtc(profile.get("webrtcMode") or "default"),
            proxy_fallback=ProxyFallback.BLOCK,
        )


def _map_webrtc(mode: str) -> WebRTCPolicy:
    m = (mode or "").lower().strip()
    if m == "disable_non_proxied_udp":
        return WebRTCPolicy.PROXY_ONLY
    if m == "disable":
        return WebRTCPolicy.DISABLED
    return WebRTCPolicy.DEFAULT


# ---------------------------------------------------------------------------
# Health result
# ---------------------------------------------------------------------------

@dataclass
class ProxyHealthResult:
    ok: bool
    latency_ms: int | None = None
    tcp_ms: int | None = None
    egress_ip: str | None = None
    country: str | None = None
    asn: str | None = None
    error: str | None = None
    checked_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latencyMs": self.latency_ms,
            "tcpMs": self.tcp_ms,
            "egressIp": self.egress_ip,
            "country": self.country,
            "asn": self.asn,
            "error": self.error,
            "checkedAt": self.checked_at,
            "status": "HEALTHY" if self.ok else "FAILED",
        }


# ---------------------------------------------------------------------------
# ProxyHealthManager
# ---------------------------------------------------------------------------

class ProxyHealthManager:
    """Stateless helper that probes a proxy and returns a health result.

    Deliberately stateless — callers cache the result if desired.
    Never logs credentials.
    """

    DEFAULT_TIMEOUT = 8.0

    def check(
        self,
        proxy: ProxyConfig,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> ProxyHealthResult:
        """Full probe: TCP connect → HTTP tunnel → egress IP lookup."""
        t0 = time.monotonic()

        # Step 1: TCP connect to proxy host:port
        tcp_ok, tcp_err = self._tcp_connect(proxy.host, proxy.port, timeout)
        tcp_ms = int((time.monotonic() - t0) * 1000)
        if not tcp_ok:
            return ProxyHealthResult(
                ok=False,
                tcp_ms=tcp_ms,
                latency_ms=tcp_ms,
                error=f"TCP connect failed: {tcp_err}",
            )

        # Step 2: Egress IP probe (skip for SOCKS5 — stdlib has no native socks support)
        egress_ip: str | None = None
        country: str | None = None
        asn: str | None = None
        http_error: str | None = None

        if proxy.scheme in ("http", "https", ""):
            egress_ip, country, asn, http_error = self._http_egress_check(proxy, timeout)

        latency_ms = int((time.monotonic() - t0) * 1000)
        return ProxyHealthResult(
            ok=True,
            tcp_ms=tcp_ms,
            latency_ms=latency_ms,
            egress_ip=egress_ip,
            country=country,
            asn=asn,
            error=http_error,  # TCP succeeded even if HTTP check failed
        )

    @staticmethod
    def _tcp_connect(host: str, port: int, timeout: float) -> tuple[bool, str | None]:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
            return True, None
        except OSError as e:
            return False, str(e)

    @staticmethod
    def _http_egress_check(
        proxy: ProxyConfig,
        timeout: float,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Return (egress_ip, country, asn, error)."""
        try:
            if proxy.username and proxy.password:
                proxy_url = (
                    f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
                )
            elif proxy.username:
                proxy_url = f"http://{proxy.username}@{proxy.host}:{proxy.port}"
            else:
                proxy_url = f"http://{proxy.host}:{proxy.port}"

            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            opener = urllib.request.build_opener(handler)
            # ip-api.com returns JSON with ip, country, org — free, no key required
            req = urllib.request.Request(
                "http://ip-api.com/json/?fields=query,country,org",
                headers={"User-Agent": "multiprofile-native/1.4.0"},
            )
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "ignore")
                try:
                    data = json.loads(body)
                    ip = data.get("query") or None
                    country = data.get("country") or None
                    asn = data.get("org") or None
                    return ip, country, asn, None
                except Exception:
                    return body.strip()[:64] or None, None, None, None
        except Exception as e:
            return None, None, None, str(e)


# Module-level singleton for convenience
_health_manager = ProxyHealthManager()


def check_proxy(proxy: ProxyConfig, *, timeout: float = 8.0) -> ProxyHealthResult:
    """Convenience wrapper around ProxyHealthManager.check()."""
    return _health_manager.check(proxy, timeout=timeout)


def check_proxy_before_launch(
    network_config: NetworkConfig,
    *,
    timeout: float = 8.0,
) -> tuple[bool, str | None, ProxyHealthResult | None]:
    """Pre-launch safety gate.

    Returns:
        (allowed, error_message, health_result)

    Rules:
    - DIRECT mode → always allowed (no check).
    - Proxy configured → check; on failure respect ProxyFallback policy.
    - Default policy (BLOCK) → never allow Chrome to launch with a dead proxy.
    """
    # A route that was required and could not be resolved blocks before any
    # probe runs. Checked first: DIRECT-looking configs reach here too, and
    # this must not be mistaken for "no proxy configured, carry on".
    if network_config.blocked_reason:
        log.warning("pre-launch blocked: %s", network_config.blocked_reason)
        return False, network_config.blocked_reason, None

    if not network_config.needs_proxy_check():
        return True, None, None

    assert network_config.proxy is not None
    log.info(
        "pre-launch proxy check: %s",
        network_config.proxy.masked_str(),
    )
    result = check_proxy(network_config.proxy, timeout=timeout)
    log.info("proxy health: ok=%s latency=%sms egress=%s error=%s",
             result.ok, result.latency_ms, result.egress_ip, result.error)

    if result.ok:
        return True, None, result

    # Proxy failed
    fallback = network_config.proxy_fallback
    if fallback == ProxyFallback.BLOCK:
        msg = (
            f"Proxy {network_config.proxy.masked_str()} failed pre-launch health check "
            f"({result.error or 'unreachable'}). "
            "Launch blocked to prevent host IP exposure. "
            "Use ?direct=1 to override (DIRECT mode) or fix the proxy."
        )
        return False, msg, result
    elif fallback == ProxyFallback.WARN:
        log.warning("Proxy failed but proxyFallback=WARN — launching with host IP. This may expose your real IP.")
        return True, "proxy_failed_warn", result
    else:  # ALLOW
        log.warning("Proxy failed and proxyFallback=ALLOW — launching with host IP.")
        return True, None, result


# ---------------------------------------------------------------------------
# Chrome flag builders
# ---------------------------------------------------------------------------

def chrome_network_flags(network_config: NetworkConfig) -> list[str]:
    """Return Chrome CLI flags for a given NetworkConfig.

    Proxy credentials are NOT included here — the extension handles
    proxy auth via onAuthRequired.
    """
    flags: list[str] = []

    # Proxy server
    if network_config.proxy and network_config.mode != NetworkMode.DIRECT:
        flags.append(f"--proxy-server={network_config.proxy.chrome_proxy_server()}")

    # DNS — when proxy is active, tell Chrome to resolve DNS remotely too
    if network_config.dns_policy == DNSPolicy.PROXY_RESOLVED and network_config.proxy:
        # This forces all DNS to go through the proxy
        flags.append("--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost")

    # IPv6
    if network_config.ipv6_policy in (IPv6Policy.DISABLED, IPv6Policy.PROXY_ONLY):
        flags.append("--disable-ipv6")

    # WebRTC
    if network_config.webrtc_policy == WebRTCPolicy.PROXY_ONLY:
        flags.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        flags.append("--enforce-webrtc-ip-permission-check")
    elif network_config.webrtc_policy == WebRTCPolicy.DISABLED:
        flags.append("--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
        flags.append("--enforce-webrtc-ip-permission-check")
        flags.append("--disable-webrtc")

    return flags


_POOL_SCHEME_MODES = {
    "http": NetworkMode.HTTP_PROXY,
    "https": NetworkMode.HTTPS_PROXY,
    "socks5": NetworkMode.SOCKS5,
    "socks5h": NetworkMode.SOCKS5,
}


def network_config_for_profile(profile: dict[str, Any]) -> NetworkConfig:
    """Read NetworkConfig for a profile, migrating legacy fields if needed.

    A profile carrying ``poolId`` draws its route from that shared pool and
    the pool wins over the profile's own proxy fields: the operator put the
    profile in a pool precisely so the pool decides. If the pool cannot
    supply a member the result is a BLOCKED config, never DIRECT -- falling
    back to the host IP is the one outcome a proxy pool must never produce.
    """
    pool_id = profile.get("poolId") or profile.get("pool_id")
    if pool_id:
        return _network_config_from_pool(str(pool_id), profile)

    nc_raw = profile.get("networkConfig")
    if nc_raw and isinstance(nc_raw, dict) and nc_raw.get("mode"):
        return NetworkConfig.from_dict(nc_raw)
    # Legacy migration: build from scattered fields
    return NetworkConfig.from_legacy_profile(profile)


def _network_config_from_pool(pool_id: str, profile: dict[str, Any]) -> NetworkConfig:
    from .storage.pools import choose_member, get_pool, resolve_member

    webrtc = _map_webrtc(profile.get("webrtcMode") or "default")

    def blocked(reason: str) -> NetworkConfig:
        return NetworkConfig(mode=NetworkMode.DIRECT, webrtc_policy=webrtc, blocked_reason=reason)

    pool = get_pool(pool_id)
    if pool is None:
        return blocked(f"Proxy pool '{pool_id}' no longer exists. Pick another pool or clear it.")

    member, reason = choose_member(pool, str(profile.get("id") or ""))
    if member is None:
        return blocked(reason)

    endpoint = resolve_member(pool, member)
    if endpoint is None:
        return blocked(
            f"Pool '{pool.get('label')}' member "
            f"'{member.get('label') or member.get('id')}' has no usable endpoint."
        )

    scheme = endpoint["scheme"]
    return NetworkConfig(
        mode=_POOL_SCHEME_MODES.get(scheme, NetworkMode.SOCKS5),
        proxy=ProxyConfig(
            scheme=scheme,
            host=endpoint["host"],
            port=int(endpoint["port"]),
            username=endpoint.get("username"),
            password=endpoint.get("password"),
        ),
        webrtc_policy=webrtc,
        # Pools inherit the fail-closed default. A dead pool proxy blocks.
        proxy_fallback=ProxyFallback.BLOCK,
    )


# ---------------------------------------------------------------------------
# Network diagnostics (Worker 3 — ADDITIVE; no existing behavior changed)
# ---------------------------------------------------------------------------

# Health values reported by run_diagnostics()
HEALTH_HEALTHY = "HEALTHY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_DOWN = "DOWN"

_DNS_PASS = "PASS"
_DNS_FAIL = "FAIL"
_DNS_NOT_RUN = "NOT_RUN"

_IPV6_SUPPORTED = "SUPPORTED"
_IPV6_UNAVAILABLE = "UNAVAILABLE"
_IPV6_NOT_RUN = "NOT_RUN"

# Single IP-echo + geo endpoint used for egress measurement.
# Returns: query (ip), country, countryCode, timezone. Free, no key required.
_ECHO_GEO_URL = "https://ip-api.com/json/?fields=status,message,query,country,countryCode,timezone"
_DNS_TEST_HOST = "example.com"
_IPV6_PROBE = ("2606:4700:4700::1111", 443)  # Cloudflare DNS over a well-known IPv6 address


@dataclass
class NetworkDiagnostics:
    """Measured network route state for a profile.

    All fields are REAL measurements or explicit NOT_RUN/None with reasons.
    Placeholder IPs, guessed geolocation, and silent fallbacks are forbidden.
    """
    profile_id: str = ""
    mode: str = NetworkMode.DIRECT.value
    health: str = HEALTH_DOWN  # HEALTHY | DEGRADED | DOWN
    egress_ip: str | None = None
    country: str | None = None
    country_code: str | None = None
    timezone: str | None = None
    latency_ms: float | None = None
    dns: dict[str, Any] = field(default_factory=dict)
    ipv6: dict[str, Any] = field(default_factory=dict)
    last_test: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        # No credentials are ever stored on this dataclass, so nothing to mask.
        return {
            "profileId": self.profile_id,
            "mode": self.mode,
            "health": self.health,
            "egressIp": self.egress_ip,
            "country": self.country,
            "countryCode": self.country_code,
            "timezone": self.timezone,
            "latencyMs": self.latency_ms,
            "dns": dict(self.dns),
            "ipv6": dict(self.ipv6),
            "lastTest": self.last_test,
            "error": self.error,
        }


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _validate_ip_literal(value: str | None) -> str | None:
    """Return the value if it is a real IP literal, else None (never fake one)."""
    if not value:
        return None
    try:
        import ipaddress
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _proxy_opener(proxy: ProxyConfig) -> urllib.request.OpenerDirector:
    if proxy.username and proxy.password:
        proxy_url = f"http://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
    elif proxy.username:
        proxy_url = f"http://{proxy.username}@{proxy.host}:{proxy.port}"
    else:
        proxy_url = f"http://{proxy.host}:{proxy.port}"
    handlers: list[Any] = [urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})]
    if proxy.username and proxy.password:
        pwd_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        pwd_mgr.add_password(None, f"{proxy.host}:{proxy.port}", proxy.username, proxy.password)
        handlers.append(urllib.request.ProxyBasicAuthHandler(pwd_mgr))
    return urllib.request.build_opener(*handlers)


def _tcp_connect_ms(host: str, port: int, timeout: float) -> tuple[float | None, str | None]:
    """Return (connect latency ms, error)."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - t0) * 1000.0, None
    except OSError as e:
        return None, str(e)


def _fetch_echo_geo(opener: urllib.request.OpenerDirector, timeout: float) -> dict[str, Any]:
    """Fetch egress IP + geo through the opener's route. Raises on any failure."""
    req = urllib.request.Request(
        _ECHO_GEO_URL,
        headers={"User-Agent": "multiprofile-native/1.7.0"},
    )
    with opener.open(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "ignore")
    data = json.loads(body)
    if data.get("status") != "success":
        raise RuntimeError(f"geo lookup failed: {data.get('message') or 'unknown'}")
    return data


def _measure_dns(net_config: NetworkConfig, timeout: float) -> dict[str, Any]:
    """DNS check: resolve a test hostname through the effective route.

    Honest about what stdlib can observe: through a proxy we cannot see
    remote DNS, so we report NOT_RUN with a reason instead of guessing.
    """
    if net_config.mode == NetworkMode.DIRECT:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            try:
                _, _, addrs = socket.gethostbyname_ex(_DNS_TEST_HOST)
            except OSError as e:
                return {"status": _DNS_FAIL, "detail": f"system DNS resolution failed: {e}"}
            ip = addrs[0] if addrs else None
            return {
                "status": _DNS_PASS if ip else _DNS_FAIL,
                "detail": f"resolved {_DNS_TEST_HOST} via system resolver to {ip}"
                if ip else f"system resolver returned no addresses for {_DNS_TEST_HOST}",
            }
        finally:
            socket.setdefaulttimeout(old_timeout)
    if net_config.dns_policy == DNSPolicy.PROXY_RESOLVED:
        return {
            "status": _DNS_NOT_RUN,
            "detail": "DNS is delegated to the proxy; remote resolution cannot be "
                      "observed from here. Chrome enforces it via --host-resolver-rules.",
        }
    return {
        "status": _DNS_NOT_RUN,
        "detail": "DNS through the configured route cannot be verified with the "
                  "stdlib HTTP stack; no claim is made.",
    }


def _measure_ipv6(net_config: NetworkConfig, timeout: float) -> dict[str, Any]:
    """IPv6 check: attempt a real IPv6 TCP connection. Never guessed."""
    if net_config.ipv6_policy in (IPv6Policy.DISABLED, IPv6Policy.PROXY_ONLY):
        return {
            "status": _IPV6_UNAVAILABLE,
            "detail": f"IPv6 suppressed by profile policy {net_config.ipv6_policy.value}",
        }
    if net_config.mode != NetworkMode.DIRECT:
        return {
            "status": _IPV6_NOT_RUN,
            "detail": "IPv6 through the configured proxy cannot be probed from here; "
                      "Chrome enforces the profile's ipv6 policy at launch.",
        }
    host, port = _IPV6_PROBE
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port, 0, 0))
        return {
            "status": _IPV6_SUPPORTED,
            "detail": f"IPv6 TCP connect to [{host}]:{port} succeeded",
        }
    except OSError as e:
        return {
            "status": _IPV6_UNAVAILABLE,
            "detail": f"IPv6 TCP connect to [{host}]:{port} failed: {e}",
        }


def run_diagnostics(
    net_config: NetworkConfig,
    *,
    profile_id: str = "",
    timeout: float = 10.0,
) -> NetworkDiagnostics:
    """Run REAL network measurements for a profile's configured route.

    Fail-closed: any route failure yields health DOWN with an error and NO
    egress data. The reported `mode` always reflects the configured mode —
    a failed proxy NEVER flips the report to DIRECT (no silent fallback).
    """
    mode = net_config.mode.value
    diag = NetworkDiagnostics(
        profile_id=profile_id,
        mode=mode,
        health=HEALTH_DOWN,
        last_test=_iso_now(),
    )
    timeout = max(1.0, min(float(timeout or 10.0), 60.0))

    # ---------------------------------------------------------------- direct
    # DIRECT is allowed ONLY when explicitly configured (mode == DIRECT).
    # A proxy mode with no proxy endpoint is misconfigured -> fail closed below.
    if net_config.mode == NetworkMode.DIRECT:
        t0 = time.monotonic()
        try:
            # Default opener honors the host's egress configuration (env
            # proxies on sandboxed hosts); this measures the host's real
            # egress path, matching what a DIRECT Chrome launch would use.
            opener = urllib.request.build_opener()
            data = _fetch_echo_geo(opener, timeout)
        except Exception as e:
            diag.error = f"direct route failed: {e}"
            diag.dns = _measure_dns(net_config, timeout)
            diag.ipv6 = _measure_ipv6(net_config, timeout)
            return diag
        diag.egress_ip = _validate_ip_literal(data.get("query"))
        diag.country = data.get("country") or None
        diag.country_code = data.get("countryCode") or None
        diag.timezone = data.get("timezone") or None
        diag.latency_ms = round((time.monotonic() - t0) * 1000.0, 1)
        diag.dns = _measure_dns(net_config, timeout)
        diag.ipv6 = _measure_ipv6(net_config, timeout)
        if diag.egress_ip:
            diag.health = HEALTH_HEALTHY
        else:
            diag.error = "direct route answered but returned no valid egress IP"
        return diag

    # ------------------------------------------------------- proxy required
    proxy = net_config.proxy
    if proxy is None or not proxy.host:
        # Fail closed: misconfigured route — never fall back to DIRECT.
        if net_config.mode == NetworkMode.REMOTE_GATEWAY:
            diag.error = (
                f"mode {mode} requires a gateway route"
                + (f" (gateway_id={net_config.gateway_id})" if net_config.gateway_id else "")
                + ", but no gateway route is available. "
                "Refusing to report DIRECT (fail-closed)."
            )
        else:
            diag.error = (
                f"mode {mode} requires a proxy endpoint, but none is configured. "
                "Refusing to report DIRECT (fail-closed)."
            )
        diag.dns = _measure_dns(net_config, timeout)
        diag.ipv6 = _measure_ipv6(net_config, timeout)
        return diag

    # Step 1: TCP connect to the proxy — measured latency, no assumptions.
    tcp_ms, tcp_err = _tcp_connect_ms(proxy.host, proxy.port, timeout)
    diag.latency_ms = round(tcp_ms, 1) if tcp_ms is not None else None
    if tcp_err is not None:
        diag.error = f"proxy unreachable ({proxy.masked_str()}): {tcp_err}"
        diag.dns = _measure_dns(net_config, timeout)
        diag.ipv6 = _measure_ipv6(net_config, timeout)
        return diag

    # Step 2: egress measurement through the proxy.
    if proxy.scheme in ("socks5", "socks5h", "socks4"):
        # stdlib has no SOCKS transport — report honestly instead of faking.
        diag.health = HEALTH_DEGRADED
        diag.error = (
            "SOCKS5 TCP handshake succeeded, but egress IP cannot be verified: "
            "the stdlib HTTP stack has no SOCKS transport. Treat as unverified "
            "(pre-launch gate uses the same TCP probe)."
        )
        diag.dns = _measure_dns(net_config, timeout)
        diag.ipv6 = _measure_ipv6(net_config, timeout)
        return diag

    try:
        opener = _proxy_opener(proxy)
        data = _fetch_echo_geo(opener, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 407:
            diag.error = (
                f"proxy authentication failed (HTTP 407) for {proxy.masked_str()}: "
                "invalid proxy credentials"
            )
        else:
            diag.error = f"proxy HTTP request failed ({proxy.masked_str()}): HTTP {e.code}"
        diag.dns = _measure_dns(net_config, timeout)
        diag.ipv6 = _measure_ipv6(net_config, timeout)
        return diag
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        reason_str = str(reason)
        if "407" in reason_str or "Proxy Authentication Required" in reason_str:
            diag.error = (
                f"proxy authentication failed for {proxy.masked_str()}: "
                "invalid proxy credentials"
            )
        else:
            diag.error = f"proxy request failed ({proxy.masked_str()}): {reason_str}"
        diag.dns = _measure_dns(net_config, timeout)
        diag.ipv6 = _measure_ipv6(net_config, timeout)
        return diag
    except Exception as e:
        diag.error = f"proxy request failed ({proxy.masked_str()}): {e}"
        diag.dns = _measure_dns(net_config, timeout)
        diag.ipv6 = _measure_ipv6(net_config, timeout)
        return diag

    diag.egress_ip = _validate_ip_literal(data.get("query"))
    diag.country = data.get("country") or None
    diag.country_code = data.get("countryCode") or None
    diag.timezone = data.get("timezone") or None
    diag.dns = _measure_dns(net_config, timeout)
    diag.ipv6 = _measure_ipv6(net_config, timeout)
    if diag.egress_ip:
        diag.health = HEALTH_HEALTHY
    else:
        diag.error = (
            f"proxy responded ({proxy.masked_str()}) but returned no valid egress IP"
        )
    return diag
