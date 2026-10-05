"""Gateway configuration storage — gateways.json CRUD.

Gateways are stored separately from profiles so multiple profiles can
share one gateway without duplicating credentials.

Each gateway entry:
{
    "id": "gw_...",
    "label": "My Azure East US",
    "providerType": "azure" | "custom" | "wireguard",
    "host": "...",        # for custom/azure
    "port": 1080,
    "scheme": "socks5",
    "username": "...",
    "password": "...",    # stored 0600 — never returned in API responses
    "region": "eastus",   # azure
    "status": "provisioned",
    "createdAt": "...",
    "notes": "..."
}

SECURITY:
    Passwords are stored in the gateways.json file which should be 0600.
    They are NEVER returned in API responses — only masked versions.
    The to_safe_dict() method produces the API-safe representation.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from . import xdg as _xdg


def data_dir():
    # Resolved through the module so a test that patches
    # xdg.data_dir actually redirects this store too. Binding
    # the function at import silently kept using the real path.
    return _xdg.data_dir()

_lock = threading.RLock()


def _gateways_path() -> Path:
    p = data_dir() / "gateways.json"
    return p


def _read_gateways() -> list[dict[str, Any]]:
    path = _gateways_path()
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return []
    if not isinstance(items, list):
        return []
    # Scalars in the list used to reach _mask_password and raise.
    return [g for g in items if isinstance(g, dict)]


def _encrypt_secrets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Never let a cleartext proxy password reach the file."""
    from ..secrets import encrypt_secret, is_encrypted

    out = []
    for g in items:
        g = dict(g)
        pw = g.get("password")
        if isinstance(pw, str) and pw and pw != "***" and not is_encrypted(pw):
            g["password"] = encrypt_secret(pw)
        out.append(g)
    return out


def _write_gateways(items: list[dict[str, Any]]) -> None:
    items = _encrypt_secrets(items)
    path = _gateways_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    tmp.replace(path)
    # Ensure 0600 — gateways may contain proxy passwords
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _mask_password(gw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy safe for API responses — password masked."""
    safe = dict(gw)
    if safe.get("password"):
        safe["password"] = "***"
    return safe


def load_gateways(safe: bool = True) -> list[dict[str, Any]]:
    with _lock:
        items = _read_gateways()
        if safe:
            return [_mask_password(g) for g in items]
        return items


def get_gateway(gateway_id: str, safe: bool = True) -> dict[str, Any] | None:
    for g in load_gateways(safe=False):
        if g.get("id") == gateway_id:
            if safe:
                return _mask_password(g)
            # The unmasked path is the one about to authenticate against a
            # real proxy, so it gets the plaintext. Legacy values that were
            # never encrypted pass through unchanged.
            from ..secrets import decrypt_secret
            out = dict(g)
            if out.get("password"):
                out["password"] = decrypt_secret(out["password"])
            return out
    return None


def create_gateway(payload: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        items = _read_gateways()
        gw = dict(payload)
        gw["id"] = "gw_" + uuid.uuid4().hex[:8]
        gw.setdefault("createdAt", date.today().isoformat())
        gw.setdefault("status", "unknown")
        gw.setdefault("providerType", "custom")
        items.append(gw)
        _write_gateways(items)
        return _mask_password(gw)


def update_gateway(gateway_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _lock:
        items = _read_gateways()
        for i, g in enumerate(items):
            if g.get("id") == gateway_id:
                merged = {**g, **payload, "id": gateway_id}
                items[i] = merged
                _write_gateways(items)
                return _mask_password(merged)
        return None


def delete_gateway(gateway_id: str) -> bool:
    with _lock:
        items = _read_gateways()
        new_items = [g for g in items if g.get("id") != gateway_id]
        if len(new_items) == len(items):
            return False
        _write_gateways(new_items)
        return True


def resolve_gateway(gateway_id: str) -> Any | None:
    """Resolve a gateway ID to a RemoteNetworkProvider instance."""
    from ..gateway.azure import AzureGatewayProvider
    from ..gateway.custom import CustomGatewayProvider
    from ..gateway.wireguard import WireGuardGatewayProvider

    gw = get_gateway(gateway_id, safe=False)
    if not gw:
        return None

    provider_map = {
        "custom": CustomGatewayProvider,
        "azure": AzureGatewayProvider,
        "wireguard": WireGuardGatewayProvider,
    }
    cls = provider_map.get(gw.get("providerType") or "custom", CustomGatewayProvider)
    return cls(gateway_id=gateway_id, config=gw)
