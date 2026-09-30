#!/usr/bin/env python3
"""Build a .deb without dpkg-deb.

F12's first blocker was not signing, it was that ``build-deb.sh`` **could not
be run on the machine the product is developed on**. Arch has no ``dpkg-deb``,
so every packaging change shipped unverified and the only place the package
was ever built was a CI job nobody watched. A release process that cannot be
exercised is a release process that fails on the day it matters.

A ``.deb`` is not a dpkg-specific format. It is an ``ar`` archive holding
exactly three members, in order:

    debian-binary     the text "2.0\\n"
    control.tar.gz    DEBIAN/ -- control, postinst, md5sums, conffiles
    data.tar.gz       the filesystem tree, paths relative to /

All three are standard formats Python can write, so this builds the same
package on Arch, on Debian, and in a container, and ``dpkg-deb -I`` reads the
result. When ``dpkg-deb`` *is* present the shell wrapper still prefers it --
this is a fallback that makes the process runnable everywhere, not a
replacement for the canonical tool.

Deterministic on purpose: fixed mtimes, sorted entries, normalised
ownership. Two builds of the same tree produce byte-identical output, which
is what makes "the signature still matches" a meaningful check rather than a
tautology.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import os
import stat
import sys
import tarfile
from pathlib import Path

#: Fixed timestamp for every entry. Reproducible builds need a constant, and
#: 0 is unambiguous where a "sensible default" would vary by machine.
EPOCH = 0


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Normalise an entry so the build does not depend on who ran it."""
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = EPOCH
    return info


def _gz_tar(build: "callable") -> bytes:
    """A gzip-compressed tar, with the gzip mtime pinned for reproducibility."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        build(tar)
    out = io.BytesIO()
    # mtime=0: gzip stores its own timestamp, which would otherwise change
    # every build and defeat the point of the normalisation above.
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def _ar_member(name: str, payload: bytes) -> bytes:
    """One ar member: a 60-byte header, the data, and even-length padding."""
    header = (
        f"{name:<16}"
        f"{EPOCH:<12}"
        f"{0:<6}{0:<6}"
        f"{'100644':<8}"
        f"{len(payload):<10}"
        "`\n"
    ).encode()
    assert len(header) == 60, len(header)
    pad = b"\n" if len(payload) % 2 else b""
    return header + payload + pad


def build_deb(stage: Path, output: Path) -> Path:
    """Package a staged tree (`stage/DEBIAN/...` + the filesystem) as a .deb."""
    stage = Path(stage)
    control_dir = stage / "DEBIAN"
    if not (control_dir / "control").is_file():
        raise SystemExit(f"no DEBIAN/control under {stage}")

    # md5sums is generated rather than hand-maintained: a stale one is worse
    # than none, because `dpkg --verify` would then report intact files as
    # modified and an operator would learn to ignore it.
    (control_dir / "md5sums").write_bytes(_md5sums_for_stage(stage))

    control_tar = _gz_tar(lambda tar: _add_control(tar, control_dir))
    data_tar = _gz_tar(lambda tar: _add_data(tar, stage))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        fh.write(b"!<arch>\n")
        fh.write(_ar_member("debian-binary", b"2.0\n"))
        fh.write(_ar_member("control.tar.gz", control_tar))
        fh.write(_ar_member("data.tar.gz", data_tar))
    return output


def _md5sums_for_stage(stage: Path) -> bytes:
    lines = []
    for dirpath, dirnames, filenames in os.walk(stage):
        dirnames[:] = sorted(d for d in dirnames if d != "DEBIAN")
        if Path(dirpath) == stage:
            dirnames[:] = [d for d in dirnames if d != "DEBIAN"]
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(stage).as_posix()
            if rel.startswith("DEBIAN/"):
                continue
            digest = hashlib.md5(p.read_bytes()).hexdigest()  # noqa: S324 - dpkg's format
            lines.append(f"{digest}  {rel}")
    return ("\n".join(lines) + "\n").encode()


def _add_control(tar: tarfile.TarFile, control_dir: Path) -> None:
    root = tarfile.TarInfo("./")
    root.type = tarfile.DIRTYPE
    root.mode = 0o755
    tar.addfile(_tar_filter(root))
    for name in sorted(p.name for p in control_dir.iterdir() if p.is_file()):
        src = control_dir / name
        info = tar.gettarinfo(str(src), arcname=f"./{name}")
        info = _tar_filter(info)
        # postinst/prerm/postrm must be executable or dpkg refuses to run them.
        info.mode = 0o755 if name in {"postinst", "preinst", "prerm", "postrm"} else 0o644
        with src.open("rb") as fh:
            tar.addfile(info, fh)


def _add_data(tar: tarfile.TarFile, stage: Path) -> None:
    dirs: set[str] = set()
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(stage):
        dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) == stage and d == "DEBIAN"))
        rel_dir = Path(dirpath).relative_to(stage)
        if str(rel_dir) != ".":
            dirs.add(f"./{rel_dir.as_posix()}")
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.relative_to(stage).parts[:1] == ("DEBIAN",):
                continue
            files.append(p)

    # dpkg-deb emits the archive root as a member of its own, and `dpkg-deb
    # -c` lists it. Omitting it made the two builders' file listings differ by
    # exactly one line, which is the sort of difference that is invisible
    # until something compares them -- here, the CI step that does.
    root_info = tarfile.TarInfo("./")
    root_info.type = tarfile.DIRTYPE
    root_info.mode = 0o755
    tar.addfile(_tar_filter(root_info))
    for d in sorted(dirs):
        info = tarfile.TarInfo(d + "/")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tar.addfile(_tar_filter(info))
    for src in sorted(files, key=lambda p: str(p)):
        arcname = f"./{src.relative_to(stage).as_posix()}"
        info = tar.gettarinfo(str(src), arcname=arcname)
        info = _tar_filter(info)
        info.mode = 0o755 if src.stat().st_mode & stat.S_IXUSR else 0o644
        with src.open("rb") as fh:
            tar.addfile(info, fh)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <stage-dir> <output.deb>", file=sys.stderr)
        return 2
    out = build_deb(Path(argv[1]), Path(argv[2]))
    print(f"built {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
