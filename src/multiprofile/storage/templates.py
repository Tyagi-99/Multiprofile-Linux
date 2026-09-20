"""Profile field preset templates stored as templates.json under XDG data dir."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .xdg import templates_path

_lock = threading.RLock()

TEMPLATE_FIELDS = (
    "name",
    "userAgentId",
    "userAgent",
    "isMobile",
    "proxyType",
    "customProxy",
    "proxyHost",
    "proxyPort",
    "proxyUser",
    "proxyPass",
    "timezone",
    "language",
    "webrtcMode",
    "notes",
    "tags",
    "startUrl",
    "color",
)


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


def load_templates() -> list[dict[str, Any]]:
    with _lock:
        items = _read_json(templates_path(), [])
        if not isinstance(items, list):
            return []
        return items


def save_templates(items: list[dict[str, Any]]) -> None:
    with _lock:
        _write_json(templates_path(), items)


def get_template(template_id: str) -> dict[str, Any] | None:
    for t in load_templates():
        if t.get("id") == template_id:
            return t
    return None


def create_template(payload: dict[str, Any]) -> dict[str, Any]:
    items = load_templates()
    tpl: dict[str, Any] = {
        "id": "tpl_" + uuid.uuid4().hex[:8],
        "createdAt": date.today().isoformat(),
    }
    for key in TEMPLATE_FIELDS:
        if key in payload:
            tpl[key] = payload[key]
    tpl.setdefault("name", payload.get("name") or "Template")
    tpl.setdefault("tags", payload.get("tags") or [])
    items.append(tpl)
    save_templates(items)
    return tpl


def update_template(template_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    items = load_templates()
    for i, t in enumerate(items):
        if t.get("id") == template_id:
            merged = {**t}
            for key in TEMPLATE_FIELDS:
                if key in payload:
                    merged[key] = payload[key]
            merged["id"] = template_id
            items[i] = merged
            save_templates(items)
            return items[i]
    return None


def delete_template(template_id: str) -> bool:
    items = load_templates()
    new_items = [t for t in items if t.get("id") != template_id]
    if len(new_items) == len(items):
        return False
    save_templates(new_items)
    return True


def template_to_profile_payload(tpl: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in TEMPLATE_FIELDS:
        if key in tpl and key != "name":
            payload[key] = tpl[key]
    # name handled by caller
    if tpl.get("name"):
        payload["name"] = tpl["name"]
    if overrides:
        payload.update(overrides)
    return payload
