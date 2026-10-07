"""The import gate: what a hostile or malformed package must not be able to do.

Every case here is a real ZIP failure mode, not a hypothetical. The product's
extension importer is the only place untrusted bytes enter the feature, so it
is tested as a gate: each test names the thing that would happen if the check
were missing.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from multiprofile.extensions.package import (
    MAX_MEMBERS,
    PackageError,
    crx_rejection,
    extract_zip,
    find_extension_root,
    inspect_folder,
    inspect_zip,
    parse_manifest,
    referenced_files,
)

GOOD_MANIFEST = {
    "manifest_version": 3,
    "name": "Fixture",
    "version": "1.2.3",
    "description": "d",
    "permissions": ["storage", "tabs"],
    "host_permissions": ["http://127.0.0.1/*"],
}


def _zip(tmp_path: Path, entries: dict[str, bytes | str], name: str = "e.zip") -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        for key, value in entries.items():
            zf.writestr(key, value if isinstance(value, bytes) else str(value))
    return p


def _manifest_bytes(**overrides) -> str:
    data = dict(GOOD_MANIFEST)
    data.update(overrides)
    return json.dumps(data)


# ---------------------------------------------------------------------------
# the happy paths, so the rejections below are not passing vacuously
# ---------------------------------------------------------------------------

def test_a_top_level_manifest_is_accepted(tmp_path):
    z = _zip(tmp_path, {"manifest.json": _manifest_bytes()})
    report = inspect_zip(z)
    assert report.root == ""
    assert report.manifest.name == "Fixture"
    assert report.manifest.version == "1.2.3"
    assert report.manifest.manifest_version == 3
    assert "storage" in report.manifest.permissions
    assert "http://127.0.0.1/*" in report.manifest.host_permissions


def test_a_single_wrapping_directory_is_accepted(tmp_path):
    """What every "Download ZIP" button produces."""
    z = _zip(tmp_path, {"my-ext-main/manifest.json": _manifest_bytes()})
    assert inspect_zip(z).root == "my-ext-main"


def test_extraction_writes_the_root_not_the_wrapper(tmp_path):
    z = _zip(tmp_path, {
        "wrap/manifest.json": _manifest_bytes(),
        "wrap/sub/a.js": "1",
    })
    dest = tmp_path / "out"
    extract_zip(z, dest)
    assert (dest / "manifest.json").is_file()
    assert (dest / "sub" / "a.js").is_file()
    assert not (dest / "wrap").exists()


# ---------------------------------------------------------------------------
# archive shape
# ---------------------------------------------------------------------------

def test_two_extension_roots_are_refused_rather_than_guessed(tmp_path):
    """Picking the first would silently install something not chosen."""
    z = _zip(tmp_path, {
        "one/manifest.json": _manifest_bytes(name="One"),
        "two/manifest.json": _manifest_bytes(name="Two"),
    })
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "ambiguous_root"
    assert "one" in str(e.value) and "two" in str(e.value)


def test_no_manifest_is_refused(tmp_path):
    z = _zip(tmp_path, {"readme.txt": "hello"})
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "manifest_absent"


def test_a_deeply_nested_manifest_is_not_treated_as_the_root(tmp_path):
    """A vendored dependency's manifest is not this extension."""
    z = _zip(tmp_path, {"a/b/c/manifest.json": _manifest_bytes()})
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "manifest_absent"


def test_a_non_zip_is_refused(tmp_path):
    p = tmp_path / "not.zip"
    p.write_bytes(b"this is not a zip")
    with pytest.raises(PackageError) as e:
        inspect_zip(p)
    assert e.value.code == "bad_zip"


@pytest.mark.parametrize("names,expected", [
    (["manifest.json"], ""),
    (["wrap/manifest.json"], "wrap"),
    (["wrap/manifest.json", "wrap/a.js"], "wrap"),
])
def test_root_detection(names, expected):
    assert find_extension_root(names) == expected


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------

def test_path_traversal_is_refused(tmp_path):
    """Without this check the entry writes outside the destination."""
    z = _zip(tmp_path, {
        "manifest.json": _manifest_bytes(),
        "../escaped.txt": "pwned",
    })
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "path_traversal"


def test_an_absolute_member_path_is_refused(tmp_path):
    p = tmp_path / "abs.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", _manifest_bytes())
        info = zipfile.ZipInfo("/etc/passwd")
        zf.writestr(info, "root:x:0:0")
    with pytest.raises(PackageError) as e:
        inspect_zip(p)
    assert e.value.code == "absolute_path"


def test_a_symlink_member_is_refused(tmp_path):
    """Extracting a symlink can redirect every later write out of the tree."""
    p = tmp_path / "link.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", _manifest_bytes())
        info = zipfile.ZipInfo("evil")
        info.external_attr = (0o120777 << 16)      # S_IFLNK
        zf.writestr(info, "/etc")
    with pytest.raises(PackageError) as e:
        inspect_zip(p)
    assert e.value.code == "symlink"


def test_extraction_never_writes_outside_the_destination(tmp_path):
    """Belt and braces: even if inspection were bypassed, the write path
    resolves the target and refuses anything that lands outside."""
    z = _zip(tmp_path, {"manifest.json": _manifest_bytes(), "../out.txt": "x"})
    dest = tmp_path / "dest"
    with pytest.raises(PackageError):
        extract_zip(z, dest)
    assert not (tmp_path / "out.txt").exists()


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------

def test_a_decompression_bomb_is_refused(tmp_path):
    """The compressed size says nothing about the uncompressed size."""
    p = tmp_path / "bomb.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", _manifest_bytes())
        zf.writestr("bomb.bin", b"\0" * (8 * 1024 * 1024))
    with pytest.raises(PackageError) as e:
        inspect_zip(p)
    assert e.value.code == "compression_ratio", e.value.code


def test_too_many_members_is_refused(tmp_path):
    p = tmp_path / "many.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", _manifest_bytes())
        for i in range(MAX_MEMBERS + 1):
            zf.writestr(f"f{i}.txt", "x")
    with pytest.raises(PackageError) as e:
        inspect_zip(p)
    assert e.value.code == "too_many_members"


# ---------------------------------------------------------------------------
# manifest validation
# ---------------------------------------------------------------------------

def test_manifest_v1_is_refused_as_unsupported(tmp_path):
    z = _zip(tmp_path, {"manifest.json": _manifest_bytes(manifest_version=1)})
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "manifest_version_unsupported"
    assert "manifest_version 1" in str(e.value)


def test_a_missing_manifest_version_is_refused(tmp_path):
    data = dict(GOOD_MANIFEST)
    del data["manifest_version"]
    z = _zip(tmp_path, {"manifest.json": json.dumps(data)})
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "manifest_version_missing"


def test_unparseable_json_is_refused(tmp_path):
    z = _zip(tmp_path, {"manifest.json": "{not json"})
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "manifest_json"


def test_a_nameless_manifest_is_refused(tmp_path):
    z = _zip(tmp_path, {"manifest.json": _manifest_bytes(name="   ")})
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "manifest_name"


def test_a_versionless_manifest_is_refused(tmp_path):
    data = dict(GOOD_MANIFEST)
    del data["version"]
    z = _zip(tmp_path, {"manifest.json": json.dumps(data)})
    with pytest.raises(PackageError) as e:
        inspect_zip(z)
    assert e.value.code == "manifest_version"


def test_manifest_v2_is_still_accepted():
    """MV2 is deprecated, not absent. Refusing it would be inventing a limit
    the engine does not have."""
    m = parse_manifest({"manifest_version": 2, "name": "n", "version": "1"})
    assert m.manifest_version == 2


def test_a_manifest_key_is_reported_because_it_pins_the_id():
    m = parse_manifest({"manifest_version": 3, "name": "n", "version": "1",
                        "key": "AAAA"})
    assert m.has_key is True


# ---------------------------------------------------------------------------
# referenced files
# ---------------------------------------------------------------------------

def test_referenced_files_are_collected_from_every_manifest_surface():
    found = referenced_files({
        "action": {"default_popup": "popup.html"},
        "background": {"service_worker": "sw.js"},
        "content_scripts": [{"js": ["c.js"], "css": ["c.css"]}],
        "options_ui": {"page": "opt.html"},
        "icons": {"16": "i16.png"},
        "web_accessible_resources": [{"resources": ["r.js"]}],
    })
    for name in ("popup.html", "sw.js", "c.js", "c.css", "opt.html",
                 "i16.png", "r.js"):
        assert name in found, name


def test_an_archive_missing_a_referenced_file_is_reported(tmp_path):
    """The browser would load this and then fail on it, much later and much
    less clearly."""
    z = _zip(tmp_path, {
        "manifest.json": _manifest_bytes(
            action={"default_popup": "popup.html"},
            background={"service_worker": "sw.js"}),
    })
    report = inspect_zip(z)
    assert sorted(report.missing) == ["popup.html", "sw.js"]


def test_a_remote_url_is_not_treated_as_a_missing_file():
    assert referenced_files({"icons": {"16": "https://example.test/i.png"}}) == []


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------

def test_a_folder_without_a_manifest_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(PackageError) as e:
        inspect_folder(tmp_path / "empty")
    assert e.value.code == "manifest_absent"


def test_a_folder_missing_a_referenced_file_is_refused(tmp_path):
    d = tmp_path / "ext"
    d.mkdir()
    (d / "manifest.json").write_text(
        _manifest_bytes(background={"service_worker": "sw.js"}))
    with pytest.raises(PackageError) as e:
        inspect_folder(d)
    assert e.value.code == "missing_files"


def test_a_complete_folder_is_accepted(tmp_path):
    d = tmp_path / "ext"
    d.mkdir()
    (d / "manifest.json").write_text(
        _manifest_bytes(background={"service_worker": "sw.js"}))
    (d / "sw.js").write_text("//")
    assert inspect_folder(d).name == "Fixture"


# ---------------------------------------------------------------------------
# CRX
# ---------------------------------------------------------------------------

def test_a_crx_is_refused_with_a_supported_alternative(tmp_path):
    p = tmp_path / "thing.crx"
    p.write_bytes(b"Cr24" + b"\0" * 32)
    with pytest.raises(PackageError) as e:
        inspect_zip(p)
    assert e.value.code == "crx_unsupported"
    message = str(e.value)
    # The refusal has to say WHY and what to do instead, or it is just a wall.
    assert "signature" in message
    assert "Web Store" in message


def test_the_crx_rejection_names_the_identity_problem():
    message = str(crx_rejection("x.crx"))
    assert "extension ID" in message
    assert "unpacked" in message
