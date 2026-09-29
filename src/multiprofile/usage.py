"""What the fleet actually did, measured rather than estimated.

Nothing in the product recorded usage before this. That is not only a missing
report: every commercial model this product could have -- a seat price, a
per-profile price, a runtime price, a fair-use cap -- has to be drawn from
numbers somebody can defend, and there were none. `storage/db.py` already made
`client_id` a column with the note that "billing has to aggregate by it", but
there was nothing to aggregate.

Three decisions shape what is here.

**Runtime, not launches.** A launch is a count and easy to record, but it is
not what a fleet costs. Twenty profiles started and closed immediately and
twenty left open overnight are the same number of launches and very different
amounts of memory, proxy bandwidth and wear. The metered quantity is seconds
spent in the RUNNING state.

**Measured at the state machine.** Sessions open and close inside
`db.set_profile_state`, which every caller already routes through. Metering
from the callers would mean four places that must each remember, and the one
that forgets produces a bill that is quietly short -- the same shape as F1,
where a control existed but was not on the path that mattered.

**Undercount rather than overcount.** A session abandoned by a crash is closed
at the profile's last heartbeat, not at the time the process next starts. The
machine may have been off for three days. Charging for them would be the more
profitable reading and the dishonest one, and a meter that can be shown to
round in its author's favour is worth nothing to the person being billed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .storage import db

log = logging.getLogger("multiprofile.usage")

#: Windows the summary understands. `None` means "everything recorded".
PERIODS = ("day", "week", "month", "all")


def period_start(period: str, *, now: datetime | None = None) -> str | None:
    """The ISO timestamp a period begins at, or None for "all"."""
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}; expected one of {PERIODS}")
    if period == "all":
        return None
    current = now or datetime.now(timezone.utc)
    days = {"day": 1, "week": 7, "month": 30}[period]
    return (current - timedelta(days=days)).isoformat()


def _blank() -> dict[str, Any]:
    return {"sessions": 0, "seconds": 0, "open": 0}


def _add(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["sessions"] += 1
    bucket["seconds"] += int(row.get("seconds") or 0)
    if row.get("open"):
        bucket["open"] += 1


def summary(period: str = "month", *, now: datetime | None = None) -> dict[str, Any]:
    """Totals for the window, broken down by profile and by workspace.

    Names are resolved for display, but the aggregation keys are the ids that
    were recorded at the time: a profile renamed or moved yesterday must not
    change what last month's numbers say.
    """
    since = period_start(period, now=now)
    rows = db.usage_rows(since=since)

    total = _blank()
    by_profile: dict[str, dict[str, Any]] = {}
    by_client: dict[str, dict[str, Any]] = {}

    for row in rows:
        _add(total, row)
        pid = str(row.get("profile_id") or "")
        by_profile.setdefault(pid, {"profileId": pid, **_blank()})
        _add(by_profile[pid], row)
        cid = row.get("client_id") or ""
        by_client.setdefault(cid, {"clientId": cid or None, **_blank()})
        _add(by_client[cid], row)

    names = _profile_names()
    for pid, bucket in by_profile.items():
        # A profile that has since been deleted keeps its usage and loses its
        # name. Dropping the row instead would make the totals stop adding up,
        # which is the one thing a usage report may not do.
        bucket["name"] = names.get(pid)
    client_names = _client_names()
    for cid, bucket in by_client.items():
        bucket["name"] = client_names.get(cid) if cid else None

    return {
        "period": period,
        "since": since,
        "total": total,
        "byProfile": sorted(by_profile.values(), key=lambda b: -b["seconds"]),
        "byClient": sorted(by_client.values(), key=lambda b: -b["seconds"]),
    }


def sessions(period: str = "month", limit: int = 200) -> list[dict[str, Any]]:
    """The individual sessions behind the summary, newest first.

    A total nobody can drill into is a number to be taken on trust, which is
    exactly what a bill should not be.
    """
    rows = db.usage_rows(since=period_start(period))
    names = _profile_names()
    out = []
    for row in rows[: max(1, limit)]:
        record = dict(row)
        record["profileName"] = names.get(str(row.get("profile_id") or ""))
        out.append(record)
    return out


def _profile_names() -> dict[str, str]:
    try:
        from .storage.profiles import load_profiles

        return {str(p.get("id")): str(p.get("name") or "") for p in load_profiles()}
    except Exception as e:  # noqa: BLE001 - a missing name must not break a total
        log.debug("could not resolve profile names for the usage report: %s", e)
        return {}


def _client_names() -> dict[str, str]:
    try:
        from .storage.clients import load_clients

        return {str(c.get("id")): str(c.get("name") or "") for c in load_clients()}
    except Exception as e:  # noqa: BLE001
        log.debug("could not resolve workspace names for the usage report: %s", e)
        return {}


def format_seconds(seconds: int) -> str:
    """"3h 12m" for a duration. Used by the CLI and the report."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds % 60:02d}s"
    return f"{seconds}s"
