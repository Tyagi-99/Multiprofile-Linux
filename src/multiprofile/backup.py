"""Profile backup / restore as plaintext zip + sha256 checksum (MVP, no encryption)."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import audit_log
from .cookies_store import cookies_path, load_cookies, save_cookies
from .storage.profiles import get_profile, load_profiles, save_profiles, update_profile
from .storage.xdg import backups_dir, chrome_profile_dir

_SKIP_DIR_NAMES = {"Cache", "Code Cache", "GPUCache", "Service Worker", "ShaderCache", "GrShaderCache"}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _should_skip(rel: Path) -> bool:
    return bool(set(rel.parts) & _SKIP_DIR_NAMES)


def create_backup(profile_id: str, *, include_user_data: bool = True) -> dict[str, Any]:
    profile = get_profile(profile_id)
    if not profile:
        return {"ok": False, "error": "profile not found"}

    dest_dir = backups_dir() / profile_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = _ts()
    zip_path = dest_dir / f"{stamp}.zip"
    checksum_path = dest_dir / f"{stamp}.sha256"

    meta = {
        "profileId": profile_id,
        "createdAt": stamp,
        "profile": {k: v for k, v in profile.items() if k not in ("pid", "isRunning")},
        "cookies": load_cookies(profile_id),
        "includeUserData": include_user_data,
        "encryption": "none",
        "note": (
            "MVP plaintext zip (stdlib zipfile). "
            "MULTIPROFILE_BACKUP_PASS is reserved; encryption not implemented in 1.2.0."
        ),
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2))
        cpath = cookies_path(profile_id)
        if cpath.exists():
            zf.write(cpath, arcname=f"cookies/{cpath.name}")
        if include_user_data:
            user_dir = chrome_profile_dir(profile_id) / "user-data"
            if user_dir.exists():
                for f in user_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    rel = f.relative_to(user_dir)
                    if _should_skip(rel):
                        continue
                    zf.write(f, arcname=f"user-data/{rel.as_posix()}")

    digest = _sha256_file(zip_path)
    checksum_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    result = {
        "ok": True,
        "backupId": f"{profile_id}/{stamp}",
        "backupPath": str(zip_path),
        "checksumPath": str(checksum_path),
        "sha256": digest,
        "profileId": profile_id,
    }
    audit_log("backup", profile_id=profile_id, ok=True, detail={"path": str(zip_path)})
    return result


def list_backups(profile_id: str | None = None) -> list[dict[str, Any]]:
    root = backups_dir()
    items: list[dict[str, Any]] = []
    if profile_id:
        dirs = [root / profile_id] if (root / profile_id).is_dir() else []
    else:
        dirs = [d for d in root.iterdir() if d.is_dir()] if root.exists() else []
    for d in dirs:
        for z in sorted(d.glob("*.zip"), reverse=True):
            stamp = z.stem
            sha_path = d / f"{stamp}.sha256"
            sha = None
            if sha_path.exists():
                try:
                    sha = sha_path.read_text(encoding="utf-8").split()[0]
                except Exception:
                    pass
            items.append(
                {
                    "backupId": f"{d.name}/{stamp}",
                    "profileId": d.name,
                    "backupPath": str(z),
                    "sha256": sha,
                    "size": z.stat().st_size,
                    "mtime": z.stat().st_mtime,
                }
            )
    items.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
    return items


def _resolve_backup(backup_path: str | None = None, backup_id: str | None = None) -> Path | None:
    if backup_path:
        p = Path(backup_path)
        if p.exists():
            return p
    if backup_id:
        parts = backup_id.strip("/").split("/")
        if len(parts) == 2:
            p = backups_dir() / parts[0] / f"{parts[1]}.zip"
            if p.exists():
                return p
        if "/" not in backup_id:
            cand = list(backups_dir().rglob(f"{backup_id}.zip"))
            if cand:
                return cand[0]
    return None


def restore_backup(
    *,
    backup_path: str | None = None,
    backup_id: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    zpath = _resolve_backup(backup_path, backup_id)
    if not zpath:
        return {"ok": False, "error": "backup not found"}

    try:
        with zipfile.ZipFile(zpath, "r") as zf:
            meta = json.loads(zf.read("meta.json").decode("utf-8"))
            src_profile = meta.get("profile") or {}
            target_id = profile_id or src_profile.get("id") or meta.get("profileId")
            if not target_id:
                return {"ok": False, "error": "cannot determine target profileId"}

            payload = {
                k: v
                for k, v in src_profile.items()
                if k not in ("id", "pid", "isRunning", "status")
            }
            existing = get_profile(target_id)
            if existing:
                update_profile(target_id, payload)
            else:
                items = load_profiles()
                new_p = dict(src_profile)
                new_p["id"] = target_id
                new_p["status"] = "idle"
                new_p.pop("pid", None)
                new_p.pop("isRunning", None)
                items.append(new_p)
                save_profiles(items)

            cookies = meta.get("cookies")
            if isinstance(cookies, list):
                save_cookies(target_id, cookies)

            user_dir = chrome_profile_dir(target_id) / "user-data"
            user_members = [n for n in zf.namelist() if n.startswith("user-data/") and not n.endswith("/")]
            if user_members:
                user_dir.mkdir(parents=True, exist_ok=True)
                for name in user_members:
                    dest = user_dir / name[len("user-data/") :]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)

            result = {"ok": True, "profileId": target_id, "backupPath": str(zpath)}
            audit_log("restore", profile_id=target_id, ok=True, detail={"path": str(zpath)})
            return result
    except Exception as e:
        audit_log("restore", profile_id=profile_id, ok=False, detail=str(e))
        return {"ok": False, "error": str(e)}
