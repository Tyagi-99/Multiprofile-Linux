"""Append-only JSONL audit log under XDG data dir."""
from __future__ import annotations

import json
import os
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
        existed = path.exists()
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if not existed:
            # Who launched, exported and deleted what is the operator's
            # record, not the machine's. Create it owner-only.
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    path = audit_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return []
    items: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:  # noqa: BLE001 - one malformed entry must not cost the rest
            continue
    return list(reversed(items))
