"""Tests for Worker 3 — NetworkDiagnostics / run_diagnostics.

Live-network tests are marked `network` and use short timeouts so they fail
gracefully (no hangs) when outbound connectivity is unavailable.
"""
from __future__ import annotations

import base64
import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from multiprofile.network import (
    DNSPolicy,
    NetworkConfig,
    NetworkDiagnostics,
    NetworkMode,
    ProxyConfig,
    ProxyFallback,
    check_proxy_before_launch,
    run_diagnostics,
)

pytestmark = pytest.mark.network


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dead_proxy() -> ProxyConfig:
    # 127.0.0.1:9 — discard port, never listening: TCP connect is refused fast.
    return ProxyConfig(scheme="http", host="127.0.0.1", port=9)


def _dead_config() -> NetworkConfig:
    return NetworkConfig(mode=NetworkMode.HTTP_PROXY, proxy=_dead_proxy())


class _AuthProxyHandler(BaseHTTPRequestHandler):
    """Tiny proxy that demands Proxy-Authorization: Basic user:pass."""

    expected = "Basic " + base64.b64encode(b"user:pass").decode()

    def _reject(self):
        self.send_response(407, "Proxy Authentication Required")
        self.send_header("Proxy-Authenticate", 'Basic realm="test"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle(self):
        auth = self.headers.get("Proxy-Authorization")
        if auth != self.expected:
            self._reject()
            return
        # Credentials valid — but we are a stub: refuse with 502 (no upstream).
        self.send_response(502, "Bad Gateway")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _handle
    do_POST = _handle
    do_CONNECT = _handle

    def log_message(self, *args):
        pass


@pytest.fixture()
def auth_proxy():
    srv = HTTPServer(("127.0.0.1", 0), _AuthProxyHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()


# ---------------------------------------------------------------------------
# Dead proxy -> DOWN + would-block-launch
# ---------------------------------------------------------------------------

def test_dead_proxy_reports_down():
    diag = run_diagnostics(_dead_config(), profile_id="p1", timeout=3)
    assert isinstance(diag, NetworkDiagnostics)
    assert diag.health == "DOWN"
    assert diag.egress_ip is None
    assert diag.error  # a reason is always given
    assert diag.last_test  # ISO8601 timestamp present


def test_dead_proxy_would_block_launch():
    allowed, msg, result = check_proxy_before_launch(_dead_config(), timeout=3)
    assert allowed is False
    assert msg is not None
    assert result is not None and result.ok is False


def test_no_silent_fallback_to_direct_on_dead_proxy():
    diag = run_diagnostics(_dead_config(), profile_id="p1", timeout=3)
    # The configured mode is preserved in the report — it must never be
    # rewritten to DIRECT just because the proxy failed.
    assert diag.mode == NetworkMode.HTTP_PROXY.value
    assert diag.mode != NetworkMode.DIRECT.value
    assert diag.error is not None


def test_misconfigured_proxy_mode_fails_closed():
    cfg = NetworkConfig(mode=NetworkMode.HTTP_PROXY, proxy=None,
                        proxy_fallback=ProxyFallback.BLOCK)
    diag = run_diagnostics(cfg, timeout=3)
    assert diag.health == "DOWN"
    assert diag.mode == NetworkMode.HTTP_PROXY.value
    assert diag.error is not None
    assert diag.egress_ip is None


def test_remote_gateway_without_route_fails_closed():
    cfg = NetworkConfig(mode=NetworkMode.REMOTE_GATEWAY, proxy=None,
                        gateway_id="gw-1")
    diag = run_diagnostics(cfg, timeout=3)
    assert diag.health == "DOWN"
    assert diag.error is not None
    assert diag.egress_ip is None


# ---------------------------------------------------------------------------
# Invalid proxy credentials -> DOWN
# ---------------------------------------------------------------------------

def test_invalid_proxy_credentials_down(auth_proxy):
    proxy = ProxyConfig(scheme="http", host="127.0.0.1", port=auth_proxy,
                        username="user", password="wrong")
    cfg = NetworkConfig(mode=NetworkMode.HTTP_PROXY, proxy=proxy)
    diag = run_diagnostics(cfg, timeout=5)
    assert diag.health == "DOWN"
    assert diag.egress_ip is None
    assert diag.error is not None
    assert "auth" in diag.error.lower() or "407" in diag.error


def test_invalid_proxy_credentials_surface_on_gate_result(auth_proxy):
    # The legacy pre-launch gate is TCP-based and unchanged: a 407 surfaces on
    # the health result. The fail-closed contract for bad auth is diagnostics
    # (DOWN above); the gate must not be silently widened here.
    proxy = ProxyConfig(scheme="http", host="127.0.0.1", port=auth_proxy,
                        username="user", password="wrong")
    cfg = NetworkConfig(mode=NetworkMode.HTTP_PROXY, proxy=proxy)
    allowed, msg, result = check_proxy_before_launch(cfg, timeout=5)
    assert result is not None
    assert result.error is not None and "407" in result.error


# ---------------------------------------------------------------------------
# Direct mode: explicit and measured
# ---------------------------------------------------------------------------

def test_direct_mode_measures_real_egress():
    cfg = NetworkConfig(mode=NetworkMode.DIRECT)
    diag = run_diagnostics(cfg, timeout=8)
    assert diag.mode == NetworkMode.DIRECT.value
    if diag.health == "HEALTHY":
        # A real measurement: a parseable IP literal, never a placeholder.
        ip = ipaddress.ip_address(diag.egress_ip)  # raises if not a literal
        assert str(ip) not in ("0.0.0.0", "127.0.0.1")
        assert diag.country_code is None or len(diag.country_code) == 2
    else:
        # No network on the test host: fail gracefully with a reason.
        assert diag.health == "DOWN"
        assert diag.error is not None
        assert diag.egress_ip is None


def test_dns_ipv6_report_honest_statuses():
    cfg = NetworkConfig(mode=NetworkMode.DIRECT)
    diag = run_diagnostics(cfg, timeout=8)
    assert diag.dns.get("status") in ("PASS", "FAIL", "NOT_RUN")
    assert diag.dns.get("detail")
    assert diag.ipv6.get("status") in ("SUPPORTED", "UNAVAILABLE", "NOT_RUN")
    assert diag.ipv6.get("detail")


def test_socks5_live_tcp_reports_degraded_not_faked(auth_proxy):
    # stdlib has no SOCKS transport: TCP ok, but egress cannot be verified.
    # Must be reported honestly (DEGRADED + reason), never faked, never DIRECT.
    proxy = ProxyConfig(scheme="socks5", host="127.0.0.1", port=auth_proxy)
    cfg = NetworkConfig(mode=NetworkMode.SOCKS5, proxy=proxy)
    diag = run_diagnostics(cfg, timeout=5)
    assert diag.health == "DEGRADED"
    assert diag.mode == NetworkMode.SOCKS5.value
    assert diag.egress_ip is None
    assert diag.error is not None and "SOCKS" in diag.error


def test_dns_not_run_for_proxy_delegated_dns():
    cfg = NetworkConfig(mode=NetworkMode.HTTP_PROXY, proxy=_dead_proxy(),
                        dns_policy=DNSPolicy.PROXY_RESOLVED)
    diag = run_diagnostics(cfg, timeout=3)
    assert diag.dns["status"] == "NOT_RUN"
    assert diag.dns["detail"]


def test_diagnostics_to_dict_has_no_secrets():
    cfg = NetworkConfig(mode=NetworkMode.HTTP_PROXY,
                        proxy=ProxyConfig(scheme="http", host="127.0.0.1",
                                          port=9, username="u", password="s3cret"))
    diag = run_diagnostics(cfg, timeout=3)
    body = str(diag.to_dict()).lower()
    assert "s3cret" not in body
    assert "password" not in body
