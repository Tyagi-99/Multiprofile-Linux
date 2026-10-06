"""Import, assign, apply, verify, update, remove.

The design rule for this module is that **nothing claims an installation it
has not observed**. Importing writes files and a row; assigning writes a row;
only a launch can install, and only the browser's own answer can verify. The
state machine is therefore:

    imported/downloaded     the package exists in managed storage
      -> assigned           chosen for a profile, applies on next launch
      -> pending_restart    the profile is running; the flag cannot be
                            retrofitted to a live browser, so it waits
      -> installed_verified the browser reported the extension ID we
                            predicted from the managed path
      -> failed             with a per-profile reason, retryable alone

Bulk operations return one result per profile. A single failure must never
mark the batch successful, and a batch must never be reported as "3 profiles
updated" when one of them refused.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..storage.profiles import get_profile, load_profiles
from ..storage.xdg import (
    extension_payload_dir,
    extension_versions_dir,
    extensions_dir,
)
from . import store
from .capability import BrowserCapability, detect
from .identity import predict_unpacked_id
from .package import (
    ArchiveReport,
    ExtensionManifest,
    PackageError,
    copy_folder,
    extract_zip,
    inspect_folder,
    inspect_zip,
)

log = logging.getLogger("multiprofile.extensions")

SOURCE_ZIP = "zip"
SOURCE_FOLDER = "folder"
SOURCE_WEBSTORE = "webstore"
SOURCE_DOWNLOAD = "download"


@dataclass
class ProfileResult:
    """What happened to exactly one profile."""

    profile_id: str
    ok: bool
    state: str
    detail: str = ""
    profile_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "profileName": self.profile_name,
            "ok": self.ok,
            "state": self.state,
            "stateLabel": store.STATE_LABELS.get(self.state, self.state),
            "detail": self.detail,
        }


@dataclass
class BulkResult:
    """A per-profile ledger. ``ok`` is true only if every profile succeeded."""

    results: list[ProfileResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)

    @property
    def failed_profile_ids(self) -> list[str]:
        return [r.profile_id for r in self.results if not r.ok]

    def to_dict(self) -> dict[str, Any]:
        done = [r for r in self.results if r.ok]
        return {
            "ok": self.ok,
            "total": len(self.results),
            "succeeded": len(done),
            "failed": len(self.results) - len(done),
            "failedProfileIds": self.failed_profile_ids,
            "results": [r.to_dict() for r in self.results],
            "skipped": list(self.skipped),
        }


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def _record(
    manifest: ExtensionManifest, *, ext_uuid: str, source: str,
    source_detail: str, payload: Path, size: int, package_state: str,
) -> dict[str, Any]:
    store.create_extension(
        ext_uuid,
        name=manifest.name,
        version=manifest.version,
        manifest_version=manifest.manifest_version,
        description=manifest.description,
        source=source,
        source_detail=source_detail,
        permissions=manifest.permissions,
        host_permissions=manifest.host_permissions,
        has_key=manifest.has_key,
        payload_path=str(payload),
        size_bytes=size,
        install_state=package_state,
    )
    # The ID we expect the browser to report. Recorded as *expected*, not as
    # fact: `verify_profile` replaces belief with observation.
    store.update_extension(ext_uuid, expected_ext_id=predict_unpacked_id(payload))
    store.record_version(
        ext_uuid, version=manifest.version, payload_path=str(payload),
        manifest_version=manifest.manifest_version, size_bytes=size,
    )
    got = store.get_extension(ext_uuid)
    assert got is not None
    return got


def preview_zip(zip_path: str | Path) -> dict[str, Any]:
    """Validate an archive and report what installing it would mean.

    Nothing is written. This is what backs "show permissions before
    installation" -- the operator sees the manifest's declared permissions
    before any bytes leave the archive.
    """
    report: ArchiveReport = inspect_zip(zip_path)
    return {
        "manifest": report.manifest.to_dict(),
        "root": report.root or "<top level>",
        "members": report.members,
        "totalBytes": report.total_bytes,
        "missingReferencedFiles": report.missing,
        "capability": detect().to_dict(),
    }


def import_zip(zip_path: str | Path, *, source_detail: str = "") -> dict[str, Any]:
    """Extract a validated archive into managed storage."""
    ext_uuid = uuid.uuid4().hex
    payload = extension_payload_dir(ext_uuid)
    payload.parent.mkdir(parents=True, exist_ok=True)
    report = extract_zip(zip_path, payload)
    if report.missing:
        shutil.rmtree(payload.parent, ignore_errors=True)
        raise PackageError(
            f"manifest.json references {len(report.missing)} file(s) missing "
            f"from the archive: {', '.join(report.missing[:5])}. The browser "
            f"would load this and then fail on it.",
            code="missing_files")
    size = sum(f.stat().st_size for f in payload.rglob("*") if f.is_file())
    return _record(
        report.manifest, ext_uuid=ext_uuid, source=SOURCE_ZIP,
        source_detail=source_detail or Path(zip_path).name,
        payload=payload, size=size, package_state=store.PACKAGE_IMPORTED,
    )


def import_folder(folder: str | Path) -> dict[str, Any]:
    """Copy a validated unpacked folder into managed storage.

    The operator's folder is not loaded directly, on purpose. The extension ID
    is derived from the load path, so loading from wherever the operator
    happens to keep it would tie the extension's identity -- and therefore its
    storage -- to a directory they may move, rename or delete.
    """
    manifest = inspect_folder(folder)
    ext_uuid = uuid.uuid4().hex
    payload = extension_payload_dir(ext_uuid)
    payload.parent.mkdir(parents=True, exist_ok=True)
    size = copy_folder(folder, payload)
    return _record(
        manifest, ext_uuid=ext_uuid, source=SOURCE_FOLDER,
        source_detail=str(Path(folder).resolve()),
        payload=payload, size=size, package_state=store.PACKAGE_IMPORTED,
    )


# ---------------------------------------------------------------------------
# update and rollback
# ---------------------------------------------------------------------------

def update_extension(
    ext_uuid: str, *, zip_path: str | Path | None = None,
    folder: str | Path | None = None,
) -> dict[str, Any]:
    """Replace the payload in place, retaining the previous version.

    In place is the point: the load path -- and therefore the extension ID,
    and therefore the per-profile storage behind it -- stays the same across
    the update. A versioned directory would give the new payload a new ID and
    present every profile with an empty extension.

    The previous payload is copied aside first, so a failed or unwanted update
    is recoverable.
    """
    row = store.get_extension(ext_uuid)
    if row is None:
        raise PackageError(f"No extension {ext_uuid}.", code="not_found")
    if (zip_path is None) == (folder is None):
        raise PackageError("Provide exactly one of zip_path or folder.",
                           code="bad_request")

    # Validate the NEW payload before touching the old one.
    if zip_path is not None:
        report = inspect_zip(zip_path)
        new_manifest = report.manifest
        if report.missing:
            raise PackageError(
                f"The new archive is missing {len(report.missing)} referenced "
                f"file(s): {', '.join(report.missing[:5])}.", code="missing_files")
    else:
        new_manifest = inspect_folder(folder)  # type: ignore[arg-type]

    payload = extension_payload_dir(ext_uuid)
    keep = extension_versions_dir(ext_uuid) / f"{row['version']}-{uuid.uuid4().hex[:8]}"
    if payload.exists():
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(payload, keep)
        store.record_version(
            ext_uuid, version=row["version"], payload_path=str(keep),
            manifest_version=row["manifestVersion"], size_bytes=row["sizeBytes"],
        )

    try:
        if zip_path is not None:
            extract_zip(zip_path, payload)
        else:
            copy_folder(folder, payload)  # type: ignore[arg-type]
    except Exception:
        # Put the previous payload back rather than leaving a half-written one.
        if keep.exists():
            shutil.rmtree(payload, ignore_errors=True)
            shutil.copytree(keep, payload)
        raise

    size = sum(f.stat().st_size for f in payload.rglob("*") if f.is_file())
    store.update_extension(
        ext_uuid, name=new_manifest.name, version=new_manifest.version,
        manifest_version=new_manifest.manifest_version,
        description=new_manifest.description,
        permissions=new_manifest.permissions,
        host_permissions=new_manifest.host_permissions,
        has_key=new_manifest.has_key, size_bytes=size,
    )
    got = store.get_extension(ext_uuid)
    assert got is not None
    return got


def rollback(ext_uuid: str) -> dict[str, Any]:
    """Restore the most recent retained payload."""
    versions = store.list_versions(ext_uuid)
    payload = str(extension_payload_dir(ext_uuid))
    previous = next((v for v in versions if v["payloadPath"] != payload), None)
    if previous is None:
        raise PackageError(
            "No retained previous version to roll back to.", code="no_previous")
    src = Path(previous["payloadPath"])
    if not src.is_dir():
        raise PackageError(
            f"The retained payload for {previous['version']} is missing from "
            f"disk ({src}), so it cannot be restored.", code="missing_files")
    target = extension_payload_dir(ext_uuid)
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(src, target)
    manifest = inspect_folder(target)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    store.update_extension(
        ext_uuid, name=manifest.name, version=manifest.version,
        manifest_version=manifest.manifest_version,
        permissions=manifest.permissions,
        host_permissions=manifest.host_permissions, size_bytes=size,
    )
    got = store.get_extension(ext_uuid)
    assert got is not None
    return got


# ---------------------------------------------------------------------------
# profile selection
# ---------------------------------------------------------------------------

def resolve_profiles(
    *, profile_ids: list[str] | None = None,
    search: str = "", tags: list[str] | None = None,
    running: str = "any", select_all_matching: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve a selection to concrete profiles, **now**.

    ``select_all_matching`` is resolved against the filter at the moment the
    operation starts, and the resulting ID list is what the operation uses. It
    is deliberately not re-evaluated later: a profile created while a bulk
    assignment is running must not be swept into it, and a profile the
    operator had filtered out must not appear because the filter changed.

    Returns ``(profiles, missing_ids)``.
    """
    everything = load_profiles()
    by_id = {p["id"]: p for p in everything}

    if profile_ids and not select_all_matching:
        found = [by_id[p] for p in profile_ids if p in by_id]
        missing = [p for p in profile_ids if p not in by_id]
        return found, missing

    needle = (search or "").strip().lower()
    want_tags = {t.lower() for t in (tags or [])}
    out = []
    for p in everything:
        if needle and needle not in str(p.get("name") or "").lower():
            continue
        if want_tags:
            have = {str(t).lower() for t in (p.get("tags") or [])}
            if not (want_tags & have):
                continue
        if running == "running" and not p.get("isRunning"):
            continue
        if running == "stopped" and p.get("isRunning"):
            continue
        out.append(p)
    return out, []


# ---------------------------------------------------------------------------
# assignment
# ---------------------------------------------------------------------------

def assign(
    ext_uuid: str, profile_ids: list[str], *,
    capability: BrowserCapability | None = None,
) -> BulkResult:
    """Assign one extension to many profiles, idempotently.

    A stopped profile becomes ``assigned`` and picks the extension up on its
    next launch. A running profile becomes ``pending_restart``: the flag that
    loads an unpacked extension is a command-line argument, so it cannot be
    applied to a browser that is already running, and this product does not
    kill an operator's live session to make a number go green.
    """
    row = store.get_extension(ext_uuid)
    result = BulkResult()
    if row is None:
        raise PackageError(f"No extension {ext_uuid}.", code="not_found")

    cap = capability or detect()
    payload = Path(row["payloadPath"])
    if not payload.is_dir():
        for pid in profile_ids:
            profile = get_profile(pid)
            result.results.append(ProfileResult(
                pid, False, store.STATE_FAILED,
                f"The extension's files are missing from managed storage "
                f"({payload}). Re-import it before assigning.",
                str((profile or {}).get("name") or ""),
            ))
            store.upsert_assignment(ext_uuid, pid, state=store.STATE_FAILED,
                                    last_error="payload missing")
        return result

    seen: set[str] = set()
    for pid in profile_ids:
        if pid in seen:
            continue            # the same profile named twice is one profile
        seen.add(pid)
        profile = get_profile(pid)
        name = str((profile or {}).get("name") or "")
        if profile is None:
            result.results.append(ProfileResult(
                pid, False, store.STATE_FAILED, "No such profile.", name))
            continue
        if not cap.unpacked:
            store.upsert_assignment(ext_uuid, pid, state=store.STATE_FAILED,
                                    last_error=cap.reason)
            result.results.append(ProfileResult(
                pid, False, store.STATE_FAILED,
                f"{cap.reason} {cap.remediation}".strip(), name))
            continue

        running = bool(profile.get("isRunning"))
        state = store.STATE_PENDING_RESTART if running else store.STATE_ASSIGNED
        existing = store.get_assignment(ext_uuid, pid)
        if existing and existing["state"] == store.STATE_INSTALLED_VERIFIED and not running:
            # Already installed and verified, profile stopped: re-assigning is
            # a no-op rather than a downgrade to "assigned".
            result.results.append(ProfileResult(
                pid, True, store.STATE_INSTALLED_VERIFIED,
                "Already installed and verified in this profile.", name))
            continue
        store.upsert_assignment(ext_uuid, pid, state=state, enabled=True)
        detail = ("This profile is running. The extension loads on its next "
                  "launch -- use Restart to apply it now."
                  if running else
                  "Applies on next launch.")
        result.results.append(ProfileResult(pid, True, state, detail, name))
    return result


def unassign(ext_uuid: str, profile_id: str) -> ProfileResult:
    """Remove from ONE profile. Other profiles keep theirs, and the shared
    payload is left alone -- deleting it would break them."""
    profile = get_profile(profile_id)
    name = str((profile or {}).get("name") or "")
    removed = store.remove_assignment(ext_uuid, profile_id)
    if not removed:
        return ProfileResult(profile_id, True, "unassigned",
                             "Was not assigned to this profile.", name)
    running = bool((profile or {}).get("isRunning"))
    return ProfileResult(
        profile_id, True, "unassigned",
        "Still loaded in the running browser until it restarts."
        if running else "Removed. Will not load on next launch.",
        name)


# ---------------------------------------------------------------------------
# launch integration
# ---------------------------------------------------------------------------

def payload_paths_for_profile(profile_id: str) -> list[tuple[str, str]]:
    """``(ext_uuid, payload_path)`` for every enabled assignment that exists.

    A missing payload is skipped and recorded as failed rather than passed to
    the browser: ``--load-extension`` with a path that is not there makes
    Chromium complain and can abort the whole load, taking the other
    extensions with it.
    """
    out: list[tuple[str, str]] = []
    for row in store.list_assignments(profile_id=profile_id, enabled_only=True):
        ext = store.get_extension(row["extensionId"])
        if ext is None:
            continue
        path = Path(ext["payloadPath"])
        if not (path / "manifest.json").is_file():
            store.set_assignment_state(
                row["extensionId"], profile_id, store.STATE_FAILED,
                last_error=f"payload missing at {path}")
            continue
        out.append((row["extensionId"], str(path)))
    return out


def mark_applied(profile_id: str, ext_uuids: list[str]) -> None:
    """A launch carried these extensions on its command line.

    Applied is not verified. This records that the browser was *told* to load
    them; ``verify_profile`` records what it actually did.
    """
    for ext_uuid in ext_uuids:
        store.set_assignment_state(
            ext_uuid, profile_id, store.STATE_ASSIGNED, applied=True)


def verify_profile(profile_id: str, observed_ids: list[str]) -> list[ProfileResult]:
    """Reconcile what the browser reported against what we expected.

    ``observed_ids`` comes from the browser itself -- the extension targets it
    exposes over CDP. An expected ID that is absent stays unverified with a
    reason; an ID that matches moves to ``installed_verified``. This is the
    only transition into that state anywhere in the package.
    """
    out: list[ProfileResult] = []
    observed = {i for i in observed_ids if i}
    profile = get_profile(profile_id)
    name = str((profile or {}).get("name") or "")
    for row in store.list_assignments(profile_id=profile_id, enabled_only=True):
        ext = store.get_extension(row["extensionId"])
        if ext is None:
            continue
        expected = ext.get("extensionId") or predict_unpacked_id(ext["payloadPath"])
        if expected in observed:
            store.set_assignment_state(
                row["extensionId"], profile_id, store.STATE_INSTALLED_VERIFIED,
                observed_ext_id=expected, observed_version=ext["version"],
                applied=True)
            out.append(ProfileResult(
                profile_id, True, store.STATE_INSTALLED_VERIFIED,
                f"{ext['name']} {ext['version']} running as {expected}.", name))
        else:
            store.set_assignment_state(
                row["extensionId"], profile_id, store.STATE_FAILED,
                last_error=(
                    f"the browser did not report extension {expected}; "
                    f"it reported {sorted(observed) or 'none'}"))
            out.append(ProfileResult(
                profile_id, False, store.STATE_FAILED,
                f"{ext['name']} was passed to the browser but the browser did "
                f"not report it. Expected {expected}.", name))
    return out


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

def delete(ext_uuid: str, *, force: bool = False) -> dict[str, Any]:
    """Remove an extension and its managed files.

    Refuses by default while profiles still reference it, because deleting the
    payload out from under an assigned profile turns its next launch into a
    browser error rather than a missing extension.
    """
    row = store.get_extension(ext_uuid)
    if row is None:
        raise PackageError(f"No extension {ext_uuid}.", code="not_found")
    assignments = store.list_assignments(ext_uuid=ext_uuid)
    if assignments and not force:
        names = []
        for a in assignments[:5]:
            p = get_profile(a["profileId"])
            names.append(str((p or {}).get("name") or a["profileId"]))
        raise PackageError(
            f"{row['name']} is still assigned to {len(assignments)} profile(s) "
            f"({', '.join(names)}). Remove those assignments first, or delete "
            f"with force to remove them too.", code="still_assigned")

    # Another extension row pointing at the same payload means the files are
    # shared and must survive this delete.
    shared = store.reference_count(row["payloadPath"]) > 1
    store.delete_extension(ext_uuid)
    removed_files = False
    if not shared:
        root = extensions_dir() / ext_uuid
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)
            removed_files = True
    return {
        "deleted": True,
        "removedAssignments": len(assignments),
        "filesRemoved": removed_files,
        "filesKeptBecauseShared": shared,
    }
