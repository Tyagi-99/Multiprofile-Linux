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
# the extension as actually prepared for a profile
# ---------------------------------------------------------------------------


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_EXTENSION", str(EXTENSION))
    return tmp_path


def test_a_prepared_profile_extension_exposes_nothing(xdg):
    """End to end: build a real profile extension with an authenticated proxy
    and assert the credential file exists but is unreachable from a page."""
    from multiprofile.browser.launcher import _prepare_extension
    from multiprofile.fingerprint import build_fingerprint

    profile = {
        "id": "prof-cred",
        "name": "Credential test",
        "seed": 4242,
        "proxyType": "socks5",
        "proxyHost": "198.51.100.5",
        "proxyPort": 1080,
        "proxyUser": "operator",
        "proxyPass": "super-secret-value",
    }
    dest = _prepare_extension(profile, build_fingerprint(profile))

    written = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert _accessible_resources(written) == []

    # The credential file is still there for the service worker to read...
    proxy_json = dest / "proxy.json"
    assert proxy_json.exists()
    assert json.loads(proxy_json.read_text())["password"] == "super-secret-value"
    # ...it is simply no longer reachable from any page.
    assert "proxy.json" not in _accessible_resources(written)
    assert not (dest / "loader.js").exists()
