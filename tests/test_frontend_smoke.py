"""Frontend smoke test for the v1.7.0 Multiprofile Linux console restyle.

Serves no backend: it reads src/static directly and asserts structural
requirements:

- index.html contains the sidebar nav, the profiles table mount, the
  wizard mount, and the device library mount.
- app.js contains no placeholder/demo content (no lorem ipsum, no
  hard-coded demo profile names), wires the real API endpoints
  (profiles/devices/jobs/audit/metrics/consistency), implements the
  WebSocket -> SSE -> poll fallback chain, and masks proxy passwords.
- app.css defines our own brand tokens (--mp-*) and does not reference
  AdsPower (inspiration is UX grammar only; brand stays original).

This is a smoke test, not a full UI test: it checks for the presence of
the required structure and the absence of placeholders.
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "static"

INDEX = STATIC / "index.html"
APP_JS = STATIC / "app.js"
APP_CSS = STATIC / "app.css"

# HTML structural requirements: the static shell. View mounts below are
# rendered dynamically by app.js into #view (SPA), so they are asserted
# against app.js instead.
HTML_REQUIREMENTS = [
    ("sidebar nav element", 'id="mainNav"'),
    ("sidebar brand", 'class="brand'),
    ("topbar", 'class="topbar"'),
    ("view container", 'id="view"'),
    ("drawer", 'id="drawer"'),
    ("modal", 'id="modal"'),
    ("toasts", 'id="toasts"'),
    ("login gate (401 view)", 'id="loginGate"'),
]

# JS-rendered view mounts (SPA views render into #view at runtime)
JS_MOUNT_REQUIREMENTS = [
    ("profiles table mount", "pvTableWrap"),
    ("profiles pagination mount", "pvPager"),
    ("bulk action bar mount", "bulkBtns"),
    ("wizard mount", "wizBody"),
    ("device library mount", "device-grid"),
    ("device picker mount (wizard)", "device-pick"),
    ("fingerprint inspector mount", "fpBody"),
    ("jobs table mount", "jbList"),
    ("jobs pagination mount", "jbPager"),
    ("network tabs mount", "netBody"),
    ("audit table mount", "auQ"),
]

# JS structural requirements: (description, needle)
JS_REQUIREMENTS = [
    ("profiles endpoint", "/api/profiles"),
    ("devices endpoint", "/api/devices"),
    ("jobs endpoint", "/api/jobs"),
    ("audit endpoint", "/api/audit"),
    ("metrics endpoint (may 404)", "/metrics"),
    ("consistency endpoint", "/consistency"),
    ("network test endpoint", "/network/test"),
    ("WebSocket live updates", "/ws/events"),
    ("SSE fallback", "/events/stream"),
    ("poll fallback", "15000"),
    ("profile.status live event", "profile.status"),
    ("job.progress live event", "job.progress"),
    ("proxy password masking", "••••"),
    ("device support badges", "supportBadge"),
    ("cursor pagination param", "cursor"),
    ("401 -> login gate", "authFailed"),
    ("inline SVG nav icons", "viewBox"),
    ("delete confirmation", "confirm("),
]

# Placeholder/demo content that must never ship.
PLACEHOLDER_PATTERNS = [
    "lorem",
    "demo-profile",
    "demo_profile",
    "sample profile",
    "acme corp",
    "john doe",
    "jane doe",
    "example user",
    "placeholder-profile",
    "test-user-01",
]


def read(path: Path) -> str:
    assert path.exists(), f"missing static file: {path}"
    return path.read_text(encoding="utf-8")


def test_index_html_structure() -> None:
    html = read(INDEX)
    missing = [d for d, n in HTML_REQUIREMENTS if n not in html]
    assert not missing, f"index.html missing: {missing}"
    # The 8 nav sections are rendered from JS; the container must exist.
    assert "mainNav" in html


def test_app_js_structure() -> None:
    js = read(APP_JS)
    missing = [d for d, n in JS_REQUIREMENTS if n not in js]
    assert not missing, f"app.js missing: {missing}"


def test_app_js_view_mounts() -> None:
    js = read(APP_JS)
    missing = [d for d, n in JS_MOUNT_REQUIREMENTS if n not in js]
    assert not missing, f"app.js missing view mounts: {missing}"


def test_app_js_no_placeholders() -> None:
    js = read(APP_JS).lower()
    found = [p for p in PLACEHOLDER_PATTERNS if p in js]
    assert not found, f"app.js contains placeholder content: {found}"


def test_app_js_no_hardcoded_demo_profiles() -> None:
    js = read(APP_JS)
    # No literal profile fixture objects baked into the UI.
    fixtures = re.findall(r"\{\s*id:\s*[\"']profile_(demo|test|sample|example)", js, re.IGNORECASE)
    assert not fixtures, f"app.js contains hard-coded demo profiles: {fixtures}"


def test_css_own_brand_tokens() -> None:
    css = read(APP_CSS)
    assert ":root" in css, "app.css has no :root token block"
    tokens = re.findall(r"--mp-[a-z0-9-]+", css)
    assert len(set(tokens)) >= 10, f"expected >=10 --mp-* brand tokens, found {len(set(tokens))}"
    assert "adspower" not in css.lower(), "app.css must not reference AdsPower"


def test_js_no_adspower_brand() -> None:
    js = read(APP_JS)
    assert "adspower" not in js.lower(), "app.js must not reference AdsPower"


def test_nav_sections_in_js() -> None:
    js = read(APP_JS)
    for section in ["Dashboard", "Profiles", "Devices", "Fingerprint", "Network", "Jobs", "Audit Log", "Settings"]:
        assert section in js, f"nav section missing from app.js: {section}"


def test_ten_detail_tabs() -> None:
    js = read(APP_JS)
    for tab in ["overview", "network", "fingerprint", "device", "cookies",
                "storage", "runtime", "health", "automation", "audit"]:
        assert f'"{tab}"' in js, f"detail tab missing from app.js: {tab}"


def test_six_wizard_steps() -> None:
    js = read(APP_JS)
    for step in ["Basic", "Device", "Network", "Fingerprint", "Consistency", "Review"]:
        assert f'"{step}"' in js, f"wizard step missing from app.js: {step}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS {t.__name__}")
    print(f"\n{passed}/{len(tests)} smoke tests passed")
    print(f"HTML requirements checked: {len(HTML_REQUIREMENTS)}")
    print(f"JS view mounts checked: {len(JS_MOUNT_REQUIREMENTS)}")
    print(f"JS requirements checked: {len(JS_REQUIREMENTS)}")
    print(f"Placeholder patterns checked: {len(PLACEHOLDER_PATTERNS)}")
