"""Client (account/brand) CRUD stored as clients.json under XDG data dir."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from . import db
from .profiles import load_profiles
from .xdg import clients_path, secure_write_json

_lock = threading.RLock()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    secure_write_json(path, data)


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


def _workspace_fields(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Workspace additions on top of the original client shape.

    A workspace is a client that also carries operating defaults: the proxy
    pool its profiles draw from and the tag vocabulary the team agreed on.
    Kept additive so every existing clients.json stays readable.
    """
    base = existing or {}
    tags = payload.get("tags", base.get("tags") or [])
    return {
        "defaultPoolId": payload.get("defaultPoolId", base.get("defaultPoolId")) or None,
        "tags": [str(t).strip() for t in tags if str(t).strip()] if isinstance(tags, list) else [],
    }


# What a client/workspace record may hold. `create_client` built a fixed dict
# and so dropped anything else, while `update_client` merged the payload
# wholesale and so kept anything at all -- two different answers to the same
# question, and between them they lost "description" on create and accepted
# arbitrary keys on update (F21). One list now answers it for both.
CLIENT_FIELDS = (
    "name",
    "description",
    "notes",
    "color",
    "createdAt",
    "defaultPoolId",
    "tags",
)


def create_client(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_clients()
    client = {
        "id": "client_" + uuid.uuid4().hex[:8],
        "name": (payload.get("name") or "Client").strip() or "Client",
        "description": payload.get("description") or "",
        "notes": payload.get("notes") or "",
        "color": payload.get("color") or "#6366f1",
        "createdAt": payload.get("createdAt") or date.today().isoformat(),
        **_workspace_fields(payload),
    }
    items.append(client)
    save_clients(items)
    return client


def update_client(client_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    items = load_clients()
    for i, c in enumerate(items):
        if c.get("id") == client_id:
            known = {k: v for k, v in payload.items() if k in CLIENT_FIELDS}
            merged = {**c, **known, "id": client_id, **_workspace_fields(payload, c)}
            items[i] = merged
            save_clients(items)
            return items[i]
    return None


def load_workspaces() -> list[dict[str, Any]]:
    """Clients, with the workspace fields filled in for older records."""
    return [{**_workspace_fields({}, c), **c, **_workspace_fields(c, c)} for c in load_clients()]


def workspace_default_pool(client_id: str | None) -> str | None:
    """The pool a new profile in this workspace should join, if any."""
    if not client_id:
        return None
    client = get_client(client_id)
    return (client or {}).get("defaultPoolId") or None


def delete_client(client_id: str) -> bool:
    """Delete client and null profile.clientId (do not cascade-delete profiles)."""
    items = load_clients()
    new_items = [c for c in items if c.get("id") != client_id]
    if len(new_items) == len(items):
        return False
    save_clients(new_items)
    # One row per affected profile, rather than rewriting the whole set:
    # unassigning a client must not discard an unrelated edit made while the
    # deletion was in flight.
    for p in load_profiles():
        if p.get("clientId") == client_id:
            db.mutate_profile(p["id"], lambda c: {**c, "clientId": None})
    return True
