"""AzureGatewayProvider — Azure-hosted remote gateway.

Architecture:
    Multiprofile → Azure VM/VPN → SOCKS5/HTTP proxy → Chrome egress

This provider represents the CLEAN BOUNDARY between the application and
an Azure-hosted gateway.  The actual Azure VM/gateway provisioning is
handled outside this application (Terraform, Azure CLI, etc.) and the
result is a proxy endpoint that this provider wraps.

What this is:
    - A legitimate Azure VM running a SOCKS5 or HTTP proxy server
    - Azure VPN Gateway endpoint
    - Any Azure-hosted network exit point

What this is NOT:
    - GitHub Actions used as a browser proxy
    - GitHub runners used for persistent browsing traffic
    - An Azure endpoint obtained from a GitHub PAT

Status: IMPLEMENTATION_BOUNDARY
    The provider interface and data model are complete.
    Actual provisioning requires Azure credentials + infrastructure setup.
    Configure a manually provisioned Azure proxy endpoint using the
    'host', 'port', 'scheme' fields as a CustomGatewayProvider first,
    then migrate to automated provisioning when ready.
"""
from __future__ import annotations

import socket
import time
from typing import Any

from .provider import GatewayHealth, NetworkRoute, RemoteNetworkProvider


class AzureGatewayProvider(RemoteNetworkProvider):
    """Azure-hosted proxy gateway.

    Config fields:
        host         — Azure VM public IP or DNS name (required when provisioned)
        port         — proxy port (default 1080 for SOCKS5)
        scheme       — 'socks5' | 'http' (default 'socks5')
        username     — proxy auth username (optional)
        password     — proxy auth password (optional)
        region       — Azure region label e.g. 'eastus' (informational)
        resource_group — Azure RG (future: for automated provision/destroy)
        vm_name      — Azure VM name (future)
        label        — human-readable label shown in UI
        status       — 'provisioned' | 'provisioning' | 'stopped' | 'unknown'
    """

    provider_type = "azure"

    def get_route(self) -> NetworkRoute | None:
        host = self.config.get("host")
        port = self.config.get("port")
        provision_status = self.config.get("status") or "unknown"
        if not host or provision_status not in ("provisioned", "running"):
            return None
        return NetworkRoute(
            type=self.config.get("scheme") or "socks5",
            host=host,
            port=int(port or 1080),
            username=self.config.get("username") or None,
            password=self.config.get("password") or None,
            label=self.config.get("label") or f"Azure-{self.config.get('region', 'unknown')}",
        )

    def health(self) -> GatewayHealth:
        provision_status = self.config.get("status") or "unknown"
        if provision_status == "provisioning":
            return GatewayHealth(ok=False, status="PROVISIONING", error="Gateway is still being provisioned")
        if provision_status not in ("provisioned", "running"):
            return GatewayHealth(ok=False, status="UNKNOWN",
                                 error=f"Gateway status: {provision_status}. Configure host/port first.")
        host = self.config.get("host")
        port = self.config.get("port", 1080)
        if not host:
            return GatewayHealth(ok=False, status="FAILED", error="No host configured")
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, int(port)), timeout=10):
                pass
            latency = int((time.monotonic() - t0) * 1000)
            return GatewayHealth(
                ok=True,
                latency_ms=latency,
                status="HEALTHY",
                country=f"Azure/{self.config.get('region', '?')}",
            )
        except OSError as e:
            return GatewayHealth(ok=False, status="FAILED", error=str(e))

    def provision(self) -> dict[str, Any]:
        """Placeholder — automated Azure provisioning requires Azure SDK + credentials.

        To provision an Azure gateway:
        1. Create an Azure VM (az vm create)
        2. Install a SOCKS5 server (e.g. dante-server or microsocks)
        3. Update this gateway's config with host/port/status=provisioned
        4. The provider will then route traffic through it

        Returns an actionable error until implemented.
        """
        return {
            "ok": False,
            "status": "not_implemented",
            "message": (
                "Automated Azure provisioning is not yet implemented. "
                "To use an Azure gateway: provision an Azure VM, install a SOCKS5/HTTP proxy server, "
                "then update this gateway config with host, port, and status='provisioned'."
            ),
            "docs": "See docs/REMOTE_GATEWAYS.md for manual setup instructions.",
        }
