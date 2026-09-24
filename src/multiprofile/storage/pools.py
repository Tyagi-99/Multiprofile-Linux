"""Shared proxy pools — pools.json CRUD and assignment.

A pool is an ordered set of proxy endpoints that many profiles draw from,
so an operator with twenty profiles and six proxies configures the six once
instead of pasting them into twenty profiles.

ASSIGNMENT IS STICKY BY DEFAULT, AND THAT IS THE POINT.
A profile is a durable identity: same seed, same timezone, same screen --
and the same egress IP tomorrow. A pool that handed out a different proxy
on every launch would break the one property the product sells. So the
first launch records ``assignments[profile_id] = member_id`` and every
later launch reuses it. Rotation is available for the cases that want it,
but it is opt-in per pool, never the default.

Balance: an unassigned profile takes the enabled member with the fewest
assignments, ties broken by member order, so twenty profiles spread evenly
across six proxies without the operator arranging it.

FAIL-CLOSED: resolving a pool never invents a route. A pool with no enabled
member, or an assignment pointing at a member that has since been disabled
or deleted, returns None with a reason. The caller must block the launch --
see network.network_config_for_profile, which turns that into a blocked
NetworkConfig rather than a silent DIRECT.

SECURITY: member passwords live in pools.json (0600, like gateways.json)
and are NEVER returned by the API. Use to_safe_dict() for any response.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .xdg import data_dir

_lock = threading.RLock()

STRATEGY_STICKY = "sticky"
STRATEGY_ROTATE = "rotate"
STRATEGIES = (STRATEGY_STICKY, STRATEGY_ROTATE)

_SECRET_KEYS = ("password", "proxyPass", "proxy_password")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pools_path() -> Path:
    return data_dir() / "pools.json"


def _read() -> list[dict[str, Any]]:
    path = pools_path()
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return items if isinstance(items, list) else []


def _write(items: list[dict[str, Any]]) -> None:
    path = pools_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    # Credentials live in this file. Narrow the mode before it is in place.
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------


def _normalize_member(raw: dict[str, Any]) -> dict[str, Any]:
    member: dict[str, Any] = {
        "id": raw.get("id") or "pm_" + uuid.uuid4().hex[:8],
        "label": (raw.get("label") or "").strip(),
        "enabled": raw.get("enabled", True) is not False,
    }
    gateway_id = raw.get("gatewayId") or raw.get("gateway_id")
    if gateway_id:
        # Indirection: the endpoint and its credentials stay in gateways.json.
        member["gatewayId"] = str(gateway_id)
        return member
    member["scheme"] = (raw.get("scheme") or "socks5").lower().strip()
    member["host"] = (raw.get("host") or "").strip()
    member["port"] = int(raw.get("port") or 1080)
    if raw.get("username"):
        member["username"] = str(raw["username"])
    if raw.get("password"):
        member["password"] = str(raw["password"])
    return member


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    strategy = (raw.get("strategy") or STRATEGY_STICKY).lower().strip()
    if strategy not in STRATEGIES:
        strategy = STRATEGY_STICKY
    return {
        "id": raw.get("id") or "pool_" + uuid.uuid4().hex[:8],
        "label": (raw.get("label") or "Pool").strip() or "Pool",
        "strategy": strategy,
        "members": [_normalize_member(m) for m in (raw.get("members") or []) if isinstance(m, dict)],
        "assignments": dict(raw.get("assignments") or {}),
        "cursor": int(raw.get("cursor") or 0),
        "notes": raw.get("notes") or "",
        "createdAt": raw.get("createdAt") or _now(),
        "updatedAt": raw.get("updatedAt") or _now(),
    }


def to_safe_dict(pool: dict[str, Any]) -> dict[str, Any]:
    """API-safe copy: member passwords masked, never returned in the clear."""
    safe = dict(pool)
    members = []
    for m in pool.get("members") or []:
        sm = {k: v for k, v in m.items() if k not in _SECRET_KEYS}
        sm["hasPassword"] = any(m.get(k) for k in _SECRET_KEYS)
        members.append(sm)
    safe["members"] = members
    return safe


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def load_pools() -> list[dict[str, Any]]:
    with _lock:
        return [_normalize(p) for p in _read() if isinstance(p, dict)]


def get_pool(pool_id: str) -> dict[str, Any] | None:
    for p in load_pools():
        if p.get("id") == pool_id:
            return p
    return None


def create_pool(payload: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        items = _read()
        pool = _normalize({**payload, "id": None})
        items.append(pool)
        _write(items)
        return pool


def update_pool(pool_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    with _lock:
        items = _read()
        for i, p in enumerate(items):
            if p.get("id") != pool_id:
                continue
            merged = _normalize({**p, **payload, "id": pool_id})
            merged["createdAt"] = p.get("createdAt") or merged["createdAt"]
            merged["updatedAt"] = _now()
            # An assignment pointing at a member that no longer exists is
            # dropped here rather than surfacing as a launch-time mystery.
            live = {m["id"] for m in merged["members"]}
            merged["assignments"] = {
                pid: mid for pid, mid in merged["assignments"].items() if mid in live
            }
            items[i] = merged
            _write(items)
            return merged
    return None


def delete_pool(pool_id: str) -> bool:
    with _lock:
        items = _read()
        remaining = [p for p in items if p.get("id") != pool_id]
        if len(remaining) == len(items):
            return False
        _write(remaining)
        return True


# ---------------------------------------------------------------------------
# assignment
# ---------------------------------------------------------------------------


def enabled_members(pool: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in pool.get("members") or [] if m.get("enabled", True)]


def _member_by_id(pool: dict[str, Any], member_id: str | None) -> dict[str, Any] | None:
    if not member_id:
        return None
    for m in pool.get("members") or []:
        if m.get("id") == member_id:
            return m
    return None


def choose_member(pool: dict[str, Any], profile_id: str) -> tuple[dict[str, Any] | None, str]:
    """Pick this profile's member. Pure -- no disk write, no network.

    Returns ``(member, reason)``. ``member`` is None when the pool cannot
    supply a route; ``reason`` always explains the choice or the refusal, and
    is what the operator sees when a launch is blocked.
    """
    members = enabled_members(pool)
    if not members:
        total = len(pool.get("members") or [])
        return None, (
            f"Pool '{pool.get('label')}' has no enabled member"
            + (f" ({total} disabled)" if total else " (pool is empty)")
        )

    if (pool.get("strategy") or STRATEGY_STICKY) == STRATEGY_ROTATE:
        idx = int(pool.get("cursor") or 0) % len(members)
        return members[idx], f"rotate: position {idx + 1} of {len(members)}"

    assignments: dict[str, Any] = pool.get("assignments") or {}
    existing = _member_by_id(pool, assignments.get(profile_id))
    if existing is not None:
        if existing.get("enabled", True):
            return existing, "sticky: keeping this profile's assigned member"
        # Held identity is gone. Reassigning silently would change the
        # egress IP behind the operator's back, so say so.
        return None, (
            f"Pool '{pool.get('label')}': this profile's assigned member "
            f"'{existing.get('label') or existing['id']}' is disabled. "
            "Re-assign the profile or re-enable the member."
        )

    # Unassigned: take the least-loaded enabled member so profiles spread out.
    load = {m["id"]: 0 for m in members}
    for mid in assignments.values():
        if mid in load:
            load[mid] += 1
    chosen = min(members, key=lambda m: (load[m["id"]], [x["id"] for x in members].index(m["id"])))
    return chosen, f"sticky: new assignment ({load[chosen['id']]} profile(s) already on it)"


def assign(pool_id: str, profile_id: str) -> tuple[dict[str, Any] | None, str]:
    """Choose a member for ``profile_id`` and persist the decision.

    Sticky pools record the assignment so the profile keeps this egress
    tomorrow. Rotate pools advance the cursor instead.
    """
    with _lock:
        items = _read()
        for i, raw in enumerate(items):
            if raw.get("id") != pool_id:
                continue
            pool = _normalize(raw)
            member, reason = choose_member(pool, profile_id)
            if member is None:
                return None, reason
            if pool["strategy"] == STRATEGY_ROTATE:
                pool["cursor"] = (int(pool.get("cursor") or 0) + 1) % max(1, len(enabled_members(pool)))
            elif pool["assignments"].get(profile_id) != member["id"]:
                pool["assignments"][profile_id] = member["id"]
            pool["updatedAt"] = _now()
            items[i] = pool
            _write(items)
            return member, reason
    return None, f"Pool '{pool_id}' not found"


def release(pool_id: str, profile_id: str) -> bool:
    """Drop a profile's sticky assignment (next launch picks afresh)."""
    with _lock:
        items = _read()
        for i, raw in enumerate(items):
            if raw.get("id") != pool_id:
                continue
            pool = _normalize(raw)
            if profile_id not in pool["assignments"]:
                return False
            pool["assignments"].pop(profile_id)
            pool["updatedAt"] = _now()
            items[i] = pool
            _write(items)
            return True
    return False


def resolve_member(pool: dict[str, Any], member: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten a member into ``{scheme, host, port, username, password}``.

    A member that points at a gateway reads the endpoint from gateways.json,
    so one set of credentials serves both. Returns None when the endpoint is
    incomplete -- the caller must treat that as "no route", never as direct.
    """
    if member.get("gatewayId"):
        from .gateways import get_gateway

        # safe=False: this endpoint is about to authenticate against a real
        # proxy, so it needs the actual password. get_gateway() masks by
        # default, and a masked password would reach Chrome as "***".
        gw = get_gateway(member["gatewayId"], safe=False)
        if not gw or not gw.get("host"):
            return None
        return {
            "scheme": (gw.get("scheme") or "socks5").lower(),
            "host": gw["host"],
            "port": int(gw.get("port") or 1080),
            "username": gw.get("username") or None,
            "password": gw.get("password") or None,
        }
    if not member.get("host"):
        return None
    return {
        "scheme": (member.get("scheme") or "socks5").lower(),
        "host": member["host"],
        "port": int(member.get("port") or 1080),
        "username": member.get("username") or None,
        "password": member.get("password") or None,
    }


def pool_usage(pool_id: str) -> dict[str, int]:
    """How many profiles sit on each member — for the console's pool view."""
    pool = get_pool(pool_id)
    if not pool:
        return {}
    usage = {m["id"]: 0 for m in pool.get("members") or []}
    for mid in (pool.get("assignments") or {}).values():
        if mid in usage:
            usage[mid] += 1
    return usage
