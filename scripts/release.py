#!/usr/bin/env python3
"""Publish a signed apt repository, with deltas, from built .deb files.

**F12's update channel.** An unsigned .deb with no update path is not
something a security-conscious buyer installs, and it is the only way a fix
reaches an install that already exists.

What this builds is an ordinary Debian repository, because that is the
mechanism apt already verifies and the one an operator already knows how to
trust:

    pool/main/<pkg>_<version>_<arch>.deb
    dists/<suite>/Release            checksums of every index
    dists/<suite>/Release.gpg        detached signature over Release
    dists/<suite>/InRelease          the same content, inline-signed
    dists/<suite>/main/binary-<arch>/Packages{,.gz}

**Where the trust actually sits.** apt verifies `InRelease`, which carries the
hashes of `Packages`, which carries the SHA-256 of each `.deb`. So one
signature covers the whole chain, and a tampered package fails before dpkg
sees it. Per-package signatures (`debsigs`) are *not* used: almost nothing
verifies them by default, and shipping a signature that nobody checks is
worse than not shipping one, because it reads as protection.

**Deltas.** `--patch-from` in zstd produces a binary delta between two
versions of the same package; zstd is in Debian main, so the client needs
nothing unusual. The deltas and their hashes are listed in `deltas.json`,
whose own SHA-256 goes into the signed `Release`, so a delta cannot be
substituted without breaking the same signature that protects the packages.
The updater verifies the *reconstructed* .deb against the hash in `Packages`
before installing, and falls back to the full download when anything
disagrees -- a delta is an optimisation and is never the thing trusted.

Signing key: passed by id. This script never reads a private key from the
repository, and CI uses a throwaway key generated per run.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CONTROL_FIELD_ORDER = (
    "Package", "Version", "Architecture", "Maintainer", "Installed-Size",
    "Depends", "Recommends", "Suggests", "Section", "Priority",
    "Homepage", "Description",
)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _control_of(deb: Path) -> dict[str, str]:
    """Read DEBIAN/control out of a .deb without dpkg.

    `dpkg-deb -f` would be the obvious call and is unavailable on the machine
    this is developed on -- the same reason build_deb.py exists.
    """
    import io
    import tarfile

    with deb.open("rb") as fh:
        if fh.read(8) != b"!<arch>\n":
            raise SystemExit(f"{deb} is not an ar archive")
        while True:
            header = fh.read(60)
            if len(header) < 60:
                raise SystemExit(f"{deb}: no control member found")
            name = header[:16].decode().strip()
            size = int(header[48:58].decode().strip())
            payload = fh.read(size)
            if size % 2:
                fh.read(1)
            if name.startswith("control.tar"):
                break
    mode = "r:gz" if name.endswith(".gz") else "r:*"
    with tarfile.open(fileobj=io.BytesIO(payload), mode=mode) as tar:
        member = next(
            (m for m in tar.getmembers() if m.name.lstrip("./") == "control"), None
        )
        if member is None:
            raise SystemExit(f"{deb}: control.tar has no control file")
        text = tar.extractfile(member).read().decode()
    fields: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] += "\n" + line
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    return fields


def build_packages_index(repo: Path, suite: str, component: str, arch: str) -> Path:
    """Write Packages and Packages.gz for every .deb in the pool."""
    pool = repo / "pool" / component
    binary_dir = repo / "dists" / suite / component / f"binary-{arch}"
    binary_dir.mkdir(parents=True, exist_ok=True)

    stanzas: list[str] = []
    for deb in sorted(pool.glob("*.deb")):
        fields = _control_of(deb)
        fields["Filename"] = str(deb.relative_to(repo))
        fields["Size"] = str(deb.stat().st_size)
        fields["SHA256"] = _sha256(deb)
        lines = []
        for key in CONTROL_FIELD_ORDER:
            if key in fields and key != "Description":
                lines.append(f"{key}: {fields[key]}")
        for key in ("Filename", "Size", "SHA256"):
            lines.append(f"{key}: {fields[key]}")
        if "Description" in fields:
            lines.append(f"Description: {fields['Description']}")
        stanzas.append("\n".join(lines))

    packages = ("\n\n".join(stanzas) + "\n") if stanzas else ""
    path = binary_dir / "Packages"
    path.write_text(packages, encoding="utf-8")
    with gzip.GzipFile(str(binary_dir / "Packages.gz"), "wb", mtime=0) as gz:
        gz.write(packages.encode())
    return path


def build_deltas(repo: Path, component: str) -> Path | None:
    """zstd deltas between consecutive versions of each package."""
    if shutil.which("zstd") is None:
        print("zstd not found; skipping deltas", file=sys.stderr)
        return None
    pool = repo / "pool" / component
    delta_dir = repo / "deltas"
    delta_dir.mkdir(parents=True, exist_ok=True)

    by_package: dict[str, list[Path]] = {}
    for deb in sorted(pool.glob("*.deb")):
        by_package.setdefault(_control_of(deb)["Package"], []).append(deb)

    entries = []
    for package, debs in sorted(by_package.items()):
        ordered = sorted(debs, key=lambda p: _control_of(p)["Version"])
        for old, new in zip(ordered, ordered[1:], strict=False):
            ov, nv = _control_of(old)["Version"], _control_of(new)["Version"]
            name = f"{package}_{ov}_to_{nv}.zst"
            out = delta_dir / name
            _run(["zstd", "-q", "-f", "--patch-from", str(old), str(new), "-o", str(out)])
            entries.append({
                "package": package,
                "from": ov,
                "to": nv,
                "path": str(out.relative_to(repo)),
                "size": out.stat().st_size,
                "sha256": _sha256(out),
                # The hash of the .deb this delta must reconstruct. The
                # updater checks the result against it and discards the delta
                # if it disagrees, so a corrupt delta costs a download and
                # never an install.
                "resultSha256": _sha256(new),
                "resultSize": new.stat().st_size,
                "fullSize": new.stat().st_size,
            })
            saving = 100 - (out.stat().st_size * 100 // max(1, new.stat().st_size))
            print(f"delta {package} {ov} -> {nv}: "
                  f"{out.stat().st_size} bytes ({saving}% smaller than the full package)")

    manifest = delta_dir / "deltas.json"
    manifest.write_text(json.dumps({"version": 1, "deltas": entries}, indent=2) + "\n")
    return manifest


def build_release(repo: Path, suite: str, component: str, arch: str,
                  origin: str, extra_files: list[Path]) -> Path:
    """The Release file: one signed document covering every index."""
    dist = repo / "dists" / suite
    indexed: list[Path] = [
        dist / component / f"binary-{arch}" / "Packages",
        dist / component / f"binary-{arch}" / "Packages.gz",
        *extra_files,
    ]
    lines = [
        f"Origin: {origin}",
        f"Label: {origin}",
        f"Suite: {suite}",
        f"Codename: {suite}",
        f"Architectures: {arch}",
        f"Components: {component}",
        f"Date: {datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S UTC')}",
        "Description: Multiprofile Linux update channel",
        "SHA256:",
    ]
    for path in indexed:
        if not path.is_file():
            continue
        # Paths are relative to dists/<suite>/ for indices, and to the repo
        # root for anything outside it (the delta manifest), which is why the
        # relative_to falls back.
        try:
            rel = path.relative_to(dist)
        except ValueError:
            rel = Path("..") / ".." / path.relative_to(repo)
        lines.append(f" {_sha256(path)} {path.stat().st_size} {rel.as_posix()}")
    release = dist / "Release"
    release.parent.mkdir(parents=True, exist_ok=True)
    release.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return release


def sign_release(release: Path, key_id: str, gnupghome: str | None) -> tuple[Path, Path]:
    """Release.gpg (detached) and InRelease (inline). apt prefers InRelease."""
    env = {"GNUPGHOME": gnupghome} if gnupghome else None
    detached = release.with_name("Release.gpg")
    inline = release.with_name("InRelease")
    for target in (detached, inline):
        target.unlink(missing_ok=True)
    base = ["gpg", "--batch", "--yes", "--local-user", key_id]
    _run([*base, "--armor", "--detach-sign", "--output", str(detached), str(release)],
         env=_env(env))
    _run([*base, "--clearsign", "--output", str(inline), str(release)], env=_env(env))
    return detached, inline


def _env(overrides: dict[str, str] | None) -> dict[str, str] | None:
    if overrides is None:
        return None
    import os

    merged = dict(os.environ)
    merged.update(overrides)
    return merged


def export_key(repo: Path, key_ids: str | list[str], gnupghome: str | None) -> Path:
    """The public key(s) an installer adds to /etc/apt/keyrings.

    More than one may be published at a time, and that is what makes a key
    rotation survivable. The keyring is pinned on the client and is fetched
    out of band, so an install only ever trusts the keys that were in the
    keyring it already has. Signing with a new key before clients hold it
    stops every install updating -- including the update that would have
    delivered the new key. So a rotation publishes both keys, waits for the
    fleet to pick the keyring up, and only then switches which key signs.
    `gpgv` accepts a keyring with several keys and succeeds on any one of
    them, so the overlap window costs nothing while it is open.
    """
    if isinstance(key_ids, str):
        key_ids = [key_ids]
    out = repo / "multiprofile-archive-keyring.asc"
    blocks = []
    for key_id in key_ids:
        proc = _run(["gpg", "--batch", "--yes", "--armor", "--export", key_id],
                    env=_env({"GNUPGHOME": gnupghome} if gnupghome else None))
        if "BEGIN PGP PUBLIC KEY BLOCK" not in proc.stdout:
            raise SystemExit(f"gpg exported no public key for {key_id}")
        blocks.append(proc.stdout.strip() + "\n")
    out.write_text("".join(blocks), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path, help="repository root to write")
    ap.add_argument("--deb", action="append", default=[], type=Path,
                    help="a .deb to add to the pool (repeatable)")
    ap.add_argument("--suite", default="stable")
    ap.add_argument("--component", default="main")
    ap.add_argument("--arch", default="amd64")
    ap.add_argument("--origin", default="Multiprofile")
    ap.add_argument("--key-id", help="gpg key to sign with; unsigned when omitted")
    ap.add_argument("--gnupghome", help="GNUPGHOME for the signing key")
    ap.add_argument("--also-publish-key", action="append", default=[],
                    metavar="KEY_ID",
                    help="publish this public key alongside the signing key "
                         "without signing with it (repeatable). Use it during "
                         "a key rotation so installs trust both the outgoing "
                         "and the incoming key; see docs/UPDATES.md 4.6.")
    args = ap.parse_args(argv[1:])

    repo: Path = args.repo
    pool = repo / "pool" / args.component
    pool.mkdir(parents=True, exist_ok=True)
    for deb in args.deb:
        if not deb.is_file():
            raise SystemExit(f"no such package: {deb}")
        # Named from the control fields, not from the input filename. A
        # Debian pool file is `package_version_arch.deb` by convention, and
        # several things downstream rely on it -- the delta verifier finds a
        # base package by that name. Copying `v1.deb` in as `v1.deb` produced
        # a repository whose indices were all correct and whose base package
        # could not be located, which is exactly the kind of break that only
        # shows up once something actually tries to use the channel.
        fields = _control_of(deb)
        name = (f"{fields['Package']}_{fields['Version']}_"
                f"{fields.get('Architecture', 'all')}.deb")
        shutil.copy2(deb, pool / name)

    build_packages_index(repo, args.suite, args.component, args.arch)
    manifest = build_deltas(repo, args.component)
    release = build_release(repo, args.suite, args.component, args.arch,
                            args.origin, [manifest] if manifest else [])
    print(f"wrote {release.relative_to(repo)}")

    if args.key_id:
        detached, inline = sign_release(release, args.key_id, args.gnupghome)
        published = [args.key_id] + [k for k in args.also_publish_key
                                     if k != args.key_id]
        key = export_key(repo, published, args.gnupghome)
        print(f"signed: {detached.name}, {inline.name}; "
              f"{len(published)} public key(s) at {key.name}")
    else:
        # Said out loud rather than produced quietly: an unsigned repository
        # is a development convenience and apt will refuse it without
        # [trusted=yes], which is the right amount of friction.
        print("UNSIGNED repository (no --key-id): apt will refuse this "
              "without [trusted=yes]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
