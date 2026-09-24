"""GitHub PAT accounts for Cloud Runner UI (stored under XDG data dir)."""
from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .xdg import github_accounts_path as github_accounts_path

_lock = threading.RLock()
_UA = "multiprofile-native"


def mask_token(token: str) -> str:
    t = (token or "").strip()
    if len(t) <= 8:
        return "****"
    return t[:4] + "…" + t[-4:]


def _read() -> list[dict[str, Any]]:
    path = github_accounts_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(items: list[dict[str, Any]]) -> None:
    path = github_accounts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def public_account(acc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": acc.get("id"),
        "username": acc.get("username"),
        "name": acc.get("name") or acc.get("username"),
        "avatar_url": acc.get("avatar_url") or "",
        "label": acc.get("label") or "",
        "repo": acc.get("repo") or "",
        "token_masked": acc.get("token_masked") or mask_token(str(acc.get("token") or "")),
        "is_default": bool(acc.get("is_default")),
        "scopes": acc.get("scopes") or "",
    }


def list_accounts() -> list[dict[str, Any]]:
    with _lock:
        return [public_account(a) for a in _read()]


def get_account(acc_id: str) -> dict[str, Any] | None:
    with _lock:
        for a in _read():
            if a.get("id") == acc_id:
                return a
        return None


def verify_github_token(token: str) -> dict[str, Any]:
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "Personal Access Token is required"}
    req = Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": _UA,
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            raw_scopes = (resp.headers.get("X-OAuth-Scopes") or "").replace(" ", "")
            scope_list = [s for s in raw_scopes.split(",") if s]
    except HTTPError as e:
        if e.code in (401, 403):
            return {
                "ok": False,
                "error": "Invalid GitHub token. Generate a classic PAT with repo and workflow scopes.",
            }
        return {"ok": False, "error": f"GitHub API error: HTTP {e.code}"}
    except URLError as e:
        return {"ok": False, "error": f"Could not reach GitHub: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    login = (body.get("login") or "").strip()
    if not login:
        return {"ok": False, "error": "GitHub user lookup failed"}

    if scope_list:
        has_repo = "repo" in scope_list or any(s == "repo" or s.startswith("repo:") for s in scope_list)
        has_workflow = "workflow" in scope_list
        missing = []
        if not has_repo:
            missing.append("repo")
        if not has_workflow:
            missing.append("workflow")
        if missing:
            return {
                "ok": False,
                "error": (
                    "Token missing scopes: "
                    + ", ".join(missing)
                    + ". Open the token link and tick repo and workflow, then generate a new token."
                ),
            }

    return {
        "ok": True,
        "username": login,
        "name": body.get("name") or login,
        "avatar_url": body.get("avatar_url") or "",
        "scopes": ", ".join(scope_list) if scope_list else "fine-grained",
    }


def add_account(token: str, label: str = "", is_default: bool = False) -> dict[str, Any]:
    checked = verify_github_token(token)
    if not checked.get("ok"):
        return checked
    username = checked["username"]
    with _lock:
        items = _read()
        items = [a for a in items if a.get("username") != username]
        if is_default or not items:
            for a in items:
                a["is_default"] = False
            is_default = True
        acc = {
            "id": "gh_" + uuid.uuid4().hex[:10],
            "username": username,
            "name": checked.get("name") or username,
            "avatar_url": checked.get("avatar_url") or "",
            "label": (label or "").strip(),
            "repo": f"{username}/antidetect-studio-proxy",
            "token": token.strip(),
            "token_masked": mask_token(token),
            "is_default": bool(is_default),
            "scopes": checked.get("scopes") or "",
        }
        items.append(acc)
        _write(items)
        return {"ok": True, "account": public_account(acc)}


def delete_account(acc_id: str) -> bool:
    with _lock:
        items = _read()
        new_items = [a for a in items if a.get("id") != acc_id]
        if len(new_items) == len(items):
            return False
        if new_items and not any(a.get("is_default") for a in new_items):
            new_items[0]["is_default"] = True
        _write(new_items)
        return True


def set_default(acc_id: str) -> bool:
    with _lock:
        items = _read()
        found = False
        for a in items:
            if a.get("id") == acc_id:
                a["is_default"] = True
                found = True
            else:
                a["is_default"] = False
        if found:
            _write(items)
        return found


def verify_account(acc_id: str) -> dict[str, Any]:
    acc = get_account(acc_id)
    if not acc:
        return {"success": False, "error": "account not found"}
    checked = verify_github_token(str(acc.get("token") or ""))
    if not checked.get("ok"):
        return {"success": False, "error": checked.get("error") or "verify failed"}
    return {
        "success": True,
        "username": checked.get("username"),
        "scopes": checked.get("scopes") or "",
    }
