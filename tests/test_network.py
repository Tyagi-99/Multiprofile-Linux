"""Tests for Phase 1 — Network isolation, proxy health, safe launch.

Run: pytest tests/test_network.py -v
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_echo_server(host: str, port: int) -> threading.Thread:
    """Start a minimal TCP server that accepts then closes."""
    def _serve():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((host, port))
            srv.listen(5)
            srv.settimeout(3)
            try:
                while True:
                    try:
                        conn, _ = srv.accept()
                        conn.close()
                    except socket.timeout:
                        break
                    except OSError:
                        break
            except Exception:
                pass
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    time.sleep(0.05)  # give server time to bind
    return t


# ---------------------------------------------------------------------------
# NetworkConfig tests
# ---------------------------------------------------------------------------

class TestNetworkConfig:
    def test_direct_mode_default(self):
        from multiprofile.network import NetworkConfig, NetworkMode
        nc = NetworkConfig()
        assert nc.mode == NetworkMode.DIRECT
        assert not nc.needs_proxy_check()

    def test_socks5_mode_needs_check(self):
        from multiprofile.network import NetworkConfig, NetworkMode, ProxyConfig
        nc = NetworkConfig(
            mode=NetworkMode.SOCKS5,
            proxy=ProxyConfig(scheme="socks5", host="127.0.0.1", port=9050),
        )
        assert nc.needs_proxy_check()

    def test_direct_mode_no_check(self):
        from multiprofile.network import NetworkConfig, NetworkMode
        nc = NetworkConfig(mode=NetworkMode.DIRECT)
        assert not nc.needs_proxy_check()

    def test_to_dict_round_trip(self):
        from multiprofile.network import NetworkConfig, NetworkMode, ProxyConfig, DNSPolicy, IPv6Policy
        nc = NetworkConfig(
            mode=NetworkMode.HTTP_PROXY,
            proxy=ProxyConfig(scheme="http", host="proxy.example.com", port=8080, username="user"),
            dns_policy=DNSPolicy.PROXY_RESOLVED,
            ipv6_policy=IPv6Policy.DISABLED,
        )
        d = nc.to_dict()
        nc2 = NetworkConfig.from_dict(d)
        assert nc2.mode == NetworkMode.HTTP_PROXY
        assert nc2.proxy.host == "proxy.example.com"
        assert nc2.dns_policy == DNSPolicy.PROXY_RESOLVED
        assert nc2.ipv6_policy == IPv6Policy.DISABLED

    def test_legacy_migration_none_proxy(self):
        from multiprofile.network import NetworkConfig, NetworkMode
        profile = {"proxyType": "none", "customProxy": "", "webrtcMode": "default"}
        nc = NetworkConfig.from_legacy_profile(profile)
        assert nc.mode == NetworkMode.DIRECT

    def test_legacy_migration_custom_http(self):
        from multiprofile.network import NetworkConfig, NetworkMode
        profile = {
            "proxyType": "custom",
            "customProxy": "http://proxy.example.com:8080",
            "webrtcMode": "default",
        }
        nc = NetworkConfig.from_legacy_profile(profile)
        assert nc.mode == NetworkMode.HTTP_PROXY
        assert nc.proxy is not None
        assert nc.proxy.host == "proxy.example.com"
        assert nc.proxy.port == 8080

    def test_legacy_migration_socks5(self):
        from multiprofile.network import NetworkConfig, NetworkMode
        profile = {
            "proxyType": "custom",
            "customProxy": "socks5://user:pass@127.0.0.1:1080",
            "webrtcMode": "default",
        }
        nc = NetworkConfig.from_legacy_profile(profile)
        assert nc.mode == NetworkMode.SOCKS5
        assert nc.proxy.username == "user"

    def test_legacy_migration_github_type_is_direct(self):
        """GitHub type must never imply a network route."""
        from multiprofile.network import NetworkConfig, NetworkMode
        profile = {"proxyType": "github", "customProxy": ""}
        nc = NetworkConfig.from_legacy_profile(profile)
        assert nc.mode == NetworkMode.DIRECT

    def test_proxy_masked_str_hides_password(self):
        from multiprofile.network import ProxyConfig
        p = ProxyConfig(scheme="socks5", host="proxy.evil.com", port=1080,
                        username="user", password="s3cret")
        masked = p.masked_str()
        assert "s3cret" not in masked
        assert "user" in masked
        assert "proxy.evil.com" in masked


# ---------------------------------------------------------------------------
# ProxyHealthManager tests
# ---------------------------------------------------------------------------

class TestProxyHealthManager:
    def test_reachable_proxy_ok(self):
        """TCP-reachable server → health ok=True."""
        from multiprofile.network import ProxyConfig, ProxyHealthManager
        port = _free_port()
        _start_echo_server("127.0.0.1", port)
        mgr = ProxyHealthManager()
        proxy = ProxyConfig(scheme="http", host="127.0.0.1", port=port)
        result = mgr.check(proxy, timeout=3.0)
        assert result.ok
        assert result.tcp_ms is not None
        assert result.tcp_ms >= 0

    def test_unreachable_proxy_fails(self):
        """Port with nothing listening → health ok=False."""
        from multiprofile.network import ProxyConfig, ProxyHealthManager
        # Use a port we know is not listening
        port = _free_port()  # grab the port number
        mgr = ProxyHealthManager()
        proxy = ProxyConfig(scheme="http", host="127.0.0.1", port=port)
        result = mgr.check(proxy, timeout=1.0)
        assert not result.ok
        assert result.error

    def test_health_result_to_dict(self):
        from multiprofile.network import ProxyHealthResult
        r = ProxyHealthResult(ok=True, latency_ms=42, egress_ip="1.2.3.4", country="US")
        d = r.to_dict()
        assert d["ok"] is True
        assert d["egressIp"] == "1.2.3.4"
        assert d["status"] == "HEALTHY"

    def test_failed_result_to_dict(self):
        from multiprofile.network import ProxyHealthResult
        r = ProxyHealthResult(ok=False, error="refused")
        d = r.to_dict()
        assert d["status"] == "FAILED"


# ---------------------------------------------------------------------------
# check_proxy_before_launch tests
# ---------------------------------------------------------------------------

class TestCheckProxyBeforeLaunch:
    def test_direct_always_allowed(self):
        from multiprofile.network import NetworkConfig, NetworkMode, check_proxy_before_launch
        nc = NetworkConfig(mode=NetworkMode.DIRECT)
        allowed, err, health = check_proxy_before_launch(nc)
        assert allowed
        assert err is None
        assert health is None

    def test_block_on_dead_proxy(self):
        """Dead proxy with BLOCK fallback must block launch."""
        from multiprofile.network import (
            NetworkConfig, NetworkMode, ProxyConfig, ProxyFallback,
            check_proxy_before_launch
        )
        port = _free_port()  # Nothing listening here
        nc = NetworkConfig(
            mode=NetworkMode.SOCKS5,
            proxy=ProxyConfig(scheme="socks5", host="127.0.0.1", port=port),
            proxy_fallback=ProxyFallback.BLOCK,
        )
        allowed, err, health = check_proxy_before_launch(nc, timeout=1.0)
        assert not allowed
        assert err is not None
        assert "blocked" in err.lower() or "failed" in err.lower()
        assert health is not None
        assert not health.ok

    def test_warn_on_dead_proxy_allows_launch(self):
        """Dead proxy with WARN fallback must allow launch."""
        from multiprofile.network import (
            NetworkConfig, NetworkMode, ProxyConfig, ProxyFallback,
            check_proxy_before_launch
        )
        port = _free_port()
        nc = NetworkConfig(
            mode=NetworkMode.HTTP_PROXY,
            proxy=ProxyConfig(scheme="http", host="127.0.0.1", port=port),
            proxy_fallback=ProxyFallback.WARN,
        )
        allowed, err, health = check_proxy_before_launch(nc, timeout=1.0)
        assert allowed  # WARN mode allows despite failure
        assert health is not None
        assert not health.ok

    def test_alive_proxy_allows_launch(self):
        """Live proxy must allow launch."""
        from multiprofile.network import (
            NetworkConfig, NetworkMode, ProxyConfig, ProxyFallback,
            check_proxy_before_launch
        )
        port = _free_port()
        _start_echo_server("127.0.0.1", port)
        nc = NetworkConfig(
            mode=NetworkMode.HTTP_PROXY,
            proxy=ProxyConfig(scheme="http", host="127.0.0.1", port=port),
            proxy_fallback=ProxyFallback.BLOCK,
        )
        allowed, err, health = check_proxy_before_launch(nc, timeout=3.0)
        assert allowed
        assert health is not None
        assert health.ok


# ---------------------------------------------------------------------------
# Chrome network flag tests
# ---------------------------------------------------------------------------

class TestChromeNetworkFlags:
    def test_direct_no_proxy_flag(self):
        from multiprofile.network import NetworkConfig, NetworkMode, chrome_network_flags
        nc = NetworkConfig(mode=NetworkMode.DIRECT)
        flags = chrome_network_flags(nc)
        assert not any("proxy-server" in f for f in flags)

    def test_http_proxy_flag(self):
        from multiprofile.network import NetworkConfig, NetworkMode, ProxyConfig, chrome_network_flags
        nc = NetworkConfig(
            mode=NetworkMode.HTTP_PROXY,
            proxy=ProxyConfig(scheme="http", host="proxy.example.com", port=8080),
        )
        flags = chrome_network_flags(nc)
        assert any("proxy-server=proxy.example.com:8080" in f for f in flags)

    def test_socks5_proxy_flag(self):
        from multiprofile.network import NetworkConfig, NetworkMode, ProxyConfig, chrome_network_flags
        nc = NetworkConfig(
            mode=NetworkMode.SOCKS5,
            proxy=ProxyConfig(scheme="socks5", host="127.0.0.1", port=1080),
        )
        flags = chrome_network_flags(nc)
        assert any("socks5://127.0.0.1:1080" in f for f in flags)

    def test_ipv6_disabled_flag(self):
        from multiprofile.network import NetworkConfig, NetworkMode, IPv6Policy, chrome_network_flags
        nc = NetworkConfig(mode=NetworkMode.DIRECT, ipv6_policy=IPv6Policy.DISABLED)
        flags = chrome_network_flags(nc)
        assert "--disable-ipv6" in flags

    def test_ipv6_enabled_no_flag(self):
        from multiprofile.network import NetworkConfig, NetworkMode, IPv6Policy, chrome_network_flags
        nc = NetworkConfig(mode=NetworkMode.DIRECT, ipv6_policy=IPv6Policy.ENABLED)
        flags = chrome_network_flags(nc)
        assert "--disable-ipv6" not in flags

    def test_webrtc_proxy_only_flag(self):
        from multiprofile.network import NetworkConfig, NetworkMode, WebRTCPolicy, chrome_network_flags
        nc = NetworkConfig(mode=NetworkMode.DIRECT, webrtc_policy=WebRTCPolicy.PROXY_ONLY)
        flags = chrome_network_flags(nc)
        assert any("disable_non_proxied_udp" in f for f in flags)

    def test_dns_proxy_resolved_flag(self):
        from multiprofile.network import NetworkConfig, NetworkMode, ProxyConfig, DNSPolicy, chrome_network_flags
        nc = NetworkConfig(
            mode=NetworkMode.HTTP_PROXY,
            proxy=ProxyConfig(scheme="http", host="proxy.example.com", port=8080),
            dns_policy=DNSPolicy.PROXY_RESOLVED,
        )
        flags = chrome_network_flags(nc)
        assert any("host-resolver-rules" in f for f in flags)

    def test_proxy_credentials_not_in_flags(self):
        """Chrome flags must NEVER contain proxy credentials."""
        from multiprofile.network import NetworkConfig, NetworkMode, ProxyConfig, chrome_network_flags
        nc = NetworkConfig(
            mode=NetworkMode.HTTP_PROXY,
            proxy=ProxyConfig(scheme="http", host="proxy.example.com", port=8080,
                              username="user", password="s3cr3t"),
        )
        flags = chrome_network_flags(nc)
        flag_str = " ".join(flags)
        assert "s3cr3t" not in flag_str
        assert "user" not in flag_str  # credentials only go in extension proxy.json


# ---------------------------------------------------------------------------
# Gateway tests
# ---------------------------------------------------------------------------

class TestGatewayProviders:
    def test_custom_provider_get_route(self):
        from multiprofile.gateway.custom import CustomGatewayProvider
        p = CustomGatewayProvider("gw_test", {"host": "1.2.3.4", "port": 1080, "scheme": "socks5"})
        route = p.get_route()
        assert route is not None
        assert route.host == "1.2.3.4"
        assert route.port == 1080

    def test_custom_provider_health_failure(self):
        from multiprofile.gateway.custom import CustomGatewayProvider
        port = _free_port()
        p = CustomGatewayProvider("gw_test", {"host": "127.0.0.1", "port": port})
        h = p.health()
        assert not h.ok

    def test_custom_provider_health_success(self):
        from multiprofile.gateway.custom import CustomGatewayProvider
        port = _free_port()
        _start_echo_server("127.0.0.1", port)
        p = CustomGatewayProvider("gw_test", {"host": "127.0.0.1", "port": port})
        h = p.health()
        assert h.ok

    def test_azure_provider_stub_provision(self):
        from multiprofile.gateway.azure import AzureGatewayProvider
        p = AzureGatewayProvider("gw_azure", {"status": "unknown"})
        result = p.provision()
        assert not result["ok"]
        assert "not_implemented" in result.get("status", "") or "implemented" in result.get("message", "").lower()

    def test_azure_provider_no_route_when_not_provisioned(self):
        from multiprofile.gateway.azure import AzureGatewayProvider
        p = AzureGatewayProvider("gw_azure", {"status": "provisioning", "host": "1.2.3.4", "port": 1080})
        assert p.get_route() is None

    def test_wireguard_not_implemented(self):
        from multiprofile.gateway.wireguard import WireGuardGatewayProvider
        p = WireGuardGatewayProvider("gw_wg", {})
        assert p.get_route() is None
        h = p.health()
        assert not h.ok


# ---------------------------------------------------------------------------
# Clone profile tests
# ---------------------------------------------------------------------------

class TestCloneProfile:
    def test_clone_generates_new_seed(self, tmp_path, monkeypatch):
        """Cloned profile must have a different seed than the source."""
        import multiprofile.storage.profiles as profiles_mod
        import multiprofile.storage.xdg as xdg_mod

        # Patch XDG to use tmp dir
        monkeypatch.setattr(xdg_mod, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(xdg_mod, "profiles_path", lambda: tmp_path / "profiles.json")
        monkeypatch.setattr(xdg_mod, "runtime_state_path", lambda: tmp_path / "runtime.json")

        src = profiles_mod.create_profile({"name": "Source", "seed": 123456})
        cloned = profiles_mod.clone_profile(src["id"], mode="config_only")

        assert cloned is not None
        assert cloned["id"] != src["id"]
        assert cloned["seed"] != src["seed"], "Clone must get a new seed"
        assert cloned["name"] != src["name"] or " (clone)" in cloned["name"]

    def test_duplicate_profile_is_alias(self, tmp_path, monkeypatch):
        """duplicate_profile is backwards-compat alias for clone_profile."""
        import multiprofile.storage.profiles as profiles_mod
        import multiprofile.storage.xdg as xdg_mod
        monkeypatch.setattr(xdg_mod, "data_dir", lambda: tmp_path)
        monkeypatch.setattr(xdg_mod, "profiles_path", lambda: tmp_path / "profiles.json")
        monkeypatch.setattr(xdg_mod, "runtime_state_path", lambda: tmp_path / "runtime.json")

        src = profiles_mod.create_profile({"name": "Orig"})
        dup = profiles_mod.duplicate_profile(src["id"])
        assert dup is not None
        assert dup["id"] != src["id"]
        assert dup["seed"] != src["seed"]
