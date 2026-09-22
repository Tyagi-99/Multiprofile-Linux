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


def network_config_for_profile(profile: dict[str, Any]) -> NetworkConfig:
    """Read NetworkConfig for a profile, migrating legacy fields if needed."""
    nc_raw = profile.get("networkConfig")
    if nc_raw and isinstance(nc_raw, dict) and nc_raw.get("mode"):
        return NetworkConfig.from_dict(nc_raw)
    # Legacy migration: build from scattered fields
    return NetworkConfig.from_legacy_profile(profile)
