#!/usr/bin/env python3
"""Verify a published repository end to end, offline.

Checks the chain apt relies on, in the order a client would:

    1. InRelease / Release.gpg carry a good signature from the expected key
    2. Release's SHA-256 entries match the indices on disk
    3. every SHA-256 in Packages matches the .deb in the pool
    4. every delta in deltas.json applies and reconstructs its target exactly

Exists so "signed" is a thing that was checked rather than a thing that was
claimed. It is run in CI against a throwaway key, and can be run by an
operator against a real mirror before trusting it.

Exit code is 0 only when every stage passes. Each failure prints the file and
what disagreed, because "verification failed" without a name is the kind of
message people learn to ignore.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.checks += 1
        if ok:
            print(f"  ok    {label}")
        else:
            print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
            self.failures.append(label)
        return ok


def verify_signature(repo: Path, suite: str, gnupghome: str | None,
                     key_id: str | None, c: Checker) -> None:
    dist = repo / "dists" / suite
    env = dict(os.environ)
    if gnupghome:
        env["GNUPGHOME"] = gnupghome

    for name, args in (
        ("InRelease", ["--verify", str(dist / "InRelease")]),
        ("Release.gpg", ["--verify", str(dist / "Release.gpg"), str(dist / "Release")]),
    ):
        path = dist / name
        if not path.is_file():
            c.check(False, f"{name} present")
            continue
        proc = subprocess.run(["gpg", "--batch", "--status-fd", "1", *args],
                              capture_output=True, text=True, env=env)
        good = "[GNUPG:] GOODSIG" in proc.stdout or "[GNUPG:] VALIDSIG" in proc.stdout
        c.check(good and proc.returncode == 0, f"{name} carries a good signature",
                proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")
        if good and key_id:
            c.check(key_id.upper().replace(" ", "") in proc.stdout.replace(" ", "").upper(),
                    f"{name} is signed by the expected key")


def verify_published_keyring(repo: Path, key_id: str | None, c: Checker) -> None:
    """The published key must contain whichever key actually signed.

    This file is the trust root, so nothing signed can vouch for it -- an
    operator compares its fingerprint out of band. What can be checked is
    that it is internally consistent with the repository beside it. Signing
    with a key the published keyring does not carry is the rotation footgun:
    an install that adds the keyring from this channel then rejects the
    channel, and the failure looks like a corrupt download rather than a
    missing key.
    """
    asc = repo / "multiprofile-archive-keyring.asc"
    if not c.check(asc.is_file(), "the public keyring is published"):
        return
    proc = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--import-options", "show-only",
         "--import", str(asc)],
        capture_output=True, text=True,
    )
    fprs = [ln.split(":")[9] for ln in proc.stdout.splitlines()
            if ln.startswith("fpr:")]
    c.check(bool(fprs), "the published keyring parses as a public key",
            proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "")
    if key_id and fprs:
        want = key_id.upper().replace(" ", "")
        c.check(any(f.upper().endswith(want) or want.endswith(f.upper())
                    for f in fprs),
                "the published keyring carries the signing key",
                f"published {', '.join(fprs)}")


def verify_release_hashes(repo: Path, suite: str, c: Checker) -> None:
    dist = repo / "dists" / suite
    release = dist / "Release"
    if not release.is_file():
        c.check(False, "Release present")
        return
    in_sha = False
    listed = 0
    for line in release.read_text().splitlines():
        if line.startswith("SHA256:"):
            in_sha = True
            continue
        if in_sha and line.startswith(" "):
            digest, size, rel = line.split()
            target = (dist / rel).resolve()
            if not target.is_file():
                c.check(False, f"Release lists {rel}", "missing on disk")
                continue
            listed += 1
            c.check(_sha256(target) == digest and target.stat().st_size == int(size),
                    f"Release hash matches {rel}")
        elif in_sha:
            break
    c.check(listed > 0, "Release indexes at least one file")


def verify_packages(repo: Path, suite: str, component: str, arch: str,
                    c: Checker) -> None:
    packages = repo / "dists" / suite / component / f"binary-{arch}" / "Packages"
    if not packages.is_file():
        c.check(False, "Packages present")
        return
    stanzas = [s for s in packages.read_text().split("\n\n") if s.strip()]
    c.check(bool(stanzas), "Packages lists at least one package")
    for stanza in stanzas:
        fields = dict(
            (k.strip(), v.strip())
            for k, _, v in (ln.partition(":") for ln in stanza.splitlines() if ":" in ln)
        )
        name = f"{fields.get('Package')} {fields.get('Version')}"
        deb = repo / fields["Filename"]
        if not deb.is_file():
            c.check(False, f"{name}: package present", str(deb))
            continue
        c.check(_sha256(deb) == fields["SHA256"], f"{name}: SHA256 matches Packages")
        c.check(str(deb.stat().st_size) == fields["Size"], f"{name}: Size matches Packages")


def _released_arches(repo: Path, suite: str) -> list[str]:
    """The architectures the Release claims, so every one of them is checked.

    Defaulting to amd64 alone would have let a published-but-broken arm64
    index pass verification, which is the failure an arm64 operator would
    hit and nobody else.
    """
    release = repo / "dists" / suite / "Release"
    if release.is_file():
        for line in release.read_text().splitlines():
            if line.startswith("Architectures:"):
                arches = line.partition(":")[2].split()
                if arches:
                    return arches
    return ["amd64"]


def verify_deltas(repo: Path, c: Checker) -> None:
    manifest = repo / "deltas" / "deltas.json"
    if not manifest.is_file():
        print("  --    no deltas.json (delta channel not published)")
        return
    if shutil.which("zstd") is None:
        print("  --    zstd not installed; delta application not checked")
        return
    data = json.loads(manifest.read_text())
    entries = data.get("deltas") or []
    c.check(bool(entries), "deltas.json lists at least one delta")
    for entry in entries:
        label = f"delta {entry['package']} {entry['from']} -> {entry['to']}"
        delta = repo / entry["path"]
        if not delta.is_file():
            c.check(False, f"{label}: present")
            continue
        c.check(_sha256(delta) == entry["sha256"], f"{label}: own hash matches")

        old = _find_deb(repo, entry["package"], entry["from"])
        if old is None:
            c.check(False, f"{label}: base package present")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rebuilt.deb"
            proc = subprocess.run(
                ["zstd", "-q", "-d", "--patch-from", str(old), str(delta), "-o", str(out)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                c.check(False, f"{label}: applies", proc.stderr.strip())
                continue
            # The property that makes a delta safe to use: what it
            # reconstructs is byte-identical to the package the signed index
            # vouches for. Anything else and the client throws it away.
            c.check(_sha256(out) == entry["resultSha256"],
                    f"{label}: reconstructs the exact published package")


def _find_deb(repo: Path, package: str, version: str) -> Path | None:
    for deb in (repo / "pool").rglob("*.deb"):
        if deb.name.startswith(f"{package}_{version}_"):
            return deb
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--suite", default="stable")
    ap.add_argument("--component", default="main")
    ap.add_argument("--arch", action="append", default=[], metavar="ARCH",
                    help="verify this architecture's index (repeatable); "
                         "defaults to every architecture the Release names")
    ap.add_argument("--gnupghome")
    ap.add_argument("--key-id", help="fail unless this key made the signature")
    ap.add_argument("--skip-signature", action="store_true",
                    help="for an intentionally unsigned development repository")
    args = ap.parse_args(argv[1:])

    c = Checker()
    print("signature")
    if args.skip_signature:
        print("  --    skipped by request")
    else:
        verify_signature(args.repo, args.suite, args.gnupghome, args.key_id, c)
        verify_published_keyring(args.repo, args.key_id, c)
    print("release indices")
    verify_release_hashes(args.repo, args.suite, c)
    print("packages")
    for arch in args.arch or _released_arches(args.repo, args.suite):
        verify_packages(args.repo, args.suite, args.component, arch, c)
    print("deltas")
    verify_deltas(args.repo, c)

    print()
    if c.failures:
        print(f"FAILED: {len(c.failures)} of {c.checks} checks")
        for f in c.failures:
            print(f"  - {f}")
        return 1
    print(f"OK: {c.checks} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
