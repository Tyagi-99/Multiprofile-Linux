from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from .. import __version__
from ..audit import audit_log, read_audit
from ..backup import create_backup, list_backups, restore_backup
from ..browser import manager as browser_manager
from ..browser.launcher import extension_src, find_chrome, launch_profile, stop_profile
from ..cookies_store import (
    export_cookies_json,
    import_cookies,
    load_cookies,
    save_cookies,
)
from ..fingerprint import fingerprint_report
from ..jobs import cancel_job, create_job, get_job, list_jobs
from ..proxy_util import parse_proxy, test_proxy
from ..storage.clients import (
    create_client,
    delete_client,
    load_clients,
    update_client,
)
from ..storage.profiles import (
    create_profile,
    delete_profile,
    duplicate_profile,
    filter_profiles,
    get_profile,
    load_profiles,
    reap_orphans,
    update_profile,
)
from ..storage.templates import (
    create_template,
    delete_template,
    get_template,
    load_templates,
    template_to_profile_payload,
    update_template,
)
from ..storage.xdg import chrome_profile_dir, data_dir, profiles_path
from .user_agents import USER_AGENTS

log = logging.getLogger("multiprofile.api")
_reaper_started = False


def static_dir() -> Path:
    env = os.environ.get("MULTIPROFILE_STATIC")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.extend(
        [
            Path(__file__).resolve().parents[2] / "static",
            Path("/usr/share/multiprofile/static"),
            Path(__file__).resolve().parents[3] / "src" / "static",
        ]
    )
    for c in candidates:
        if (c / "index.html").exists():
            return c
    raise FileNotFoundError("static UI not found")


def _critical_warnings(profile: dict) -> list[str]:
    report = fingerprint_report(profile)
    return [w for w in (report.get("warnings") or []) if str(w).startswith("CRITICAL")]


def _all_warnings(profile: dict) -> list[str]:
    return list(fingerprint_report(profile).get("warnings") or [])


def _start_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def _loop() -> None:
        while True:
            try:
                reaped = reap_orphans()
                for item in reaped:
                    audit_log("reap", profile_id=item.get("profileId"), ok=True, detail=item)
            except Exception as e:
                log.warning("orphan reaper error: %s", e)
            time.sleep(15)

    threading.Thread(target=_loop, name="mp-reaper", daemon=True).start()


def create_app() -> Flask:
    app = Flask(__name__)
    _start_reaper()

    @app.before_request
    def _check_api_token():
        token = os.environ.get("MULTIPROFILE_API_TOKEN") or ""
        if not token:
            return None
        path = request.path or ""
        if not path.startswith("/api/"):
            return None
        if path == "/api/health" and request.method == "GET":
            return None
        auth = request.headers.get("Authorization") or ""
        if auth == f"Bearer {token}":
            return None
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    @app.get("/api/health")
    def health():
        try:
            reaped = reap_orphans()
        except Exception:
            reaped = []

        chrome_path = None
        chrome_ok = False
        try:
            chrome_path = find_chrome()
            chrome_ok = True
        except FileNotFoundError:
            pass

        static_ok = False
        try:
            static_ok = (static_dir() / "index.html").exists()
        except Exception:
            pass

        extension_ok = False
        try:
            extension_ok = (extension_src() / "manifest.json").exists()
        except Exception:
            pass

        profiles_writable = False
        try:
            p = profiles_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            test = p.parent / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            profiles_writable = True
        except Exception:
            pass

        items = []
        try:
            items = load_profiles()
        except Exception:
            pass

        return jsonify(
            {
                "ok": True,
                "app": "multiprofile-native",
                "version": __version__,
                "chrome_path": chrome_path,
                "chrome_ok": chrome_ok,
                "static_ok": static_ok,
                "extension_ok": extension_ok,
                "profiles_path": str(profiles_path()),
                "profiles_writable": profiles_writable,
                "count": len(items),
                "data_dir": str(data_dir()),
                "slots": browser_manager.slot_status(),
                "reaped": reaped,
            }
        )

    @app.get("/api/user-agents")
    def user_agents():
        return jsonify(USER_AGENTS)

    @app.get("/api/profiles")
    def profiles():
        if not request.args:
            return jsonify(load_profiles())
        q = request.args.get("q")
        client_id = request.args.get("clientId")
        status = request.args.get("status")
        tag = request.args.get("tag")
        try:
            limit = int(request.args.get("limit", 50))
        except ValueError:
            limit = 50
        try:
            offset = int(request.args.get("offset", 0))
        except ValueError:
            offset = 0
        return jsonify(
            filter_profiles(
                q=q,
                client_id=client_id,
                status=status,
                tag=tag,
                limit=limit,
                offset=offset,
            )
        )

    @app.post("/api/profiles")
    def profiles_create():
        payload = request.get_json(force=True, silent=True) or {}
        if "userAgentId" not in payload and "uaId" in payload:
            payload["userAgentId"] = payload["uaId"]
        profile = create_profile(payload)
        warnings = _all_warnings(profile)
        audit_log("profile_create", profile_id=profile.get("id"), ok=True)
        return jsonify({"ok": True, "profile": profile, "warnings": warnings})

    @app.put("/api/profiles/<profile_id>")
    def profiles_update(profile_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        updated = update_profile(profile_id, payload)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "profile": updated, "warnings": _all_warnings(updated)})

    @app.delete("/api/profiles/<profile_id>")
    def profiles_delete(profile_id: str):
        purge = request.args.get("purgeStorage", "1")
        purge_storage = str(purge).lower() not in ("0", "false", "no")
        ok = delete_profile(profile_id, purge_storage=purge_storage)
        if not ok:
            return jsonify({"error": "not found"}), 404
        audit_log("delete", profile_id=profile_id, ok=True, detail={"purgeStorage": purge_storage})
        return jsonify({"ok": True, "purgeStorage": purge_storage})
