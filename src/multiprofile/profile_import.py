"""Create many profiles from a list of proxies.

Filling a fleet one profile at a time is the wrong shape for how operators
actually work: they arrive with a block of proxies from a provider and want a
profile for each. The product could create, clone, duplicate and build from a
template, but there was no way to say "here are 150 proxies, make me 150
profiles".

Parsing is not reimplemented here. ``proxy_util.parse_proxy`` already accepts
every shape a provider hands out -- ``ip:port``, ``ip:port:user:pass``,
``user:pass@host:port``, and the ``http://`` / ``socks5://`` / ``socks5h://``
URL forms -- and it is the same function the launcher routes through, so an
imported profile cannot parse differently from one typed in by hand.

Two decisions shape the rest:

**Partial success.** A bad line does not reject the batch. Someone pasting 150
proxies usually has one typo, and failing all 150 for it means finding the typo
by bisection. Every row is reported by index with the line that produced it, so
a caller can fix two and re-paste two rather than re-paste everything.

**Duplicates are refused, not silently merged.** Pasting the same block twice
is a much more common accident than genuinely wanting two profiles on one
proxy, and the cost of being wrong is asymmetric: a skipped row is a line in a
report, a duplicated fleet is 150 profiles to delete by hand. Repeats inside
one request are skipped as duplicates; a proxy that an existing profile already
uses is reported as such and still created, because sharing an exit between
profiles is a legitimate (if risky) thing an operator may mean.
"""
from __future__ import annotations

import logging
from typing import Any

from .proxy_util import parse_proxy

log = logging.getLogger("multiprofile.profile_import")

#: Most rows one request may carry. Not a fleet limit -- the operator decides
#: how many profiles they keep -- but a bound on a single call, so one paste
#: cannot start an unbounded amount of work.
MAX_ROWS = 500

#: Lines that carry no proxy. ``#`` is allowed because operators annotate the
#: blocks their provider sends them.
_COMMENT_PREFIXES = ("#", "//")


#: What a bare ``ip:port`` means when the operator does not say. Their list
#: usually comes from one provider and is all one kind, so this is a batch
#: setting rather than a per-row guess.
DEFAULT_PROXY_TYPE = "http"

VALID_PROXY_TYPES = ("http", "https", "socks5", "socks5h", "socks4")


def parse_proxy_list(
    raw: str | list[str], proxy_type: str = DEFAULT_PROXY_TYPE
) -> dict[str, Any]:
    """Turn pasted text into rows, without creating anything.

    ``proxy_type`` is the assumption applied to rows that carry no scheme of
    their own. A row written as ``socks5://host:port`` keeps its own scheme;
    a bare ``host:port:user:pass`` takes this one. Without it every bare row
    became HTTP, so importing a SOCKS5 list produced a fleet that silently
    spoke the wrong protocol to every proxy in it.

    Returns ``{"rows": [...], "errors": [...], "skipped": [...]}``. Each row
    carries the 1-based ``line`` it came from so a report can point at the
    input the operator is looking at, rather than at an index into a list they
    never see.
    """
    if isinstance(raw, list):
        lines = [str(item) for item in raw]
    else:
        lines = str(raw or "").splitlines()

    hint = (proxy_type or DEFAULT_PROXY_TYPE).strip().lower()
    if hint not in VALID_PROXY_TYPES:
        hint = DEFAULT_PROXY_TYPE

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: dict[tuple[str, int, str | None], int] = {}

    for index, original in enumerate(lines, start=1):
        text = original.strip()
        if not text or text.startswith(_COMMENT_PREFIXES):
            continue
        parsed = parse_proxy(text, hint)
        if not parsed:
            errors.append({
                "line": index,
                "input": text[:200],
                "error": "unparseable",
                "message": (
                    "not a proxy this understands; expected ip:port, "
                    "ip:port:user:pass, user:pass@host:port or a "
                    "http:// or socks5:// URL"
                ),
            })
            continue
        key = (
            str(parsed["host"]).lower(),
            int(parsed["port"]),
            parsed.get("username") or None,
        )
        if key in seen:
            skipped.append({
                "line": index,
                "input": text[:200],
                "reason": "duplicate",
                "message": f"same proxy as line {seen[key]}",
            })
            continue
        seen[key] = index
        rows.append({"line": index, "input": text[:200], "proxy": parsed})

    if len(rows) > MAX_ROWS:
        for row in rows[MAX_ROWS:]:
            skipped.append({
                "line": row["line"],
                "input": row["input"],
                "reason": "over_limit",
                "message": f"only the first {MAX_ROWS} rows of a request are imported",
            })
        rows = rows[:MAX_ROWS]

    return {"rows": rows, "errors": errors, "skipped": skipped}


def _profile_payload(
    row: dict[str, Any],
    ordinal: int,
    *,
    name_prefix: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    proxy = row["proxy"]
    payload = dict(base)
    payload.update({
        "name": f"{name_prefix} {ordinal}".strip(),
        # customProxy is deliberately NOT set to the pasted line.
        #
        # It looks like the natural place to keep "what the operator typed",
        # and the first version did exactly that -- which wrote the proxy
        # password to disk in clear text. `_encrypt_profile_secrets` wraps
        # `proxyPass` and nothing else, so `5.6.7.8:1080:user:s3cretpw` sat
        # readable in the database beside the encrypted copy of the same
        # password. `backup.py` already treats this field as a secret and
        # strips it, which was the hint.
        #
        # Redacting it is not an option either: `NetworkConfig.from_profile`
        # prefers `customProxy` over the split fields, so a redacted string
        # would be what the launch actually routes on, and the credentials
        # would be lost. The split fields below carry everything, and the
        # password among them is encrypted at rest.
        "customProxy": "",
        "proxyType": proxy.get("scheme") or "http",
        "proxyHost": proxy.get("host") or "",
        "proxyPort": proxy.get("port"),
        "proxyUser": proxy.get("username") or "",
        "proxyPass": proxy.get("password") or "",
    })
    return payload


def import_profiles(
    raw: str | list[str],
    *,
    name_prefix: str = "Imported",
    proxy_type: str = DEFAULT_PROXY_TYPE,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one profile per proxy.

    ``base`` is merged into every profile before the proxy fields, so a caller
    can apply a client, tags, a pool or a start URL to the whole batch without
    repeating it per row.

    Returns a report: what was created, what failed to parse, and what was
    skipped, each keyed by the input line it came from.
    """
    from .storage.profiles import create_profile, load_profiles

    parsed = parse_proxy_list(raw, proxy_type)
    rows = parsed["rows"]
    skipped = list(parsed["skipped"])

    # Which exits the fleet already uses. Reported, not refused: sharing an
    # exit between profiles is a legitimate thing to mean, and refusing it
    # here would make a re-import after a partial failure impossible.
    existing: dict[tuple[str, int], str] = {}
    try:
        for profile in load_profiles():
            host = str(profile.get("proxyHost") or "").lower()
            port = profile.get("proxyPort")
            if host and port:
                existing.setdefault((host, int(port)), str(profile.get("name") or ""))
    except Exception as e:  # noqa: BLE001 - a warning is not worth failing on
        log.debug("could not read existing profiles for the duplicate check: %s", e)

    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for ordinal, row in enumerate(rows, start=1):
        proxy = row["proxy"]
        seen_as = existing.get((str(proxy["host"]).lower(), int(proxy["port"])))
        if seen_as:
            warnings.append({
                "line": row["line"],
                "input": row["input"],
                "warning": "proxy_already_used",
                "message": f"this exit is already used by {seen_as!r}",
            })
        payload = _profile_payload(
            row, ordinal, name_prefix=name_prefix, base=base or {}
        )
        try:
            profile = create_profile(payload)
        except Exception as e:  # noqa: BLE001 - one bad row must not stop the rest
            failed.append({
                "line": row["line"],
                "input": row["input"],
                "error": "not_created",
                "message": str(e),
            })
            continue
        created.append({
            "line": row["line"],
            "id": profile["id"],
            "name": profile["name"],
            "proxy": f"{proxy['host']}:{proxy['port']}",
        })

    return {
        "ok": True,
        "created": created,
        "failed": [*parsed["errors"], *failed],
        "skipped": skipped,
        "warnings": warnings,
        "counts": {
            "created": len(created),
            "failed": len(parsed["errors"]) + len(failed),
            "skipped": len(skipped),
            "warnings": len(warnings),
        },
        "limit": MAX_ROWS,
    }
