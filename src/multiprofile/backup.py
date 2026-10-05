"""Profile backup / restore — plaintext (default) or AES-256-GCM encrypted (opt-in).

ENCRYPTION DESIGN
-----------------
When a passphrase is provided:
    Key derivation:  PBKDF2-HMAC-SHA256, 600_000 iterations, 32-byte salt, 32-byte key
    Encryption:      AES-256-GCM, 12-byte IV, 16-byte tag
    Wire format:     encrypted_backup.bin = VERSION(1) + SALT(32) + IV(12) + TAG(16) + CIPHERTEXT(n)
    Package:         The zip is created first, then its bytes are encrypted to .mp_enc

The zip itself is always created in plaintext (standard zipfile) and then
optionally encrypted.  This keeps the backup logic simple and lets the
stdlib zipfile handle compression.

Security properties:
- Authenticated encryption (GCM tag protects integrity)
- Wrong passphrase → decryption raises InvalidTag → explicit error
- No passphrase is ever logged, stored in audit, or returned in API responses

Dependencies:
- With encryption: `cryptography` package (already in many environments)
- Without encryption: stdlib only

Backwards compatibility:
- Existing .zip files are always readable by restore_backup() without passphrase
- Version byte 0x01 identifies the encrypted format
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import audit_log
from .cookies_store import cookies_path, load_cookies, save_cookies
from .storage.clients import load_clients
from .storage.profiles import get_profile, load_profiles, save_profiles, update_profile
from .storage.xdg import backups_dir, chrome_profile_dir

log = logging.getLogger("multiprofile.backup")

_SKIP_DIR_NAMES = {"Cache", "Code Cache", "GPUCache", "Service Worker", "ShaderCache", "GrShaderCache"}
_ENC_VERSION = b"\x01"
_PBKDF2_ITERS = 600_000
_SALT_LEN = 32
_IV_LEN = 12


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _should_skip(rel: Path) -> bool:
    return bool(set(rel.parts) & _SKIP_DIR_NAMES)


def _encrypt_bytes(plaintext: bytes, passphrase: str) -> bytes:
    """Encrypt bytes with AES-256-GCM.  Returns binary blob."""
    try:
        from cryptography.hazmat.primitives import hashes as _h
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as e:
        raise RuntimeError(
            "Encrypted backups require the 'cryptography' package. "
            "Install it with: pip install cryptography"
        ) from e

    salt = os.urandom(_SALT_LEN)
    kdf = PBKDF2HMAC(algorithm=_h.SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITERS)
    key = kdf.derive(passphrase.encode("utf-8"))
    iv = os.urandom(_IV_LEN)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext, None)  # ciphertext includes 16-byte GCM tag at end
    return _ENC_VERSION + salt + iv + ciphertext


def _decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    """Decrypt a blob produced by _encrypt_bytes.  Raises on wrong passphrase."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives import hashes as _h
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as e:
        raise RuntimeError(
            "Encrypted backups require the 'cryptography' package."
        ) from e

    if not blob or blob[0:1] != _ENC_VERSION:
        raise ValueError(f"Unknown backup format version: {blob[0:1].hex() if blob else 'empty'}")
    offset = 1
    salt = blob[offset : offset + _SALT_LEN]
    offset += _SALT_LEN
    iv = blob[offset : offset + _IV_LEN]
    offset += _IV_LEN
    ciphertext_with_tag = blob[offset:]

    kdf = PBKDF2HMAC(algorithm=_h.SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITERS)
    key = kdf.derive(passphrase.encode("utf-8"))
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(iv, ciphertext_with_tag, None)
    except InvalidTag:
        raise ValueError("Decryption failed — wrong passphrase or corrupted backup") from None


def _build_manifest(
    profile_id: str,
    stamp: str,
    profile: dict[str, Any],
    cookies: list,
    include_user_data: bool,
    encrypted: bool,
    zip_sha256: str,
) -> dict[str, Any]:
    """Build a structured backup manifest.  Never includes secrets."""
    _SECRET_FIELDS = frozenset({
        "pid", "isRunning", "isQueued", "isFailed", "launchState",
        "cdpPort", "proxyPass", "password", "customProxy",
    })
    safe_profile = {k: v for k, v in profile.items() if k not in _SECRET_FIELDS}

    all_clients = []
    try:
        all_clients = [{"id": c.get("id"), "name": c.get("name")} for c in load_clients()]
    except Exception:  # noqa: BLE001,S110 - the manifest records an empty client list rather than failing the backup
        pass

    return {
        "version": "2.0",
        "format": "multiprofile-backup",
        "createdAt": stamp,
        "profileId": profile_id,
        "profileIds": [profile_id],
        "clientIds": [c["id"] for c in all_clients],
        "profile": safe_profile,
        # Embed cookies in manifest for reliable roundtrip
        "cookies": cookies,
        "includeUserData": include_user_data,
        "encryption": {
            "algorithm": "AES-256-GCM" if encrypted else "none",
            "kdf": "PBKDF2-HMAC-SHA256" if encrypted else None,
            "iterations": _PBKDF2_ITERS if encrypted else None,
        },
        "checksums": {
            "zip_sha256": zip_sha256,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_backup(
    profile_id: str,
    *,
    include_user_data: bool = True,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Create a backup zip for a profile.

    If passphrase is provided the zip bytes are encrypted with AES-256-GCM
    and saved as .mp_enc alongside a manifest.json.
    Plaintext zip is always written first; encrypted version replaces it if requested.

    Never logs or stores the passphrase.
    """
    profile = get_profile(profile_id)
    if not profile:
        return {"ok": False, "error": "profile not found"}

    dest_dir = backups_dir() / profile_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = _ts()
    zip_path = dest_dir / f"{stamp}.zip"

    # Step 1: Build plaintext zip (always)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Cookies
        cpath = cookies_path(profile_id)
        if cpath.exists():
            zf.write(cpath, arcname=f"cookies/{cpath.name}")
        # User data
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

    zip_sha256 = _sha256_file(zip_path)
    # Load cookies for embedding in manifest
    cookies = load_cookies(profile_id)
    manifest = _build_manifest(
        profile_id, stamp, profile, cookies, include_user_data,
        encrypted=bool(passphrase), zip_sha256=zip_sha256
    )


    if passphrase:
        # Step 2: Encrypt zip bytes → .mp_enc
        enc_path = dest_dir / f"{stamp}.mp_enc"
        manifest_path = dest_dir / f"{stamp}.manifest.json"
        try:
            zip_bytes = zip_path.read_bytes()
            encrypted_blob = _encrypt_bytes(zip_bytes, passphrase)
            enc_path.write_bytes(encrypted_blob)
            os.chmod(enc_path, 0o600)
            # Manifest stored unencrypted (contains no secrets)
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.chmod(manifest_path, 0o600)
            # Remove plaintext zip — encrypted version is the canonical backup
            zip_path.unlink(missing_ok=True)
            enc_sha256 = _sha256_file(enc_path)
            manifest["checksums"]["enc_sha256"] = enc_sha256
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            result = {
                "ok": True,
                "backupId": f"{profile_id}/{stamp}",
                "backupPath": str(enc_path),
                "manifestPath": str(manifest_path),
                "sha256": enc_sha256,
                "encrypted": True,
                "profileId": profile_id,
            }
            audit_log("backup", profile_id=profile_id, ok=True,
                      detail={"path": str(enc_path), "encrypted": True})
            return result
        except Exception as e:  # noqa: BLE001 - broad by design; the failure is logged, not hidden
            # Plaintext zip still exists as fallback
            log.error("Encryption failed, plaintext backup retained: %s", e)
            return {"ok": False, "error": f"Encryption failed: {e}", "plaintextPath": str(zip_path)}
    else:
        # Plaintext path: write manifest alongside zip
        checksum_path = dest_dir / f"{stamp}.sha256"
        manifest_path = dest_dir / f"{stamp}.manifest.json"
        checksum_path.write_text(f"{zip_sha256}  {zip_path.name}\n", encoding="utf-8")
        # Inject zip sha256 into manifest and write as meta.json inside zip
        with zipfile.ZipFile(zip_path, "a") as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        result = {
            "ok": True,
            "backupId": f"{profile_id}/{stamp}",
            "backupPath": str(zip_path),
            "checksumPath": str(checksum_path),
            "sha256": zip_sha256,
            "encrypted": False,
            "profileId": profile_id,
        }
        audit_log("backup", profile_id=profile_id, ok=True,
                  detail={"path": str(zip_path), "encrypted": False})
        return result


def list_backups(profile_id: str | None = None) -> list[dict[str, Any]]:
    root = backups_dir()
    items: list[dict[str, Any]] = []
    if profile_id:
        dirs = [root / profile_id] if (root / profile_id).is_dir() else []
    else:
        dirs = [d for d in root.iterdir() if d.is_dir()] if root.exists() else []
    for d in dirs:
        # Plaintext zips
        for z in sorted(d.glob("*.zip"), reverse=True):
            stamp = z.stem
            sha_path = d / f"{stamp}.sha256"
            manifest_path = d / f"{stamp}.manifest.json"
            sha = None
            if sha_path.exists():
                try:
                    sha = sha_path.read_text(encoding="utf-8").split()[0]
                except Exception:  # noqa: BLE001,S110 - sha stays None, which the listing already renders as unknown
                    pass
            items.append({
                "backupId": f"{d.name}/{stamp}",
                "profileId": d.name,
                "backupPath": str(z),
                "sha256": sha,
                "encrypted": False,
                "size": z.stat().st_size,
                "mtime": z.stat().st_mtime,
                "hasManifest": manifest_path.exists(),
            })
        # Encrypted backups
        for enc in sorted(d.glob("*.mp_enc"), reverse=True):
            stamp = enc.stem
            manifest_path = d / f"{stamp}.manifest.json"
            items.append({
                "backupId": f"{d.name}/{stamp}",
                "profileId": d.name,
                "backupPath": str(enc),
                "sha256": None,
                "encrypted": True,
                "size": enc.stat().st_size,
                "mtime": enc.stat().st_mtime,
                "hasManifest": manifest_path.exists(),
            })
    items.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
    return items


def _resolve_backup(
    backup_path: str | None = None,
    backup_id: str | None = None,
) -> Path | None:
    if backup_path:
        p = Path(backup_path)
        if p.exists():
            return p
    if backup_id:
        parts = backup_id.strip("/").split("/")
        if len(parts) == 2:
            base = backups_dir() / parts[0]
            for ext in (".zip", ".mp_enc"):
                p = base / f"{parts[1]}{ext}"
                if p.exists():
                    return p
        for ext in (".zip", ".mp_enc"):
            cand = list(backups_dir().rglob(f"*{ext}"))
            for c in cand:
                if backup_id in c.stem:
                    return c
    return None


def restore_backup(
    *,
    backup_path: str | None = None,
    backup_id: str | None = None,
    profile_id: str | None = None,
    passphrase: str | None = None,
) -> dict[str, Any]:
    """Restore a backup.

    Supports both plaintext .zip and encrypted .mp_enc backups.
    For encrypted backups, passphrase must be provided.
    Never logs the passphrase.
    """
    zpath = _resolve_backup(backup_path, backup_id)
    if not zpath:
        return {"ok": False, "error": "backup not found"}

    try:
        # Determine if encrypted
        if zpath.suffix == ".mp_enc":
            if not passphrase:
                return {
                    "ok": False,
                    "error": "This backup is encrypted. Provide the passphrase to restore.",
                }
            blob = zpath.read_bytes()
            zip_bytes = _decrypt_bytes(blob, passphrase)
            import io
            zip_file_obj = io.BytesIO(zip_bytes)
            zf_ctx = zipfile.ZipFile(zip_file_obj, "r")
        else:
            zf_ctx = zipfile.ZipFile(zpath, "r")

        with zf_ctx as zf:
            # Read manifest: for encrypted backups, the external .manifest.json file is canonical.
            # For plaintext backups, manifest.json is embedded inside the zip.
            manifest: dict[str, Any] = {}

            # Try external manifest file first (encrypted backups)
            ext_manifest_path = zpath.with_name(zpath.stem + ".manifest.json")
            if ext_manifest_path.exists():
                try:
                    manifest = json.loads(ext_manifest_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001,S110 - an unreadable manifest falls through to the next candidate below
                    pass

            # Fall back to internal manifest.json or meta.json (plaintext backups)
            if not manifest:
                for name in ("manifest.json", "meta.json"):
                    if name in zf.namelist():
                        try:
                            manifest = json.loads(zf.read(name).decode("utf-8"))
                        except Exception:  # noqa: BLE001,S110 - an unreadable manifest falls through to the next candidate below
                            pass
                        break

            src_profile = manifest.get("profile") or {}
            target_id = profile_id or manifest.get("profileId") or src_profile.get("id")
            if not target_id:
                return {"ok": False, "error": "cannot determine target profileId"}


            payload = {
                k: v for k, v in src_profile.items()
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

            # Restore cookies (inline in manifest or from cookies/ dir)
            #
            # F11: this used to swallow every failure per member and say
            # nothing. A restore whose cookie members were all corrupt
            # returned ok:True with no sessions -- the operator restores a
            # profile precisely to get its logins back, and got a clean
            # success with none of them. "Restored nothing" and "there was
            # nothing to restore" have to be different answers.
            cookies_restored = 0
            cookie_failures: list[str] = []
            cookies = manifest.get("cookies")
            if isinstance(cookies, list):
                save_cookies(target_id, cookies)
                cookies_restored = len(cookies)
            else:
                cookie_members = [n for n in zf.namelist() if n.startswith("cookies/")]
                for name in cookie_members:
                    try:
                        raw = json.loads(zf.read(name).decode("utf-8"))
                        if isinstance(raw, list):
                            save_cookies(target_id, raw)
                            cookies_restored += len(raw)
                        else:
                            cookie_failures.append(f"{name}: not a cookie list")
                    except Exception as e:  # noqa: BLE001 - one bad member must not abort the restore
                        cookie_failures.append(f"{name}: {e}")
                if cookie_failures:
                    log.warning(
                        "restore %s: %d cookie member(s) could not be read: %s",
                        target_id, len(cookie_failures), "; ".join(cookie_failures[:5]),
                    )

            # Restore user-data
            user_dir = chrome_profile_dir(target_id) / "user-data"
            user_members = [n for n in zf.namelist() if n.startswith("user-data/") and not n.endswith("/")]
            if user_members:
                user_dir.mkdir(parents=True, exist_ok=True)
                for name in user_members:
                    dest = user_dir / name[len("user-data/"):]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)

        result = {
            "ok": True,
            "profileId": target_id,
            "backupPath": str(zpath),
            "encrypted": zpath.suffix == ".mp_enc",
            "restoredUserData": bool(user_members) if 'user_members' in dir() else False,
            # Always present, both of them: a count of 0 with no failures is
            # "this backup had no cookies", and a count of 0 with failures is
            # "this backup had cookies and you did not get them". Reporting
            # only on trouble would make the two look alike again.
            "restoredCookies": cookies_restored,
            "cookieFailures": cookie_failures,
        }
        audit_log("restore", profile_id=target_id, ok=True,
                  detail={"path": str(zpath), "encrypted": zpath.suffix == ".mp_enc",
                          "cookies": cookies_restored,
                          "cookieFailures": len(cookie_failures)})
        return result

    except ValueError as e:
        # Wrong passphrase or corrupt backup — never log the passphrase itself
        audit_log("restore", profile_id=profile_id, ok=False, detail="decryption_failed")
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - the failure is reported in the response, not swallowed
        audit_log("restore", profile_id=profile_id, ok=False, detail=str(e))
        return {"ok": False, "error": str(e)}
