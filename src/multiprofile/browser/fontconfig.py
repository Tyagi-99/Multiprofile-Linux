"""Give each profile the font set its claimed platform would have.

Font enumeration is one of the loudest surfaces in fingerprinting, and it was
entirely unaddressed. Measured on this host, a profile reporting
``navigator.platform === 'Win32'`` and ``sec-ch-ua-platform: "Windows"``
enumerated **zero** Windows fonts and **thirteen** Linux ones::

    win: []
    lin: [Liberation Sans, Liberation Serif, Liberation Mono, Noto Sans,
          Noto Serif, Nimbus Sans, Nimbus Roman, URW Bookman,
          C059, P052, Z003, D050000L, Standard Symbols PS]

The last five are the decisive ones. ``C059``, ``P052``, ``Z003``,
``D050000L`` and ``Standard Symbols PS`` are the URW base-35 clones shipped by
ghostscript. They exist on no Windows machine. A site does not have to weigh
probabilities about a missing Calibri; those five names are positive proof
that a self-declared Windows box is a Linux one.

What ``evasion.js`` did here made it worse rather than better. It picked one
or two names out of a hardcoded eight-font pool and forced ``measureText`` to
fall back for them, so a "Windows" profile hid *Georgia* while announcing
D050000L.

Why this is not a JavaScript hook
---------------------------------
The obvious fix is to filter ``measureText`` and ``document.fonts.check``.
Measured here, canvas and DOM detection agree exactly today, so a hook that
moved only canvas would introduce a *new* contradiction. And the DOM half
cannot be moved honestly: reporting a width the engine did not lay out
desynchronises measurement from rendering on any page that uses the font for
real.

Chromium resolves local fonts through fontconfig, which honours
``FONTCONFIG_FILE``. Measured against stock Chromium 151 on this host, with a
restricted config in the child's environment::

    DEFAULT     [C059, P052, Z003, D050000L, Standard Symbols PS, Nimbus Roman,
                 Nimbus Sans, Liberation Sans, Noto Sans, Arial, Times New Roman]
    RESTRICTED  [Liberation Sans, Noto Sans]

So the engine can simply be told which fonts exist. Every surface then agrees
for free -- canvas, DOM, ``document.fonts``, workers, and what is actually
painted -- because none of them is lying. This is the same move that fixed the
timezone in F23: anchor at the engine where an anchor exists, instead of
patching the methods you happened to think of.

The ceiling, stated plainly
---------------------------
Rejecting a font is exact and safe: the engine genuinely does not have it and
falls back the way any machine without it would. *Claiming* a font the host
does not have is not free, because a claimed name has to be backed by a real
file. A Windows claim is therefore credible exactly as far as
metric-compatible clones are installed:

* ``Liberation Sans/Serif/Mono`` are metric-compatible with Arial, Times New
  Roman and Courier New -- the core three, and the ones most probes test.
* ``Carlito`` and ``Caladea`` are metric-compatible with Calibri and Cambria.
  Neither is installed on this host, so those two names are left absent
  rather than faked with the wrong metrics.

Nothing is invented. A name is claimed only when a real file can back it, and
where the backing is missing the name stays absent -- a Windows install
without Calibri is unremarkable, whereas a Calibri with Liberation's metrics
is a contradiction. Every unbackable claim is recorded as an identity fault so
the gap is visible rather than silently assumed covered.

The residual, also stated plainly
---------------------------------
The backer's own name stays enumerable. fontconfig renaming governs
*matching*, but Chrome reads a face's names from the font file's internal
``name`` table, so the file keeps announcing itself. Asked directly, with
fontconfig reporting the renamed identity::

    fc-list        Courier New | Courier New | CourierNewPSMT
    Chrome         Liberation Mono | LiberationMono

and a page measuring ``"Liberation Mono", sans-serif`` gets the monospace
metrics (864.14) rather than the sans fallback (1056.69), which reads as
installed. So a Windows profile on this host no longer enumerates the
ghostscript set or DejaVu, but the three Liberation names remain visible.

That is a strict improvement -- absence of Calibri is weak evidence, whereas
D050000L was proof -- but it is not closure. Closing it means the claimed
names must be genuine in the file, which is a packaging decision rather than a
code one, and it has a licensing dimension: aliasing Arial to Liberation Sans
is what every Linux distribution already does in
``30-metric-aliases.conf``, whereas rewriting a font binary's ``name`` table
to assert it *is* Arial puts a third party's trademark into a file this
product ships. Both options are recorded in the audit report for a decision;
neither is taken here. Each launch records the residual as an identity fault
so it cannot be mistaken for covered.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .identity_faults import record as record_fault

# ---------------------------------------------------------------------------
# What backs what
# ---------------------------------------------------------------------------

#: Claimed family -> families that may stand in for it, best first. A stand-in
#: is used only when it is metric-compatible: the point of claiming "Arial" is
#: that a probe measuring Arial gets Arial's widths.
_METRIC_BACKERS: dict[str, tuple[str, ...]] = {
    "Arial": ("Liberation Sans", "Arimo", "Albany"),
    "Helvetica": ("Liberation Sans", "Arimo"),
    "Times New Roman": ("Liberation Serif", "Tinos", "Thorndale"),
    "Courier New": ("Liberation Mono", "Cousine", "Cumberland"),
    "Calibri": ("Carlito",),
    "Cambria": ("Caladea",),
    "Georgia": ("Gelasio",),
}

#: Families that identify the host as Linux and must not survive a Windows or
#: macOS claim: the ghostscript/URW base-35 set plus the distribution faces.
#:
#: Case matters. fontconfig's ``<test compare="eq">`` is a case-sensitive
#: string comparison, so a rule emitted for "liberation mono" never fires
#: against Chrome's request for "Liberation Mono".
_LINUX_TELLS: tuple[str, ...] = (
    "C059", "P052", "Z003", "D050000L", "Standard Symbols PS",
    "Nimbus Sans", "Nimbus Roman", "Nimbus Mono PS", "URW Bookman",
    "URW Gothic", "URW Palladio L", "URW Chancery L", "Dingbats",
    "Century Schoolbook L", "Bitstream Charter",
    "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
    "Liberation Sans", "Liberation Serif", "Liberation Mono",
    "Liberation Sans Narrow",
    "Ubuntu", "Ubuntu Mono", "Ubuntu Condensed", "Cantarell", "Adwaita Sans",
    "Adwaita Mono", "Oxygen", "Oxygen Mono", "FreeSans", "FreeSerif",
    "FreeMono", "Droid Sans Fallback", "OpenSymbol", "Symbola",
)

_LINUX_TELLS_LOWER: frozenset[str] = frozenset(n.lower() for n in _LINUX_TELLS)

#: Kept under any claim so pages still render CJK and emoji, which every
#: desktop has and which carry no Linux signal.
_UNIVERSAL_KEEP: frozenset[str] = frozenset({
    "noto color emoji", "noto emoji",
    "noto sans cjk jp", "noto sans cjk sc", "noto sans cjk tc",
    "noto sans cjk kr", "noto sans cjk hk",
    "noto serif cjk jp", "noto serif cjk sc", "noto serif cjk tc",
    "noto serif cjk kr",
})

#: Claimed names per platform, in the order a probe is likeliest to test them.
#: Only names a real file can back are actually claimed; see
#: :func:`plan_for_platform`.
_PLATFORM_FAMILIES: dict[str, tuple[str, ...]] = {
    "windows": (
        "Arial", "Times New Roman", "Courier New", "Calibri", "Cambria",
        "Georgia", "Verdana", "Tahoma", "Trebuchet MS", "Segoe UI",
        "Consolas", "Impact", "Comic Sans MS", "Lucida Console",
        "Microsoft Sans Serif", "Palatino Linotype", "Franklin Gothic Medium",
    ),
    "macos": (
        "Helvetica", "Helvetica Neue", "Times New Roman", "Courier New",
        "Georgia", "Verdana", "Arial", "Menlo", "Monaco", "Geneva",
        "Optima", "Avenir", "American Typewriter", "Papyrus",
    ),
}

#: Optional families dropped per profile so two profiles on one host do not
#: enumerate an identical list. Real machines differ by which applications and
#: language packs were installed; these are the plausible ones to vary.
_OPTIONAL: dict[str, tuple[str, ...]] = {
    "windows": ("Comic Sans MS", "Impact", "Franklin Gothic Medium",
                "Palatino Linotype", "Lucida Console"),
    "macos": ("Papyrus", "American Typewriter", "Optima", "Avenir"),
    "linux": ("URW Bookman", "Z003", "P052", "C059", "D050000L"),
}

#: Real PostScript names, because renaming ``family`` is not enough. Chrome
#: matches a CSS ``font-family`` against the full font name and the PostScript
#: name as well, and a scan rule editing ``family`` alone leaves both
#: untouched. Measured with the family rename already in place, the faces
#: still carried their origin::
#:
#:     family          | fullname         | postscriptname
#:     Arial           | Liberation Sans  | LiberationSans
#:     Courier New     | Liberation Mono  | LiberationMono
#:     Times New Roman | Liberation Serif | LiberationSerif
#:
#: and a page asking for "Liberation Mono" was served the renamed face through
#: exactly that gap. All three leaked; the probe caught only Mono, because the
#: other two resolve to the same width as the generic they were tested against
#: and so read as absent by accident rather than by design.
_POSTSCRIPT_NAMES: dict[str, dict[str, str]] = {
    "Arial": {
        "Regular": "ArialMT", "Bold": "Arial-BoldMT",
        "Italic": "Arial-ItalicMT", "Bold Italic": "Arial-BoldItalicMT",
    },
    "Times New Roman": {
        "Regular": "TimesNewRomanPSMT", "Bold": "TimesNewRomanPS-BoldMT",
        "Italic": "TimesNewRomanPS-ItalicMT",
        "Bold Italic": "TimesNewRomanPS-BoldItalicMT",
    },
    "Courier New": {
        "Regular": "CourierNewPSMT", "Bold": "CourierNewPS-BoldMT",
        "Italic": "CourierNewPS-ItalicMT",
        "Bold Italic": "CourierNewPS-BoldItalicMT",
    },
    "Helvetica": {
        "Regular": "Helvetica", "Bold": "Helvetica-Bold",
        "Italic": "Helvetica-Oblique", "Bold Italic": "Helvetica-BoldOblique",
    },
}


def _names_for(claimed: str, style: str) -> tuple[str, str]:
    """The full name and PostScript name a real *claimed* face would carry."""
    style = style or "Regular"
    table = _POSTSCRIPT_NAMES.get(claimed)
    if table and style in table:
        ps = table[style]
    else:
        compact = claimed.replace(" ", "")
        ps = compact if style == "Regular" else f"{compact}-{style.replace(' ', '')}"
    full = claimed if style == "Regular" else f"{claimed} {style}"
    return full, ps


# ---------------------------------------------------------------------------
# Reading the host
# ---------------------------------------------------------------------------

def _platform_bucket(fingerprint: dict) -> str:
    """Which platform this profile claims, as a policy bucket."""
    raw = " ".join(str(fingerprint.get(k) or "") for k in
                   ("platform", "ch_platform", "os")).lower()
    if "win" in raw:
        return "windows"
    if "mac" in raw:
        return "macos"
    if "android" in raw:
        return "android"
    if "iphone" in raw or "ipad" in raw or "ios" in raw:
        return "ios"
    return "linux"


def available_families() -> dict[str, list[str]]:
    """Map lowercased family name -> font files, as fontconfig sees them.

    Returns an empty mapping when ``fc-list`` is unavailable, which the caller
    treats as "do not restrict" rather than "hide everything": an empty font
    directory would break rendering outright, which is far worse than the leak
    it would close.
    """
    try:
        out = subprocess.run(
            ["fc-list", "--format", "%{file}\t%{family}\n"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    families: dict[str, list[str]] = {}
    for line in out.splitlines():
        path, _, family_field = line.partition("\t")
        if not path or not family_field:
            continue
        # fc-list reports comma-separated localised aliases for one face.
        for name in family_field.split(","):
            key = name.strip().lower()
            if key:
                families.setdefault(key, []).append(path)
    return families


def face_records() -> list[dict[str, str]]:
    """Every installed face with the names Chrome can match it by."""
    try:
        out = subprocess.run(
            ["fc-list", "--format",
             "%{file}\t%{family[0]}\t%{style[0]}\t%{postscriptname}\n"],
            capture_output=True, text=True, timeout=20, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    faces: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or not parts[0] or not parts[3]:
            continue
        faces.append({"file": parts[0], "family": parts[1],
                      "style": parts[2], "postscriptname": parts[3]})
    return faces


def _seed_of(fingerprint: dict, profile_id: str) -> int:
    raw = str(fingerprint.get("seed") or profile_id or "0")
    return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def plan_for_platform(
    fingerprint: dict,
    profile_id: str,
    families: dict[str, list[str]] | None = None,
) -> dict:
    """Decide which font files to expose and which names to rename them to.

    Returns ``{"bucket", "files", "renames", "faces", "claimed", "unbacked",
    "restrict"}``. ``files`` is the exact set of font files the profile will
    see; ``renames`` maps an available family to the claimed names it will be
    scanned as; ``unbacked`` are names the claimed platform has that nothing
    on this host can honestly provide.
    """
    families = available_families() if families is None else families
    bucket = _platform_bucket(fingerprint)
    seed = _seed_of(fingerprint, profile_id)
    empty = {"bucket": bucket, "files": [], "renames": {}, "faces": [],
             "claimed": [], "unbacked": [], "restrict": False}

    if not families:
        return empty

    # A profile claiming the host's own platform has nothing to hide. Vary the
    # optional tail only, so profiles do not share one font list.
    if bucket not in _PLATFORM_FAMILIES:
        pool = _OPTIONAL.get("linux", ())
        drop = {pool[seed % len(pool)].lower()} if pool else set()
        keep = [p for name, paths in families.items()
                if name not in drop for p in paths]
        return {"bucket": bucket, "files": sorted(set(keep)), "renames": {},
                "faces": [], "claimed": [], "unbacked": [], "restrict": True}

    optional = _OPTIONAL.get(bucket, ())
    dropped = set()
    if optional:
        dropped.add(optional[seed % len(optional)])
        dropped.add(optional[(seed // 7 + 2) % len(optional)])

    renames: dict[str, list[str]] = {}
    claimed: list[str] = []
    unbacked: list[str] = []
    keep_files: list[str] = []

    for name in _PLATFORM_FAMILIES[bucket]:
        if name in dropped:
            continue
        key = name.lower()
        if key in families and key not in _LINUX_TELLS_LOWER:
            # Genuinely installed and carries no Linux signal: expose as-is.
            claimed.append(name)
            keep_files.extend(families[key])
            continue
        backer = next(
            (b for b in _METRIC_BACKERS.get(name, ()) if b.lower() in families),
            None,
        )
        if backer:
            renames.setdefault(backer, []).append(name)
            claimed.append(name)
            keep_files.extend(families[backer.lower()])
        else:
            # No metric-compatible file. Claiming it anyway would hand the
            # probe the wrong widths, which is a worse tell than absence.
            unbacked.append(name)

    for key in _UNIVERSAL_KEEP:
        if key in families:
            keep_files.extend(families[key])

    exposed = set(keep_files)
    faces = [f for f in face_records()
             if f["file"] in exposed and f["family"] in renames]

    return {
        "bucket": bucket,
        "files": sorted(exposed),
        "renames": renames,
        "faces": faces,
        "claimed": claimed,
        "unbacked": unbacked,
        "restrict": True,
    }


# ---------------------------------------------------------------------------
# The config
# ---------------------------------------------------------------------------

def _xml_escape(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_config(plan: dict, font_dir: Path, cache_dir: Path) -> str:
    """The fontconfig XML for a plan.

    Substitution happens at ``target="scan"``, which rewrites a face's names as
    fontconfig reads the file, so the engine never learns the backer's real
    one. That matters: exposing Liberation Sans to back Arial and merely
    *aliasing* the two would leave "Liberation Sans" enumerable, reintroducing
    the Linux tell through the very file used to hide it. With scan rules
    instead, the only families fontconfig reports are the claimed ones::

        $ FONTCONFIG_FILE=... fc-list --format '%{family}\\n' | sort -u
        Arial
        Courier New
        Times New Roman
    """
    lines = [
        '<?xml version="1.0"?>',
        '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">',
        "<fontconfig>",
        f"  <dir>{_xml_escape(str(font_dir))}</dir>",
        f"  <cachedir>{_xml_escape(str(cache_dir))}</cachedir>",
        "  <selectfont><rejectfont>",
        "    <glob>/usr/share/fonts/*</glob>",
        "    <glob>/usr/local/share/fonts/*</glob>",
        "    <glob>/usr/share/texmf*/*</glob>",
        "    <glob>~/.fonts/*</glob>",
        "    <glob>~/.local/share/fonts/*</glob>",
        "  </rejectfont></selectfont>",
    ]

    # One rule per face, keyed on its PostScript name, which is unique. All
    # three name channels Chrome can match on are rewritten together.
    for face in sorted(plan.get("faces", []), key=lambda f: f["postscriptname"]):
        names = plan.get("renames", {}).get(face["family"], [])
        if not names:
            continue
        full, new_ps = _names_for(names[0], face["style"])
        lines += [
            '  <match target="scan">',
            '    <test name="postscriptname" compare="eq">'
            f"<string>{_xml_escape(face['postscriptname'])}</string></test>",
            '    <edit name="family" mode="assign">'
            f"<string>{_xml_escape(names[0])}</string></edit>",
            '    <edit name="fullname" mode="assign">'
            f"<string>{_xml_escape(full)}</string></edit>",
            '    <edit name="postscriptname" mode="assign">'
            f"<string>{_xml_escape(new_ps)}</string></edit>",
        ]
        # A face can carry more than one claimed name (Arial and Helvetica are
        # the same metrics), so the rest are appended to the family list.
        lines += [
            '    <edit name="family" mode="append">'
            f"<string>{_xml_escape(extra)}</string></edit>"
            for extra in names[1:]
        ]
        lines.append("  </match>")

    # Requests for a host-identifying family are rewritten to a name nothing
    # provides, so they fall back exactly as an absent font does.
    #
    # These come BEFORE the system include, and that order is the point.
    # ``/etc/fonts/conf.d/30-metric-aliases.conf`` declares Liberation Mono and
    # Courier New mutually substitutable, so with the include first a probe
    # asking for "Liberation Mono" was handed the renamed face and read it as
    # installed. Blocking on the request side first closes that, and the test
    # has to see the bare requested family, which is why the include cannot
    # come earlier: by then the generic lists have been expanded.
    for name in sorted(set(_LINUX_TELLS) | set(plan.get("renames", {}))):
        lines += [
            '  <match target="pattern">',
            '    <test name="family" qual="first" compare="eq">'
            f"<string>{_xml_escape(name)}</string></test>",
            '    <edit name="family" mode="assign">'
            "<string>__mp_no_such_family__</string></edit>",
            "  </match>",
        ]

    # The system rules are needed for generic-family resolution and rendering
    # defaults; without them, measured here, ``sans-serif`` resolved to
    # Courier New and every page would have rendered in monospace. The blanket
    # rejection above keeps the include from reintroducing host font
    # directories.
    lines.append('  <include ignore_missing="yes">/etc/fonts/conf.d</include>')

    for generic, candidates in (
        ("sans-serif", ("Arial", "Helvetica", "Segoe UI")),
        ("serif", ("Times New Roman", "Georgia")),
        ("monospace", ("Courier New", "Consolas", "Menlo")),
    ):
        usable = [c for c in candidates if c in plan.get("claimed", ())]
        if not usable:
            continue
        lines.append(f"  <alias><family>{generic}</family><prefer>")
        lines += [f"    <family>{_xml_escape(c)}</family>" for c in usable]
        lines.append("  </prefer></alias>")

    lines.append("</fontconfig>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------

def prepare_for_profile(
    profile_id: str,
    fingerprint: dict,
    root: Path,
    notes: list[str] | None = None,
) -> Path | None:
    """Materialise this profile's font view; return the ``FONTCONFIG_FILE``.

    Returns ``None`` when the host cannot be inspected, in which case the
    caller leaves the environment alone and the profile sees the host's fonts
    -- the pre-existing behaviour, and better than a browser with no fonts.
    """
    plan = plan_for_platform(fingerprint, profile_id)
    if not plan.get("restrict") or not plan.get("files"):
        if notes is not None:
            notes.append("fonts: host font set left as-is (fc-list unavailable)")
        record_fault(
            "font_policy_unavailable",
            "could not enumerate host fonts; the profile sees the host set",
            where="browser.fontconfig", profile_id=profile_id,
        )
        return None

    font_dir = root / "fonts"
    cache_dir = root / "fontcache"
    font_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Rebuilt from scratch: a stale link from a previously claimed platform
    # would reintroduce exactly the leak this closes.
    for stale in font_dir.iterdir():
        if stale.is_symlink() or stale.is_file():
            stale.unlink()

    linked = 0
    for src in plan["files"]:
        source = Path(src)
        target = font_dir / f"{linked:04d}-{source.name}"
        try:
            target.symlink_to(source)
            linked += 1
        except OSError:
            continue

    if not linked:
        record_fault(
            "font_link_failed",
            "no font files could be linked; leaving the host set in place",
            where="browser.fontconfig", profile_id=profile_id,
        )
        return None

    conf = root / "fonts.conf"
    conf.write_text(render_config(plan, font_dir, cache_dir), encoding="utf-8")

    if notes is not None:
        notes.append(
            f"fonts: {linked} files exposed for a {plan['bucket']} claim, "
            f"{len(plan['claimed'])} families claimed"
        )
    leaking = sorted(plan.get("renames", {}))
    if leaking and plan["bucket"] in _PLATFORM_FAMILIES:
        # Chrome reads names from the file, not from fontconfig, so a renamed
        # backer is still enumerable under its own name. Recorded every launch
        # rather than assumed covered.
        record_fault(
            "font_backer_name_visible",
            "metric backers remain enumerable under their own names because "
            f"Chrome reads the font file's name table: {', '.join(leaking)}",
            where="browser.fontconfig", profile_id=profile_id,
        )

    if plan["unbacked"]:
        # Surfaced, not swallowed: these are names the claimed platform has
        # and this host cannot honestly provide.
        record_fault(
            "font_claim_unbacked",
            f"{plan['bucket']} fonts with no metric-compatible file on this "
            f"host, left absent: {', '.join(plan['unbacked'])}",
            where="browser.fontconfig", profile_id=profile_id,
        )
        if notes is not None:
            notes.append(
                f"fonts: {len(plan['unbacked'])} {plan['bucket']} families "
                f"unbacked on this host ({', '.join(plan['unbacked'][:4])})"
            )
    return conf
