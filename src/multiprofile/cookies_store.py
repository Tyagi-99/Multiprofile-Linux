"""Per-profile cookie persistence under XDG data dir."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .storage.xdg import data_dir


def cookies_dir() -> Path:
    p = data_dir() / "cookies"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cookies_path(profile_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", profile_id or "unknown")
    return cookies_dir() / f"{safe}.json"


def load_cookies(profile_id: str) -> list[dict[str, Any]]:
    path = cookies_path(profile_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("cookies"), list):
            return data["cookies"]
    except Exception:
        pass
    return []


def save_cookies(profile_id: str, cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = cookies_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [_normalize_cookie(c) for c in cookies if isinstance(c, dict)]
    path.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    return cleaned


def export_cookies_json(profile_id: str) -> str:
    cookies = load_cookies(profile_id)
    return json.dumps(cookies, indent=2)


def _normalize_cookie(c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": str(c.get("name") or ""),
        "value": str(c.get("value") or ""),
        "domain": str(c.get("domain") or c.get("host") or ""),
        "path": str(c.get("path") or "/"),
        "secure": bool(c.get("secure", False)),
        "httpOnly": bool(c.get("httpOnly") or c.get("httponly") or False),
    }
    if "expirationDate" in c:
        try:
            out["expirationDate"] = float(c["expirationDate"])
        except (TypeError, ValueError):
            pass
    elif "expires" in c:
        try:
            exp = float(c["expires"])
            if exp > 0:
                out["expirationDate"] = exp
        except (TypeError, ValueError):
            pass
    if "sameSite" in c:
        out["sameSite"] = c["sameSite"]
    return out


def _parse_netscape(text: str) -> list[dict[str, Any]]:
    """Parse Netscape cookie file format."""
    cookies: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            # Allow #HttpOnly_ prefix
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_") :]
                http_only = True
            else:
                continue
        else:
            http_only = False
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        try:
            exp = float(expires)
        except ValueError:
            exp = 0
        c: dict[str, Any] = {
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
            "httpOnly": http_only,
            "name": name,
            "value": value,
        }
        if exp > 0:
            c["expirationDate"] = exp
        cookies.append(c)
    return cookies


def import_cookies(raw_text_or_list: Any) -> list[dict[str, Any]]:
    """
    Import cookies from Chrome JSON list or Netscape cookie file text.
    Returns normalized list (does not persist — caller saves with profile_id).
    """
    if isinstance(raw_text_or_list, list):
        return [_normalize_cookie(c) for c in raw_text_or_list if isinstance(c, dict)]

    if isinstance(raw_text_or_list, dict):
        if isinstance(raw_text_or_list.get("cookies"), list):
            return import_cookies(raw_text_or_list["cookies"])
        return [_normalize_cookie(raw_text_or_list)]

    text = str(raw_text_or_list or "").strip()
    if not text:
        return []

    # Try JSON first
    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
            return import_cookies(data)
        except json.JSONDecodeError:
            pass

    # Netscape format
    return [_normalize_cookie(c) for c in _parse_netscape(text)]
