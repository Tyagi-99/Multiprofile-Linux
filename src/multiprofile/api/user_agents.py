"""The UA picker offered by ``GET /api/user-agents``.

The entries below are *templates*. Their Chromium version literals say 131
because that is what was current when they were written; what the picker
actually offers is those templates realigned to the engine installed on this
host, so an operator cannot pick a Chrome 131 identity out of a dropdown and
run it on a Chromium 151 engine (F5).

Safari entries carry no Chromium token and are returned unchanged.
"""
from __future__ import annotations

import re
from typing import Any

from ..engine import align_ua_string, detect_engine_version

_TEMPLATE_MAJOR = "131"

USER_AGENTS = [
    {"id": "win_chrome", "name": "Windows 11 — Chrome 131", "category": "Desktop", "isMobile": False,
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
    {"id": "win_edge", "name": "Windows 11 — Edge 131", "category": "Desktop", "isMobile": False,
     "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"},
    {"id": "mac_chrome", "name": "macOS Sonoma — Chrome 131", "category": "Desktop", "isMobile": False,
     "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
    {"id": "mac_safari", "name": "macOS Sonoma — Safari 18", "category": "Desktop", "isMobile": False,
     "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"},
    {"id": "linux_chrome", "name": "Linux — Chrome 131", "category": "Desktop", "isMobile": False,
     "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"},
    {"id": "iphone_16_pro", "name": "iPhone 16 Pro — Safari iOS 18", "category": "Mobile", "isMobile": True,
     "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"},
    {"id": "iphone_15", "name": "iPhone 15 — Safari iOS 17", "category": "Mobile", "isMobile": True,
     "ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"},
    {"id": "pixel_9", "name": "Pixel 9 — Chrome Android 15", "category": "Mobile", "isMobile": True,
     "ua": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"},
    {"id": "galaxy_s24", "name": "Galaxy S24 — Chrome Android 14", "category": "Mobile", "isMobile": True,
     "ua": "Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"},
    {"id": "ipad_pro", "name": "iPad Pro — Safari iPadOS 18", "category": "Tablet", "isMobile": True,
     "ua": "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"},
]

_CHROMIUM_NAME_RE = re.compile(rf"\b(Chrome|Edge)\s+{_TEMPLATE_MAJOR}\b")


def user_agent_catalog() -> list[dict[str, Any]]:
    """The picker entries, realigned to the installed engine.

    Unknown engine version -> the templates come back as written. The list
    an operator sees then still says 131, which is at least the truth about
    what this build was authored against; inventing a newer number would
    not be.
    """
    engine = detect_engine_version()
    if not engine.known:
        return [dict(e) for e in USER_AGENTS]

    major = str(engine.major)
    out: list[dict[str, Any]] = []
    for entry in USER_AGENTS:
        item = dict(entry)
        aligned = align_ua_string(item.get("ua", ""), engine)
        if aligned != item.get("ua"):
            item["ua"] = aligned
            # "Windows 11 - Chrome 131" must not label a Chrome 151 string.
            item["name"] = _CHROMIUM_NAME_RE.sub(rf"\1 {major}", str(item.get("name", "")))
        out.append(item)
    return out
