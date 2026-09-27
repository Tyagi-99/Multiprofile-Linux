"""The per-profile extension must never publish credentials to visited sites.

This is the regression guard for the worst defect this codebase has shipped.

`manifest.json` used to declare `proxy.json` and `cookies.json` in
`web_accessible_resources` with `matches: ["<all_urls>"]` and no
`use_dynamic_url`. `proxy.json` holds the proxy username and password in
cleartext (launcher._prepare_extension) and `cookies.json` holds the
profile's entire cookie jar. Any page the profile visited that learned the
extension id could `fetch()` both and walk away with the operator's proxy
credentials.

The launcher already works hard to keep that password safe -- it never goes
on the Chrome command line, and ProxyConfig.masked_str() keeps it out of the
logs. One manifest key undid all of it.

The service worker reads these files through chrome.runtime.getURL() from its
own extension context, which never required web_accessible_resources. That
key existed only for loader.js, which injected evasion.js a second time via a
<script> tag -- redundant, because the manifest already declares evasion.js
as a `world: "MAIN"` content script. Both are gone.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

EXTENSION = Path(__file__).resolve().parent.parent / "src" / "extension"

# Files the extension holds that must never be readable by a visited page.
CREDENTIAL_FILES = ("proxy.json", "cookies.json")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))


def _accessible_resources(manifest: dict) -> list[str]:
    return [
        resource
        for entry in manifest.get("web_accessible_resources") or []
        for resource in entry.get("resources") or []
    ]


# ---------------------------------------------------------------------------
# the manifest as shipped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", CREDENTIAL_FILES)
def test_credential_files_are_not_web_accessible(manifest, filename):
    assert filename not in _accessible_resources(manifest), (
        f"{filename} is web-accessible again. Any site the profile visits can "
        f"fetch it and read the proxy credentials / cookie jar."
    )


def test_no_web_accessible_resources_at_all(manifest):
    """Nothing currently needs to be reachable from a page. Keep it that way.

    If a future change genuinely needs one, add it here deliberately and give
    it `use_dynamic_url: true` so the URL is not guessable.
    """
    assert _accessible_resources(manifest) == []


def test_any_future_web_accessible_resource_uses_a_dynamic_url(manifest):
    for entry in manifest.get("web_accessible_resources") or []:
        assert entry.get("use_dynamic_url") is True, (
            "a web-accessible resource without use_dynamic_url sits at a "
            "predictable chrome-extension:// URL"
        )


def test_evasion_is_injected_by_the_manifest_not_a_script_tag(manifest):
    """world:MAIN does the injection, so evasion.js need not be exposed."""
    main_world = [
        cs
        for cs in manifest.get("content_scripts") or []
        if cs.get("world") == "MAIN" and "evasion.js" in (cs.get("js") or [])
    ]
    assert main_world, "evasion.js must be injected as a MAIN-world content script"
    assert "evasion.js" not in _accessible_resources(manifest)


def test_loader_js_is_gone(manifest):
    """It double-injected evasion.js and was the only reason for the exposure."""
    assert not (EXTENSION / "loader.js").exists()
    declared = [js for cs in manifest.get("content_scripts") or [] for js in cs.get("js") or []]
    assert "loader.js" not in declared


def test_evasion_is_injected_exactly_once(manifest):
    """Two injections double-hooked canvas and friends."""
    declared = [js for cs in manifest.get("content_scripts") or [] for js in cs.get("js") or []]
    assert declared.count("evasion.js") == 1


def test_manifest_does_not_claim_audio_protection(manifest):
    """fingerprint/audio.py states no per-profile audio override is possible on
    Linux Chromium, and evasion.js has no audio hooks. The description must not
    promise it."""
    text = f"{manifest.get('name', '')} {manifest.get('description', '')}".lower()
    assert "audio" not in text or "not spoofed" in text


# ---------------------------------------------------------------------------
# the credential files are not written at all any more
#
# The extension is no longer loaded (Google Chrome refuses --load-extension;
# Chromium honoured it and ran evasion.js twice), so the launcher no longer
# stages a per-profile copy — and with it go the two files this module was
# written to contain. `proxy.json` held the proxy password in CLEARTEXT,
# decrypted out of the OS keychain, and `cookies.json` held the whole cookie
# jar; both were written with a bare write_text, so the umask decided their
# mode and both landed 0644 world-readable while every other writer in the
# codebase goes through secure_write_json (0600).
#
# The strongest possible guard for a file that must not leak is that it is
# never written. These tests hold that line.
# ---------------------------------------------------------------------------


def test_the_launcher_no_longer_stages_a_per_profile_extension():
    from multiprofile.browser import launcher

    assert not hasattr(launcher, "_prepare_extension"), (
        "the extension staging step is back; it wrote the proxy password and "
        "the cookie jar to disk world-readable, and made evasion.js run twice"
    )


@pytest.mark.parametrize("filename", CREDENTIAL_FILES)
def test_the_launcher_writes_no_credential_files(filename):
    """Not a permissions question any more: nothing writes these."""
    source = (
        Path(__file__).resolve().parent.parent
        / "src" / "multiprofile" / "browser" / "launcher.py"
    ).read_text(encoding="utf-8")
    assert f'"{filename}"' not in source, (
        f"launcher.py writes {filename} again — route it through "
        f"secure_write_json (0600) or, better, do not write it"
    )


def test_the_launcher_never_writes_a_file_with_the_default_umask():
    """Every writer in this codebase uses secure_write_json for a reason."""
    source = (
        Path(__file__).resolve().parent.parent
        / "src" / "multiprofile" / "browser" / "launcher.py"
    ).read_text(encoding="utf-8")
    # ensure_no_client_frame_shadow writes Chrome's own Preferences file, which
    # Chrome itself owns and rewrites; it holds no profile secrets.
    allowed = source.count("tmp.write_text(")
    assert source.count(".write_text(") == allowed, (
        "a new bare write_text in the launcher: the umask decides the mode, "
        "which is how the cookie jar and the cleartext proxy password ended "
        "up 0644"
    )


def test_the_extension_is_not_loaded_into_the_browser():
    """Both flags are gone: Chrome ignored them, Chromium double-injected."""
    source = (
        Path(__file__).resolve().parent.parent
        / "src" / "multiprofile" / "browser" / "launcher.py"
    ).read_text(encoding="utf-8")
    for flag in ("--load-extension", "--disable-extensions-except"):
        assert f'f"{flag}' not in source and f'"{flag}=' not in source, (
            f"{flag} is passed to the browser again"
        )
