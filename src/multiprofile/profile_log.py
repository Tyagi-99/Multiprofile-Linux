"""Structured per-profile JSON-lines logging + secret-sanitized audit writes.

This module EXTENDS the existing audit log in ``multiprofile.audit`` — it does
not create a second audit system. All writes go through :func:`multiprofile.audit.audit_log`;
the only addition is secret sanitization: proxy passwords, tokens and similar
values are redacted before anything is written.

Every mutation the v1.7.0 workstreams perform (state transitions, park/unpark,
config changes, job completions) should write its entry via :func:`audit_event`
so secrets can never leak into logs.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import audit_log as _raw_audit_log
from .storage.xdg import data_dir

log = logging.getLogger("multiprofile.profile_log")

_lock = threading.Lock()

# Keys whose values must never reach a log file. Matching is case-insensitive
# on the exact key and on common substring forms (e.g. "proxyPass", "api_token").
_SENSITIVE_KEY_RE = re.compile(
    r"(passw|passwd|pwd|pass|secret|token|api[_-]?key|apikey|auth|credential|"
    r"cookie|session|private[_-]?key|bearer|otp|pin)",
    re.IGNORECASE,
)

REDACTED = "***REDACTED***"


def _looks_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key)))


def sanitize(value: Any) -> Any:
    """Recursively redact sensitive values from an arbitrary payload.

    Dict keys matching the sensitive pattern have their values replaced with
    "***REDACTED***". Plain strings that look like a password/token assignment
    (``password=...`` / ``token: ...``) are redacted too. Everything else is
    passed through unchanged. Never raises.
    """
    try:
        if isinstance(value, dict):
            return {
                k: (REDACTED if _looks_sensitive_key(k) else sanitize(v))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(v) for v in value]
        if isinstance(value, str):
            redacted = re.sub(
                r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+",
                lambda m: f"{m.group(1)}={REDACTED}",
                value,
            )
            return redacted
        return value
    except Exception:  # noqa: BLE001 - sanitizer must never break logging
        return REDACTED


def audit_event(
    action: str,
    *,
    profile_id: str | None = None,
    ok: bool = True,
    detail: Any = None,
) -> None:
    """Write an entry to the EXISTING audit log, with secrets redacted first.

    Drop-in replacement for :func:`multiprofile.audit.audit_log` for all new
    mutation paths: the entry format and destination (audit.jsonl) are
    identical; only the detail payload is sanitized before writing.
    """
    try:
        _raw_audit_log(action, profile_id=profile_id, ok=ok, detail=sanitize(detail))
    except Exception as e:  # noqa: BLE001 - logging must not break callers
        log.warning("audit_event failed for %s: %s", action, e)


def _profile_log_path(profile_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", profile_id)[:64] or "unknown"
    return data_dir() / "profile-logs" / f"{safe}.jsonl"


def log_event(profile_id: str, level: str, event: str, detail: Any = None) -> None:
    """Append one structured JSON line to the per-profile log.

    Entry: {"ts", "profileId", "level", "event", "detail"} with the detail
    sanitized exactly like audit entries. Never raises; best-effort only.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "profileId": profile_id,
        "level": str(level),
        "event": str(event),
        "detail": sanitize(detail),
    }
    line = json.dumps(entry, ensure_ascii=False, default=str)
    try:
        path = _profile_log_path(profile_id)
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:  # noqa: BLE001 - logging must not break callers
        log.warning("log_event failed for %s/%s: %s", profile_id, event, e)


def read_profile_log(profile_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Read the most recent per-profile log entries (newest first)."""
    path = _profile_log_path(profile_id)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001 - falls back to a stated default rather than failing the caller
        return []
    items: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:  # noqa: BLE001 - one malformed entry must not cost the rest
            continue
    return list(reversed(items))
