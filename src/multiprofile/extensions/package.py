"""Turn an archive or a folder into a validated, managed extension payload.

This is the only place untrusted bytes enter the feature, so it is written as
a gate rather than a convenience. Every rejection below is a real failure mode
of ZIP extraction, not a hypothetical:

* **Path traversal.** An entry named ``../../.ssh/authorized_keys`` writes
  outside the destination. Checked by resolving the joined path and requiring
  it stay under the target root.
* **Absolute paths.** ``/etc/passwd`` as a member name does the same thing by
  a different route.
* **Escaping symlinks.** A ZIP can carry a symlink, and extracting one that
  points at ``/`` turns every later write into a write outside the tree.
  Symlinks are refused outright -- an extension does not need them.
* **Oversized extraction (zip bomb).** The compressed size says nothing about
  the uncompressed size. Both the declared total and the bytes actually
  written are capped, because a declared size is attacker-controlled.
* **Too many members.** A flat archive of a million files costs a million
  inodes before any size cap trips.
* **Manifest absent, unparseable, or ambiguous.** An archive with two
  extension roots has no single answer to "which extension is this", so it is
  refused rather than guessed at.
* **Missing referenced files.** A manifest naming ``popup.html`` that is not
  in the archive installs an extension that is broken at runtime, which the
  browser reports much later and much less clearly.

CRX is deliberately **not** handled here. See ``crx_rejection``.
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Hard caps. Generous for a real extension, fatal for an archive designed to
#: exhaust the disk. A 256 MiB extension is not a thing anyone ships.
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_SINGLE_BYTES = 64 * 1024 * 1024
#: Beyond this the archive is a bomb, whatever it declares.
MAX_COMPRESSION_RATIO = 200

#: Manifest versions this product will install. MV1 has been dead since 2014
#: and no current engine runs it.
SUPPORTED_MANIFEST_VERSIONS = (2, 3)

_MANIFEST = "manifest.json"

#: Read from the manifest and shown before installation. Not exhaustive --
#: it is what the manifest declares, which is what the browser will grant.
_PERMISSION_KEYS = ("permissions", "optional_permissions", "host_permissions",
                    "optional_host_permissions")


class PackageError(Exception):
    """A rejection with a reason an operator can act on."""

    def __init__(self, message: str, *, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ExtensionManifest:
    """The parts of ``manifest.json`` this product acts on."""

    name: str
    version: str
    manifest_version: int
    description: str = ""
    permissions: list[str] = field(default_factory=list)
    host_permissions: list[str] = field(default_factory=list)
    #: A manifest ``key`` pins the extension ID. Without one the ID is derived
    #: from the install path, which is why payload paths here are stable.
    has_key: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "manifestVersion": self.manifest_version,
            "description": self.description,
            "permissions": list(self.permissions),
            "hostPermissions": list(self.host_permissions),
            "hasKey": self.has_key,
        }


def crx_rejection(name: str = "this file") -> PackageError:
    """Why a .crx is not accepted, and what to do instead.

    A .crx is a signed container: a header with a public key and a signature,
    then a ZIP. Unzipping it and loading the result as unpacked **changes the
    extension's identity** -- the ID stops being the one the signature
    attests and becomes one derived from the install path -- and it discards
    the signature check entirely. Presenting that as "installed from CRX"
    would be a false claim about provenance, so it is refused.

    Installing a .crx the way Chrome does requires either the Web Store flow
    or enterprise policy (``ExtensionInstallForcelist`` with a hosted update
    manifest). The Web Store flow is offered from the Extensions page, per
    profile. Third-party "CRX downloader" sites are not used: they are
    unverified mirrors of other people's code, and routing installs through
    one is exactly the supply-chain substitution this product should not make.
    """
    return PackageError(
        f"{name} is a CRX (signed Chrome package). Extracting it and loading "
        f"the contents unpacked would discard its signature and change its "
        f"extension ID, so this product does not call that an install. "
        f"Supported alternatives: install from the Chrome Web Store in a "
        f"chosen profile (Extensions -> Install from Web Store), or import the "
        f"extension's unpacked source as a ZIP or folder.",
        code="crx_unsupported",
    )


def parse_manifest(data: Any, *, where: str = _MANIFEST) -> ExtensionManifest:
    """Validate a parsed manifest. Raises ``PackageError`` with the reason."""
    if not isinstance(data, dict):
        raise PackageError(f"{where} is not a JSON object.", code="manifest_shape")

    raw_mv = data.get("manifest_version")
    if raw_mv is None:
        raise PackageError(
            f"{where} has no manifest_version. Every Chrome extension must "
            f"declare one.", code="manifest_version_missing")
    try:
        mv = int(raw_mv)
    except (TypeError, ValueError):
        raise PackageError(
            f"{where} manifest_version is {raw_mv!r}, which is not a number.",
            code="manifest_version_invalid") from None
    if mv not in SUPPORTED_MANIFEST_VERSIONS:
        raise PackageError(
            f"{where} declares manifest_version {mv}. This browser runs "
            f"{' and '.join(str(v) for v in SUPPORTED_MANIFEST_VERSIONS)}.",
            code="manifest_version_unsupported")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PackageError(f"{where} has no usable name.", code="manifest_name")
    version = data.get("version")
    if not isinstance(version, (str, int, float)) or not str(version).strip():
        raise PackageError(f"{where} has no usable version.", code="manifest_version")

    perms = []
    hosts = []
    for key in _PERMISSION_KEYS:
        got = data.get(key)
        if not isinstance(got, list):
            continue
        target = hosts if "host" in key else perms
        for p in got:
            if isinstance(p, str) and p not in target:
                target.append(p)

    return ExtensionManifest(
        name=name.strip(),
        version=str(version).strip(),
        manifest_version=mv,
        description=str(data.get("description") or "").strip(),
        permissions=perms,
        host_permissions=hosts,
        has_key=bool(data.get("key")),
        raw=data,
    )


def referenced_files(manifest: dict[str, Any]) -> list[str]:
    """Files the manifest names, which therefore have to exist.

    A manifest pointing at a popup or a service worker that is not in the
    archive produces an extension the browser loads and then fails at, long
    after the import reported success.
    """
    out: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value and not value.startswith(("http:", "https:", "data:")):
            out.append(value.lstrip("/"))

    add((manifest.get("action") or {}).get("default_popup")
        if isinstance(manifest.get("action"), dict) else None)
    add((manifest.get("browser_action") or {}).get("default_popup")
        if isinstance(manifest.get("browser_action"), dict) else None)
    bg = manifest.get("background")
    if isinstance(bg, dict):
        add(bg.get("service_worker"))
        add(bg.get("page"))
        for s in bg.get("scripts") or []:
            add(s)
    add(manifest.get("options_page"))
    opts = manifest.get("options_ui")
    if isinstance(opts, dict):
        add(opts.get("page"))
    for cs in manifest.get("content_scripts") or []:
        if not isinstance(cs, dict):
            continue
        for s in (cs.get("js") or []) + (cs.get("css") or []):
            add(s)
    for war in manifest.get("web_accessible_resources") or []:
        if isinstance(war, dict):
            for r in war.get("resources") or []:
                add(r)
    icons = manifest.get("icons")
    if isinstance(icons, dict):
        for v in icons.values():
            add(v)
    seen: set[str] = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# ---------------------------------------------------------------------------
# archive inspection
# ---------------------------------------------------------------------------

def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    """ZIP stores the unix mode in the top 16 bits of external_attr."""
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def find_extension_root(names: list[str]) -> str:
    """Which directory inside the archive is the extension?

    Accepts a manifest at the top level, or inside a single wrapping
    directory -- which is what every "Download ZIP" button produces. Refuses
    an archive holding more than one extension root, because there is no
    correct answer to which one was meant and picking the first silently
    installs something the operator did not choose.
    """
    roots: list[str] = []
    for name in names:
        norm = name.replace("\\", "/").lstrip("./")
        parts = [p for p in norm.split("/") if p not in ("", ".")]
        if not parts or parts[-1] != _MANIFEST:
            continue
        depth = len(parts) - 1
        if depth == 0:
            roots.append("")
        elif depth == 1:
            roots.append(parts[0])
        # Deeper manifests are vendored dependencies or examples, not the root.
    uniq = sorted(set(roots))
    if not uniq:
        raise PackageError(
            "No manifest.json at the top level or in a single wrapping folder. "
            "This does not look like an extension package.",
            code="manifest_absent")
    if len(uniq) > 1:
        shown = ", ".join(r or "<top level>" for r in uniq[:5])
        raise PackageError(
            f"This archive contains {len(uniq)} extension roots ({shown}). "
            f"Import one extension at a time -- choosing for you could install "
            f"something you did not pick.",
            code="ambiguous_root")
    return uniq[0]


@dataclass
class ArchiveReport:
    root: str
    manifest: ExtensionManifest
    members: int
    total_bytes: int
    missing: list[str] = field(default_factory=list)


def inspect_zip(zip_path: str | Path) -> ArchiveReport:
    """Validate an archive **without** writing anything to disk.

    Inspection is separate from extraction so that permissions can be shown
    before installation, and so a hostile archive is refused before a single
    byte of it lands in the filesystem.
    """
    path = Path(zip_path)
    if not path.is_file():
        raise PackageError(f"{path} is not a file.", code="not_a_file")
    if path.suffix.lower() == ".crx":
        raise crx_rejection(path.name)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            names = [i.filename for i in infos]
            if len(infos) > MAX_MEMBERS:
                raise PackageError(
                    f"Archive holds {len(infos)} entries (limit {MAX_MEMBERS}).",
                    code="too_many_members")

            total = 0
            for info in infos:
                if _is_symlink_member(info):
                    raise PackageError(
                        f"Archive contains a symlink ({info.filename}). Extracting "
                        f"one can redirect later writes outside the extension "
                        f"directory, and no extension needs it.",
                        code="symlink")
                _safe_member_name(info.filename)
                if info.file_size > MAX_SINGLE_BYTES:
                    raise PackageError(
                        f"{info.filename} unpacks to {info.file_size} bytes "
                        f"(limit {MAX_SINGLE_BYTES}).", code="member_too_large")
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO and info.file_size > 1024 * 1024:
                        raise PackageError(
                            f"{info.filename} expands {ratio:.0f}x "
                            f"({info.compress_size} -> {info.file_size} bytes), "
                            f"which is a decompression bomb rather than an "
                            f"extension.", code="compression_ratio")
                total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise PackageError(
                    f"Archive unpacks to {total} bytes (limit {MAX_TOTAL_BYTES}).",
                    code="too_large")

            root = find_extension_root(names)
            prefix = f"{root}/" if root else ""
            try:
                raw = zf.read(f"{prefix}{_MANIFEST}")
            except KeyError:
                raise PackageError("manifest.json disappeared between listing "
                                   "and reading the archive.",
                                   code="manifest_absent") from None
            manifest = _parse_manifest_bytes(raw)

            present = {
                n.replace("\\", "/")[len(prefix):]
                for n in names if n.replace("\\", "/").startswith(prefix)
            }
            missing = [r for r in referenced_files(manifest.raw) if r not in present]
            return ArchiveReport(root, manifest, len(infos), total, missing)
    except zipfile.BadZipFile:
        raise PackageError(f"{path.name} is not a readable ZIP archive.",
                           code="bad_zip") from None


def _parse_manifest_bytes(raw: bytes) -> ExtensionManifest:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise PackageError("manifest.json is not valid UTF-8.",
                           code="manifest_encoding") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PackageError(f"manifest.json is not valid JSON: {e}.",
                           code="manifest_json") from None
    return parse_manifest(data)


def _safe_member_name(name: str) -> str:
    """Reject anything that would write outside the destination."""
    norm = name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        raise PackageError(f"Archive entry {name!r} is an absolute path.",
                           code="absolute_path")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PackageError(
            f"Archive entry {name!r} escapes the extraction directory.",
            code="path_traversal")
    return "/".join(parts)


def extract_zip(zip_path: str | Path, dest: str | Path) -> ArchiveReport:
    """Validate, then extract the extension root into ``dest``.

    ``dest`` is created empty. Every member is re-checked while writing, and
    the bytes actually written are counted -- the declared ``file_size`` is
    attacker-controlled, so a cap that trusts it is not a cap.
    """
    report = inspect_zip(zip_path)
    target = Path(dest)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    root = report.root
    prefix = f"{root}/" if root else ""
    written = 0

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            norm = info.filename.replace("\\", "/")
            if prefix and not norm.startswith(prefix):
                continue
            rel = _safe_member_name(norm[len(prefix):])
            if not rel:
                continue
            out = (target / rel).resolve()
            # The decisive check: where did the path actually land?
            if not str(out).startswith(str(target.resolve()) + os.sep):
                raise PackageError(
                    f"Archive entry {info.filename!r} resolved outside the "
                    f"extraction directory.", code="path_traversal")
            if norm.endswith("/"):
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_TOTAL_BYTES:
                        raise PackageError(
                            f"Archive wrote more than {MAX_TOTAL_BYTES} bytes; "
                            f"its declared sizes understated it.",
                            code="too_large")
                    dst.write(chunk)
    return report


def inspect_folder(folder: str | Path) -> ExtensionManifest:
    """Validate an unpacked extension directory in place.

    Used by *Load unpacked*. The folder stays where the operator put it only
    long enough to be validated; the manager copies it into managed storage so
    the install path -- and therefore the extension ID -- is stable and does
    not depend on a directory the operator may move or delete.
    """
    path = Path(folder)
    if not path.is_dir():
        raise PackageError(f"{path} is not a directory.", code="not_a_dir")
    manifest_path = path / _MANIFEST
    if not manifest_path.is_file():
        raise PackageError(
            f"No manifest.json in {path.name}. Select the folder that contains "
            f"manifest.json.", code="manifest_absent")
    manifest = _parse_manifest_bytes(manifest_path.read_bytes())
    missing = [
        r for r in referenced_files(manifest.raw)
        if not (path / r).exists()
    ]
    if missing:
        raise PackageError(
            f"manifest.json references {len(missing)} file(s) that are not "
            f"present: {', '.join(missing[:5])}.", code="missing_files")
    return manifest


def copy_folder(src: str | Path, dest: str | Path) -> int:
    """Copy an unpacked extension into managed storage, refusing symlinks.

    A symlink inside the source would make the managed payload depend on a
    path outside it, so the payload could change or vanish under the browser.
    """
    source = Path(src)
    target = Path(dest)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    total = 0
    count = 0
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            raise PackageError(
                f"{item.relative_to(source)} is a symlink. Managed extension "
                f"storage holds real files only, so the payload cannot change "
                f"underneath the browser.", code="symlink")
        rel = item.relative_to(source)
        out = target / rel
        if item.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        count += 1
        if count > MAX_MEMBERS:
            raise PackageError(f"Folder holds more than {MAX_MEMBERS} files.",
                               code="too_many_members")
        total += item.stat().st_size
        if total > MAX_TOTAL_BYTES:
            raise PackageError(
                f"Folder is larger than {MAX_TOTAL_BYTES} bytes.", code="too_large")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, out)
    return total
