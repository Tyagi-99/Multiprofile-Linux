"""Richer notes — a timestamped log per profile.

The original ``notes`` field is a single string. It works until two people
share a machine, or until you come back in a month and cannot tell whether
"switched proxy, watch for captchas" was written today or in March.

So notes become a log: append-only entries, each with its own timestamp,
newest first. The flat ``notes`` string stays exactly as it was and keeps
its place as the pinned summary -- the one line worth seeing in the table --
so nothing that reads or writes it has to change, and search keeps working.

Entries live on the profile record as ``noteEntries``. No new file, no
migration: a profile that has never been annotated simply has none.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .profiles import get_profile, load_profiles, save_profiles

MAX_ENTRY_LEN = 4096
MAX_ENTRIES = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entries(profile: dict[str, Any]) -> list[dict[str, Any]]:
    raw = profile.get("noteEntries")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def list_notes(profile_id: str) -> list[dict[str, Any]]:
    """Entries for a profile, newest first."""
    profile = get_profile(profile_id)
    if not profile:
        return []
    return sorted(_entries(profile), key=lambda e: e.get("at") or "", reverse=True)


def add_note(profile_id: str, text: str, *, kind: str = "note") -> dict[str, Any] | None:
    """Append an entry. Returns None when the profile does not exist."""
    text = (text or "").strip()
    if not text:
        raise ValueError("note text is required")
    if len(text) > MAX_ENTRY_LEN:
        raise ValueError(f"note is longer than {MAX_ENTRY_LEN} characters")

    items = load_profiles()
    for i, p in enumerate(items):
        if p.get("id") != profile_id:
            continue
        entry = {
            "id": "note_" + uuid.uuid4().hex[:8],
            "text": text,
            "kind": (kind or "note").strip() or "note",
            "at": _now(),
        }
        # Newest first on disk too, and bounded: a note log is a record, not
        # a place for a runaway writer to grow profiles.json without limit.
        entries = [entry, *_entries(p)][:MAX_ENTRIES]
        items[i] = {**p, "noteEntries": entries}
        save_profiles(items)
        return entry
    return None


def delete_note(profile_id: str, note_id: str) -> bool:
    items = load_profiles()
    for i, p in enumerate(items):
        if p.get("id") != profile_id:
            continue
        entries = _entries(p)
        remaining = [e for e in entries if e.get("id") != note_id]
        if len(remaining) == len(entries):
            return False
        items[i] = {**p, "noteEntries": remaining}
        save_profiles(items)
        return True
    return False


def set_summary(profile_id: str, text: str) -> dict[str, Any] | None:
    """Replace the pinned one-line summary (the original ``notes`` field)."""
    from .profiles import update_profile

    text = (text or "").strip()
    if len(text) > MAX_ENTRY_LEN:
        raise ValueError(f"summary is longer than {MAX_ENTRY_LEN} characters")
    return update_profile(profile_id, {"notes": text})


def note_count(profile: dict[str, Any]) -> int:
    """Entry count for a profile record already in hand (no disk read)."""
    return len(_entries(profile))
