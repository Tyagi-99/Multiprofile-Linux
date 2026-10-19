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


def resolve_user_agent_id(user_agent_id: str) -> dict[str, Any] | None:
    """The catalogue entry for an id, realigned to the installed engine.

    Returns ``None`` for an unknown id so the caller can decide: the API
    rejects it, because silently ignoring a selection is how a profile ends up
    claiming something the operator never chose.
    """
    if not user_agent_id:
        return None
    wanted = str(user_agent_id).strip().lower()
    for entry in user_agent_catalog():
        if str(entry.get("id", "")).lower() == wanted:
            return entry
    return None


def known_user_agent_ids() -> list[str]:
    return [str(e["id"]) for e in USER_AGENTS]


def is_catalogue_user_agent(user_agent: str, *, ignore_version: bool = False) -> bool:
    """Whether this UA string is one this catalogue produced.

    Lets a caller tell a UA it derived from a picked id apart from one the
    operator pasted, which is the difference between "changing the picker may
    replace this" and "leave it alone".

    ``ignore_version`` compares the strings with their Chromium version
    tokens blanked. Without it the answer decays: ``user_agent_catalog()``
    aligns its entries to the installed engine, so a UA this catalogue
    produced on Chromium 151 stops matching the catalogue the moment the host
    upgrades to 154, and a derived UA then reads as operator-chosen. That is
    how five profiles came to announce a browser version the engine had not
    been for a month. Identity -- the platform, the model, the token names --
    is what decides provenance here, not the build number.
    """
    if not user_agent:
        return False
    wanted = str(user_agent).strip()
    if ignore_version:
        from ..engine import strip_ua_versions

        key = strip_ua_versions(wanted)
        return any(
            strip_ua_versions(str(e.get("ua", "")).strip()) == key
            for e in list(user_agent_catalog()) + list(USER_AGENTS)
        )
    return any(str(e.get("ua", "")).strip() == wanted for e in user_agent_catalog()) or any(
        str(e.get("ua", "")).strip() == wanted for e in USER_AGENTS
    )


def operator_pinned_user_agent(profile: dict) -> bool:
    """Did the operator choose this profile's UA string themselves?

    True for a UA that did not come from this product: one pasted in, usually
    to match a fingerprint bought elsewhere, where renumbering it would break
    the pairing it was bought for. Such a UA is left exactly as given and the
    disagreement with the engine is recorded instead.

    The signal is ``userAgentId``, not the shape of the string. Provenance
    cannot be read out of the UA itself: a plain Windows-Chrome string is
    both what ``win_chrome`` resolves to and the single most common UA on the
    web, so an operator who pastes one would have it silently renumbered if a
    catalogue match were enough. ``userAgentId`` is the profile's own record
    of having been derived, and it is already the field
    ``_resolve_user_agent_id`` trusts for the same decision on update.

    So: an id present, and a UA that is still the one that id resolves to
    (compared without versions, because an engine upgrade moves the
    catalogue) means derived, and derived claims track the engine. Anything
    else the operator owns.
    """
    if not isinstance(profile, dict):
        return False
    ua = (profile.get("userAgent") or "").strip()
    if not ua:
        return False
    uaid = (profile.get("userAgentId") or "").strip()
    if not uaid:
        return True
    try:
        from ..engine import strip_ua_versions

        entry = resolve_user_agent_id(uaid)
        if not entry:
            return True
        return strip_ua_versions(str(entry.get("ua") or "").strip()) != strip_ua_versions(ua)
    except Exception:  # noqa: BLE001 - an unreadable catalogue must not renumber an operator's UA
        return True
