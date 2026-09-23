"""Frontend smoke test for the v1.9.0 Multiprofile Linux console.

The console is a Next.js 15 static export published into src/static
(`frontend/out` -> `src/static` via `frontend/scripts/sync-static.mjs`).

Serves no backend: it reads src/static directly and asserts structural
requirements:

- index.html is the Next.js shell referencing _next/static assets.
- The JS bundle (all chunks under _next/) wires the real API endpoints
  (profiles/devices/jobs/audit, SSE event stream), contains the nav
  sections, the 10 profile-detail tabs and the 6 wizard steps, and masks
  proxy passwords (never renders them raw).
- The CSS bundle defines our own brand tokens (--color-*) and does not
  reference AdsPower (inspiration is UX grammar only; brand stays original).
- No placeholder/demo content ships (no lorem ipsum, no hard-coded demo
  profile names).

This is a smoke test, not a full UI test: it checks for the presence of
the required structure and the absence of placeholders.
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "static"

INDEX = STATIC / "index.html"
NEXT_DIR = STATIC / "_next"

# HTML structural requirements for the Next.js static-export shell.
HTML_REQUIREMENTS = [
    ("_next asset references", "_next/static"),
    ("Next.js app mount", "__next"),
    ("page title", "<title>"),
]

# JS structural requirements: (description, needle) — searched across every
# chunk under _next/ (app code lives in chunks/app/page-*.js).
JS_REQUIREMENTS = [
    ("profiles endpoint", "/api/profiles"),
    ("devices endpoint", "/api/devices"),
    ("jobs endpoint", "/api/jobs"),
    ("audit endpoint", "/api/audit"),
    ("SSE event stream", "/api/events/stream"),
    ("proxy password masking", "•••"),
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


def js_bundle() -> str:
    assert NEXT_DIR.is_dir(), f"missing Next.js output dir: {NEXT_DIR}"
    chunks = sorted(NEXT_DIR.rglob("*.js"))
    assert chunks, "no JS chunks found under _next/"
    return "\n".join(c.read_text(encoding="utf-8", errors="replace") for c in chunks)


def css_bundle() -> str:
    sheets = sorted(NEXT_DIR.rglob("*.css"))
    assert sheets, "no CSS found under _next/"
    return "\n".join(s.read_text(encoding="utf-8", errors="replace") for s in sheets)


def test_index_html_structure() -> None:
    html = read(INDEX)
    missing = [d for d, n in HTML_REQUIREMENTS if n not in html]
    assert not missing, f"index.html missing: {missing}"


def test_js_bundle_endpoints() -> None:
    js = js_bundle()
    missing = [d for d, n in JS_REQUIREMENTS if n not in js]
    assert not missing, f"JS bundle missing: {missing}"


def test_js_no_placeholders() -> None:
    js = js_bundle().lower()
    found = [p for p in PLACEHOLDER_PATTERNS if p in js]
    assert not found, f"JS bundle contains placeholder content: {found}"


def test_js_no_hardcoded_demo_profiles() -> None:
    js = js_bundle()
    # No literal profile fixture objects baked into the UI.
    fixtures = re.findall(
        r"\{\s*id:\s*[\"']profile_(demo|test|sample|example)", js, re.IGNORECASE
    )
    assert not fixtures, f"JS bundle contains hard-coded demo profiles: {fixtures}"


def test_css_own_brand_tokens() -> None:
    css = css_bundle()
    tokens = set(re.findall(r"--color-[a-z0-9-]+", css))
    assert len(tokens) >= 10, f"expected >=10 --color-* brand tokens, found {len(tokens)}"
    assert "adspower" not in css.lower(), "CSS must not reference AdsPower"


def test_js_no_adspower_brand() -> None:
    js = js_bundle()
    assert "adspower" not in js.lower(), "JS bundle must not reference AdsPower"


def test_nav_sections_in_js() -> None:
    js = js_bundle()
    for section in ["Overview", "Profiles", "Devices", "Fingerprints",
                    "Network", "Jobs", "Audit", "Settings"]:
        assert section in js, f"nav section missing from JS bundle: {section}"


def test_ten_detail_tabs() -> None:
    js = js_bundle()
    for tab in ["overview", "network", "fingerprint", "device", "cookies",
                "storage", "runtime", "health", "automation", "audit"]:
        assert f'"{tab}"' in js, f"detail tab missing from JS bundle: {tab}"


def test_six_wizard_steps() -> None:
    js = js_bundle()
    for step in ["Basic", "Device", "Network", "Fingerprint", "Consistency", "Review"]:
        assert f'"{step}"' in js, f"wizard step missing from JS bundle: {step}"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"PASS {t.__name__}")
    print(f"\n{passed}/{len(tests)} smoke tests passed")
