"""Take everything out, or destroy everything.

Two rights the brief names under GDPR / India DPDP readiness — "data deletion
and export" — and which the product could not honour. `backup.py` exports one
profile; there was no way to answer "give me everything you hold" or "delete
everything you hold", and no way to find out what those answers would even
contain.

They matter locally too, before any regulator does. An export is how an
operator moves to a new machine or keeps a copy that outlives a disk. A
deletion is how they hand a laptop back.

**The export is honest about what it omits.** It does not contain proxy
passwords. That is a deliberate trade and the manifest says so out loud, with
the field names, because an export whose gaps are undocumented is worse than
one with no gaps at all: the operator discovers the hole when they try to
restore from it. `backup.py` set the precedent by stripping the same fields,
and writing decrypted credentials into a plain file that gets emailed to a new
laptop would undo F4, where exactly this class of secret was found sitting
world-readable.

**The deletion is real.** It removes the database, the Chrome profile
directories, the cookie jars, the audit log and the tokens — not just the rows
that make them visible. A "delete" that unlinks an index and leaves the data
is the failure this audit has been finding all along, one layer down.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("multiprofile.account")

#: Fields removed from every exported profile. The same list `backup.py`
#: strips, for the same reason, and repeated in the manifest so the omission
#: is discoverable without reading this source.
REDACTED_FIELDS = ("proxyPass", "password", "customProxy")

#: What a caller must send to confirm a deletion. A boolean flag is too easy
#: to send by accident from a script that meant something else.
DELETE_CONFIRMATION = "DELETE EVERYTHING"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_profile(profile: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in profile.items() if k not in REDACTED_FIELDS}
    # Runtime facts are not data about the operator; they describe a process
    # that no longer exists by the time anyone reads the file.
    for transient in ("pid", "isRunning", "isQueued", "isFailed", "launchState", "cdpPort"):
        clean.pop(transient, None)
    return clean


def export_all() -> dict[str, Any]:
    """Everything this installation holds, as one JSON-serialisable document.

    Each section is collected independently: one unreadable section must not
    cost the operator the other nine, so a failure is recorded *in* the
    document rather than raised. An export that silently omits a section is
    the thing to avoid; one that says "audit: could not be read" is useful.
    """
    document: dict[str, Any] = {
        "format": "multiprofile-account-export",
        "version": "1.0",
        "exportedAt": _utcnow(),
        "redacted": {
            "fields": list(REDACTED_FIELDS),
            "why": (
                "Proxy credentials are encrypted at rest and are deliberately "
                "not decrypted into this file. A restore needs them re-entered."
            ),
        },
        "sections": {},
        "errors": {},
    }

    def section(name: str, collect) -> None:
        try:
            document["sections"][name] = collect()
        except Exception as e:  # noqa: BLE001 - one bad section must not cost the rest
            log.warning("could not export %s: %s", name, e)
            document["errors"][name] = str(e)

    def _profiles():
        from .storage.profiles import load_profiles

        return [_safe_profile(p) for p in load_profiles()]

    def _trash():
        from .storage.profiles import list_trash

        return [_safe_profile(p) for p in list_trash()]

    def _clients():
        from .storage.clients import load_clients

        return load_clients()

    def _templates():
        from .storage.templates import load_templates

        return load_templates()

    def _usage():
        from . import usage as usage_mod

        return {
            "summary": usage_mod.summary("all"),
            "sessions": usage_mod.sessions("all", limit=10_000),
        }

    def _tokens():
        # Names and scopes only. The hashes are not the operator's data in
        # any useful sense, and exporting them would put credential material
        # into a file whose whole purpose is to be copied around.
        from . import api_tokens

        return api_tokens.listing(include_revoked=True)

    def _plan():
        from . import plans

        return plans.describe()

    def _audit():
        from .audit import read_audit

        return read_audit(limit=100_000)

    def _cookies():
        from .cookies_store import load_cookies
        from .storage.profiles import load_profiles

        return {p["id"]: load_cookies(p["id"]) for p in load_profiles()}

    section("profiles", _profiles)
    section("trash", _trash)
    section("clients", _clients)
    section("templates", _templates)
    section("usage", _usage)
    section("apiTokens", _tokens)
    section("plan", _plan)
    section("audit", _audit)
    section("cookies", _cookies)

    counts = {}
    for name, value in document["sections"].items():
        if isinstance(value, list):
            counts[name] = len(value)
        elif isinstance(value, dict):
            counts[name] = len(value)
    document["counts"] = counts
    return document


def export_to_file(path: str | Path) -> dict[str, Any]:
    """Write the export, readable only by its owner.

    0600 for the same reason F4 was a finding: this file contains cookie jars,
    which are live session credentials even with the proxy passwords stripped.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = export_all()
    target.write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError as e:
        log.warning("could not tighten permissions on %s: %s", target, e)
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "counts": document.get("counts", {}),
        "errors": document.get("errors", {}),
    }


def deletion_preview() -> dict[str, Any]:
    """What a deletion would remove, without removing it.

    Nobody should have to run an irreversible operation to find out what it
    covers.
    """
    from .storage import db
    from .storage.xdg import data_dir

    preview: dict[str, Any] = {"confirmation": DELETE_CONFIRMATION}
    try:
        from .storage.profiles import fleet_capacity

        preview["profiles"] = fleet_capacity()["stored"]
    except Exception:  # noqa: BLE001
        preview["profiles"] = None
    for key, fn in (
        ("trashed", db.count_trash),
        ("usageSessions", db.count_usage_sessions),
        ("apiTokens", lambda: db.count_api_tokens(include_revoked=True)),
    ):
        try:
            preview[key] = fn()
        except Exception:  # noqa: BLE001
            preview[key] = None
    chrome_root = data_dir() / "chrome-profiles"
    preview["browserDirectories"] = (
        len([p for p in chrome_root.iterdir() if p.is_dir()]) if chrome_root.is_dir() else 0
    )
    return preview


def delete_everything(confirmation: str) -> dict[str, Any]:
    """Remove every trace this installation holds. Irreversible.

    Requires the exact confirmation string rather than a boolean, because a
    flag is too easy to send by accident from a script that meant something
    else, and there is no undo behind this one.
    """
    if (confirmation or "").strip() != DELETE_CONFIRMATION:
        raise ValueError(
            f"deletion requires the exact confirmation {DELETE_CONFIRMATION!r}"
        )

    from .storage import db
    from .storage.xdg import audit_path, clients_path, data_dir, profiles_path, templates_path

    removed: dict[str, Any] = {}

    # The database first, and closed before its files are unlinked: removing a
    # file out from under an open SQLite connection leaves the process holding
    # a handle to something that no longer exists, and the next write recreates
    # a partial database that looks like a working one.
    try:
        removed["profiles"] = db.count_profiles()
        removed["usageSessions"] = db.count_usage_sessions()
        removed["apiTokens"] = db.count_api_tokens(include_revoked=True)
    except Exception as e:  # noqa: BLE001 - a preview must still be shown
        # F11: these numbers are the whole point of a preview -- the operator
        # is about to type DELETE EVERYTHING and is deciding on them. A count
        # that silently went missing read as "nothing of that kind to
        # delete", which is the most dangerous thing this dict could say
        # wrongly, so the uncertainty is named instead.
        log.warning("deletion preview: counts unavailable: %s", e)
        removed["countsUnavailable"] = str(e)
    try:
        db_path = Path(db.db_path())
        db.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
        removed["database"] = True
    except Exception as e:  # noqa: BLE001
        log.warning("could not remove the control database: %s", e)
        removed["database"] = False

    # Browser directories: the cookies and logins. A deletion that leaves
    # these is not a deletion, it is hiding the index.
    chrome_root = data_dir() / "chrome-profiles"
    directories = 0
    if chrome_root.is_dir():
        for child in list(chrome_root.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                    directories += 1
                else:
                    child.unlink()
            except OSError as e:
                log.warning("could not remove %s: %s", child, e)
    removed["browserDirectories"] = directories

    files = 0
    for path in (profiles_path(), clients_path(), templates_path(), audit_path()):
        try:
            if Path(path).exists():
                Path(path).unlink()
                files += 1
        except OSError as e:
            log.warning("could not remove %s: %s", path, e)
    # Cookie jars live in their own directory beside the profiles.
    try:
        from .cookies_store import cookies_path

        cookie_dir = Path(cookies_path("_")).parent
        if cookie_dir.is_dir():
            for child in cookie_dir.iterdir():
                try:
                    child.unlink()
                    files += 1
                except OSError:
                    continue
    except Exception as e:  # noqa: BLE001
        log.debug("no cookie directory to clear: %s", e)
    removed["files"] = files
    removed["deletedAt"] = _utcnow()
    log.warning("account data deleted: %s", removed)
    return removed
