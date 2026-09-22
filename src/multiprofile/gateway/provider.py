"""RemoteNetworkProvider — abstract base for all remote gateway providers.

Design principles:
- A provider is responsible for lifecycle: provision, health, connect, disconnect, destroy.
- The browser launcher consumes a generic NetworkRoute, never provider-specific details.
- GitHub PAT is NOT a provider — it is an authentication credential for GitHub API.
- Provider implementations live in: custom.py, azure.py, wireguard.py
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class GatewayHealth:
    """Health snapshot for a remote gateway."""
    ok: bool
    latency_ms: int | None = None
    egress_ip: str | None = None
    country: str | None = None
    error: str | None = None
    status: str = "UNKNOWN"  # HEALTHY | DEGRADED | FAILED | UNKNOWN | PROVISIONING

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latencyMs": self.latency_ms,
            "egressIp": self.egress_ip,
            "country": self.country,
            "error": self.error,
            "status": self.status,
        }


@dataclass
class NetworkRoute:
    """Resolved network route consumed by the browser launcher.

    The launcher only sees this — it never knows which provider generated it.
    """
    type: str          # http | socks5 | direct
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    label: str | None = None  # human-readable, masked

    def masked_label(self) -> str:
        if self.host:
            auth = f"{self.username}:***@" if self.username else ""
            return f"{self.type}://{auth}{self.host}:{self.port}"
        return "direct"


class RemoteNetworkProvider(ABC):
    """Abstract base class for remote gateway providers."""

    provider_type: str = "unknown"

    def __init__(self, gateway_id: str, config: dict[str, Any]):
        self.gateway_id = gateway_id
        self.config = config

    @abstractmethod
    def health(self) -> GatewayHealth:
        """Check gateway health and return current status."""
        ...

    @abstractmethod
    def get_route(self) -> NetworkRoute | None:
        """Return the NetworkRoute for this gateway, or None if unavailable."""
        ...

    def provision(self) -> dict[str, Any]:
        """Provision the gateway resource. Override in providers that support it."""
        return {"ok": False, "error": f"Provider '{self.provider_type}' does not support provisioning"}

    def destroy(self) -> dict[str, Any]:
        """Destroy the provisioned gateway resource."""
        return {"ok": False, "error": f"Provider '{self.provider_type}' does not support destroy"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "gatewayId": self.gateway_id,
            "providerType": self.provider_type,
            "label": self.config.get("label") or self.gateway_id,
        }
