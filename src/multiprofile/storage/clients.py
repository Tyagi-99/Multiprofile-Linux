"""Client (account/brand) CRUD stored as clients.json under XDG data dir."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .profiles import load_profiles, save_profiles
from .xdg import clients_path

_lock = threading.RLock()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_clients() -> list[dict[str, Any]]:
    with _lock:
        items = _read_json(clients_path(), [])
        if not isinstance(items, list):
            return []
        return items


def save_clients(items: list[dict[str, Any]]) -> None:
    with _lock:
        _write_json(clients_path(), items)


def get_client(client_id: str) -> dict[str, Any] | None:
    for c in load_clients():
        if c.get("id") == client_id:
            return c
    return None


def create_client(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_clients()
    client = {
        "id": "client_" + uuid.uuid4().hex[:8],
        "name": (payload.get("name") or "Client").strip() or "Client",
        "notes": payload.get("notes") or "",
        "color": payload.get("color") or "#6366f1",
        "createdAt": payload.get("createdAt") or date.today().isoformat(),
    }
    items.append(client)
    save_clients(items)
    return client


def update_client(client_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    items = load_clients()
    for i, c in enumerate(items):
        if c.get("id") == client_id:
            merged = {**c, **payload, "id": client_id}
            items[i] = merged
            save_clients(items)
            return items[i]
    return None


def delete_client(client_id: str) -> bool:
    """Delete client and null profile.clientId (do not cascade-delete profiles)."""
    items = load_clients()
    new_items = [c for c in items if c.get("id") != client_id]
    if len(new_items) == len(items):
        return False
    save_clients(new_items)
    profiles = load_profiles()
    changed = False
    for p in profiles:
        if p.get("clientId") == client_id:
            p["clientId"] = None
            changed = True
    if changed:
        save_profiles(profiles)
    return True
