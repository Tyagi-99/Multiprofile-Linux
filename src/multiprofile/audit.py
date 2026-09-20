"""Append-only JSONL audit log under XDG data dir."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from .storage.xdg import audit_path

_lock = threading.Lock()


def audit_log(
    action: str,
    *,
    profile_id: str | None = None,
    ok: bool = True,
    detail: Any = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "profileId": profile_id,
        "ok": bool(ok),
        "detail": detail,
    }
    path = audit_path()
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    path = audit_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    items: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(items))
