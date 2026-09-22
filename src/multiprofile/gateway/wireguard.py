"""WireGuard gateway provider stub.

Status: IMPLEMENTATION_BOUNDARY

WireGuard integration requires:
- wg-quick or wireguard-tools on the host
- A WireGuard peer configuration (server pubkey, allowed IPs, endpoint)
- Privilege to run wg-quick up/down (sudo or CAP_NET_ADMIN)

Per-profile WireGuard requires network namespaces (ip netns) to isolate
each profile's traffic — this is a significant implementation that requires
careful privilege management and cannot be done without OS-level setup.

Current status:
    The provider interface is complete.
    Actual WireGuard tunnel management is not implemented.
    Document a manual workaround: create a WireGuard interface + namespace
    externally, then configure the gateway as a custom SOCKS5 proxy within
    that namespace.
"""
from __future__ import annotations

from typing import Any

from .provider import GatewayHealth, NetworkRoute, RemoteNetworkProvider


class WireGuardGatewayProvider(RemoteNetworkProvider):
    """WireGuard VPN gateway — future implementation."""

    provider_type = "wireguard"

    def get_route(self) -> NetworkRoute | None:
        return None  # Not yet implemented

    def health(self) -> GatewayHealth:
        return GatewayHealth(
            ok=False,
            status="UNKNOWN",
            error=(
                "WireGuard gateway not yet implemented. "
                "See docs/REMOTE_GATEWAYS.md for manual setup."
            ),
        )

    def provision(self) -> dict[str, Any]:
        return {
            "ok": False,
            "status": "not_implemented",
            "message": (
                "WireGuard per-profile isolation requires network namespaces (ip netns) "
                "and wireguard-tools. Manual setup is documented in docs/REMOTE_GATEWAYS.md."
            ),
        }
