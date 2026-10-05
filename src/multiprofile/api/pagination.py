"""Cursor-based pagination helpers for API list endpoints.

CURSOR SCHEME (documented contract):
    The opaque cursor is ``base64url`` of a JSON object describing the sort
    position of the *last item of the previous page*::

        {"k": <primary sort key>, "id": <tie-breaker id>}

    On each request the server re-sorts the full result set by the
    deterministic ordering ``(primary_key, id)`` and returns the items
    strictly *after* the cursor position ``(k, id)``. Because ``id`` is
    unique per item, identical primary keys can never cause a skip or a
    duplicate — there is no keyset bug on ties.

    Per endpoint:
    - profiles / devices : k == id (string sort key)
    - jobs               : k == createdAt (float epoch), tie-break job id
    - audit log          : k == ts (ISO-8601 string), tie-break zero-padded
                         line index (stable within one file read)

    Cursors are opaque: clients must not construct them, only round-trip
    the ``next_cursor`` value from the previous response. A malformed cursor
    is rejected with 400 ``invalid_input`` (field ``cursor``).

Response shape for every paginated list endpoint::

    {"items": [...], "next_cursor": "<opaque>" | null, "limit": N, "total": M}

``limit`` is the *applied* limit: default 25, clamped silently to [1, 100],
and echoed back so callers always know the effective page size. ``total``
is the size of the full (filtered, pre-cursor) result set — cursor
pagination cannot page by number, but the count is free here because the
endpoints already materialize the list in memory.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Callable

from .validation import ValidationError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def parse_limit(value: Any, *, maximum: int = MAX_LIMIT) -> int:
    """Parse the ``limit`` query param: default 25, clamped silently to 1..max.

    ``maximum`` is per-endpoint because the cap is about the size of one
    response, not the cost of one query. The profile list raises it, having
    earned it: a row there is 663 bytes rather than 2,795 since the
    fingerprint stopped being sent with every one. Other collections have not
    been measured that way and keep the conservative default.
    """
    try:
        limit = int(value if value is not None else DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return max(1, min(limit, maximum))


def encode_cursor(sort_key: Any, item_id: Any) -> str:
    """Encode the last item's sort position into an opaque cursor."""
    payload = {"k": sort_key, "id": item_id}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(value: str | None) -> tuple[Any, Any] | None:
    """Decode a cursor back to ``(sort_key, id)``.

    Returns None for an absent/empty cursor (first page). Raises
    ValidationError on anything malformed.
    """
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii") + b"==")
        payload = json.loads(raw.decode("utf-8"))
        return payload["k"], payload["id"]
    except (binascii.Error, ValueError, KeyError, UnicodeDecodeError, TypeError) as e:
        raise ValidationError("cursor", f"malformed pagination cursor: {e}") from e


def paginate(
    items: list[dict[str, Any]],
    *,
    cursor: str | None,
    limit: int,
    key_fn: Callable[[dict[str, Any]], tuple[Any, Any]],
) -> dict[str, Any]:
    """Keyset-paginate ``items``.

    ``key_fn`` maps each item to ``(primary_key, id)``; items are sorted by
    that tuple (id is the tie-breaker, so ties are stable and lossless).
    Returns ``{"items", "next_cursor", "limit"}``.
    """
    ordered = sorted(items, key=lambda it: key_fn(it))
    total = len(ordered)
    position = decode_cursor(cursor)
    if position is not None:
        ordered = [it for it in ordered if key_fn(it) > position]
    page = ordered[:limit]
    if len(ordered) > limit and page:
        k, item_id = key_fn(page[-1])
        next_cursor = encode_cursor(k, item_id)
    else:
        next_cursor = None
    return {"items": page, "next_cursor": next_cursor, "limit": limit, "total": total}
