"""CustomGatewayProvider — user-supplied HTTP/SOCKS5 proxy endpoint.

This is the simplest provider: the user configures a proxy host:port directly.
No provisioning/destroy lifecycle needed.
"""
from __future__ import annotations

import socket
import time
from typing import Any

from .provider import GatewayHealth, NetworkRoute, RemoteNetworkProvider


class CustomGatewayProvider(RemoteNetworkProvider):
    """A user-supplied HTTP or SOCKS5 proxy acting as a remote gateway."""

    provider_type = "custom"

    def get_route(self) -> NetworkRoute | None:
        host = self.config.get("host")
        port = self.config.get("port")
        if not host or not port:
            return None
        return NetworkRoute(
            type=self.config.get("scheme") or "socks5",
            host=host,
            port=int(port),
            username=self.config.get("username") or None,
            password=self.config.get("password") or None,
            label=self.config.get("label"),
        )

    def health(self) -> GatewayHealth:
        route = self.get_route()
        if not route or not route.host:
            return GatewayHealth(ok=False, status="FAILED", error="No host configured")
        t0 = time.monotonic()
        try:
            with socket.create_connection((route.host, route.port or 1080), timeout=8):
                pass
            latency = int((time.monotonic() - t0) * 1000)
            return GatewayHealth(ok=True, latency_ms=latency, status="HEALTHY")
        except OSError as e:
            return GatewayHealth(ok=False, status="FAILED", error=str(e))
