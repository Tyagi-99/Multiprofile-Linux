"""Named, scoped, revocable API tokens.

The product had exactly one token, in cleartext, in
``$XDG_CONFIG_HOME/multiprofile/api_token``. Three things follow from that,
and all three are real problems rather than theoretical ones:

**No least privilege.** A script that only needs to start a profile in the
morning holds the same credential that can delete the fleet, read every cookie
jar and rewrite every proxy. The blast radius of a leaked automation token was
the whole product.

**No revocation.** If it leaked, the only remedy was to change the one token,
which breaks every other integration at the same moment. In practice that
means a leaked token does not get rotated.

**Cleartext at rest.** Anyone who could read the file had a working
credential. Only the hash is stored now, so a leaked database does not yield
one.

A note on the hash: these are 256 bits from :mod:`secrets`, not human-chosen
passwords, so SHA-256 is the right primitive. A slow KDF (bcrypt, argon2)
exists to make guessing a low-entropy secret expensive, and there is nothing
low-entropy here to guess; using one would cost every request measurable time
and buy nothing.

**The legacy token still works.** It lives in a file the operator controls and
may already be wired into their scripts. It is treated as a token with full
scope and a reserved id, and it is never copied into the new store: silently
duplicating a credential into a database is not a migration anyone asked for.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from typing import Any

from .storage import db

log = logging.getLogger("multiprofile.api_tokens")

#: Tokens are presented as ``mpt_<id>_<secret>``. The id is not a secret; it
#: is there so verification is one indexed lookup rather than a comparison
#: against every row, which would make the cost of a request scale with how
#: many tokens the operator has.
TOKEN_PREFIX = "mpt"
_TOKEN_RE = re.compile(r"^mpt_([0-9a-f]{12})_([A-Za-z0-9_-]{20,})$")

#: What a token may do.
#:
#: These are coarse on purpose. A permission model with thirty verbs reads as
#: rigorous and is chosen wrongly, because nobody can hold thirty verbs in
#: their head while deciding what a cron job needs. Four map onto the
#: questions an operator actually asks: can it look, can it change things, can
#: it drive browsers, can it manage the installation itself.
SCOPES = ("read", "launch", "write", "admin")

#: The scope a request needs. Resolved from method and path.
_LAUNCH_PATHS = ("/launch", "/stop", "/restart", "/park", "/unpark")

#: The id reserved for the single file/env token that predates this module.
LEGACY_TOKEN_ID = "legacy"


class TokenError(ValueError):
    """A token could not be created as asked."""


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def normalise_scopes(scopes: Any) -> str:
    """Validate and canonicalise a scope list into storage form.

    ``admin`` implies the rest. Storing the implication rather than resolving
    it at check time means a token's stored scopes are exactly what it can do,
    so reading the row answers the question without also having to know the
    rules.
    """
    if scopes is None or scopes == "":
        requested = ["read"]
    elif isinstance(scopes, str):
        requested = [s.strip() for s in scopes.split(",") if s.strip()]
    elif isinstance(scopes, (list, tuple)):
        requested = [str(s).strip() for s in scopes if str(s).strip()]
    else:
        raise TokenError("scopes must be a list of strings")

    unknown = [s for s in requested if s not in SCOPES]
    if unknown:
        raise TokenError(
            f"unknown scope(s) {', '.join(sorted(unknown))}; "
            f"expected any of {', '.join(SCOPES)}"
        )
    if not requested:
        requested = ["read"]
    if "admin" in requested:
        requested = list(SCOPES)
    # `write` and `launch` both imply being able to look at what you changed.
    if {"write", "launch"} & set(requested):
        requested.append("read")
    return ",".join(sorted(set(requested)))


def create_token(name: str, scopes: Any = "read") -> dict[str, Any]:
    """Mint a token. The secret is returned **once** and never stored.

    The caller is responsible for showing it to the operator immediately;
    there is deliberately no way to recover it afterwards, because a store
    that can show you a token later is a store that can leak one later.
    """
    clean_name = str(name or "").strip()
    if not clean_name:
        raise TokenError("a token needs a name, so a leaked one can be identified")
    if len(clean_name) > 120:
        raise TokenError("name must be at most 120 characters")

    stored_scopes = normalise_scopes(scopes)
    token_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    record = db.insert_api_token(token_id, clean_name, _hash(secret), stored_scopes)
    record["scopes"] = stored_scopes.split(",")
    # The only time this value exists outside the caller's memory.
    record["token"] = f"{TOKEN_PREFIX}_{token_id}_{secret}"
    log.info("created API token %s (%s)", token_id, stored_scopes)
    return record


def verify(presented: str) -> dict[str, Any] | None:
    """Resolve a presented token to its record, or None.

    Returns None for every failure -- malformed, unknown, revoked, wrong
    secret -- and the caller answers 401 identically in each case, so the
    endpoint is not an oracle telling an attacker which part they got right.
    """
    if not presented:
        return None
    match = _TOKEN_RE.match(presented.strip())
    if not match:
        return None
    token_id, secret = match.group(1), match.group(2)
    row = db.get_api_token(token_id)
    if not row or row.get("revoked_at"):
        return None
    if not hmac.compare_digest(str(row.get("token_hash") or ""), _hash(secret)):
        return None
    try:
        db.touch_api_token(token_id)
    except Exception:  # noqa: BLE001 - last-used is worth having, never worth failing for
        log.debug("could not record last use of token %s", token_id, exc_info=True)
    return {
        "id": token_id,
        "name": row.get("name"),
        "scopes": str(row.get("scopes") or "read").split(","),
    }


def required_scope(method: str, path: str) -> str:
    """The scope a request needs.

    Managing tokens requires ``admin`` regardless of method, including the
    listing: knowing which integrations exist, when they were last used and
    what they may do is reconnaissance, and a read-only token has no business
    with it.
    """
    if path.startswith("/api/tokens"):
        return "admin"
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return "read"
    if any(segment in path for segment in _LAUNCH_PATHS):
        return "launch"
    return "write"


def allows(token: dict[str, Any] | None, scope: str) -> bool:
    return bool(token) and scope in (token.get("scopes") or [])


def revoke(token_id: str) -> bool:
    ok = db.revoke_api_token(token_id)
    if ok:
        log.info("revoked API token %s", token_id)
    return ok


def listing(include_revoked: bool = False) -> list[dict[str, Any]]:
    out = []
    for row in db.list_api_tokens(include_revoked=include_revoked):
        out.append({
            "id": row["id"],
            "name": row["name"],
            "scopes": str(row.get("scopes") or "").split(","),
            "createdAt": row.get("created_at"),
            "lastUsedAt": row.get("last_used_at"),
            "revokedAt": row.get("revoked_at"),
        })
    return out
