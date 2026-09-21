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
from ..browser.sandbox import probe_sandbox
from ..browser.window_layout import detect_work_area
from ..config import get_settings
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

        settings = get_settings()
        sandbox_info = None
        work_area_info = None
        try:
            if chrome_path:
                st = probe_sandbox(chrome_path)
                sandbox_info = {
                    "ok": st.ok,
                    "useNoSandbox": st.use_no_sandbox,
                    "reason": st.reason,
                    "chromeSandboxPath": st.chrome_sandbox_path,
                    "chromeSandboxSuid": st.chrome_sandbox_suid,
                    "unprivilegedUserns": st.unprivileged_userns,
                    "runningAsRoot": st.running_as_root,
                }
        except Exception as e:
            sandbox_info = {"ok": False, "reason": f"probe_failed: {e}"}
        try:
            wa = detect_work_area()
            work_area_info = {"x": wa.x, "y": wa.y, "w": wa.w, "h": wa.h, "source": wa.source}
        except Exception as e:
            work_area_info = {"error": str(e)}

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
                "maxConcurrentProfiles": settings.max_concurrent_profiles,
                "allowWebstoreExtensions": settings.allow_webstore_extensions,
                "antiDetectExclusivity": (not settings.allow_webstore_extensions),
                "windowTilingEnabled": settings.window_tiling_enabled,
                "sandbox": sandbox_info,
                "workArea": work_area_info,
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

    @app.post("/api/profiles/<profile_id>/launch")
    def profiles_launch(profile_id: str):
        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "not found"}), 404
        force = str(request.args.get("force", "0")).lower() in ("1", "true", "yes")
        critical = _critical_warnings(profile)
        if critical and not force:
            return jsonify({"ok": False, "error": "consistency_blocked", "warnings": critical}), 409
        payload = request.get_json(force=True, silent=True) or {}
        result = launch_profile(
            profile_id,
            url=payload.get("url"),
            direct=bool(payload.get("direct", False)),
            cdp=bool(payload.get("cdp", False)),
        )
        audit_log("launch", profile_id=profile_id, ok=bool(result.get("ok")), detail=result.get("error"))
        code = 200 if result.get("ok") else 500
        return jsonify(result), code

    @app.post("/api/profiles/<profile_id>/stop")
    def profiles_stop(profile_id: str):
        result = stop_profile(profile_id)
        audit_log("stop", profile_id=profile_id, ok=bool(result.get("ok")), detail=result.get("error"))
        return jsonify(result)

    @app.post("/api/profiles/<profile_id>/duplicate")
    def profiles_duplicate(profile_id: str):
        dup = duplicate_profile(profile_id)
        if not dup:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "profile": dup, "warnings": _all_warnings(dup)})

    @app.get("/api/profiles/<profile_id>/fingerprint")
    def profiles_fingerprint(profile_id: str):
        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "not found"}), 404
        return jsonify(fingerprint_report(profile))

    @app.post("/api/profiles/<profile_id>/backup")
    def profiles_backup(profile_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        include = payload.get("includeUserData", True)
        result = create_backup(profile_id, include_user_data=bool(include))
        code = 200 if result.get("ok") else 404
        return jsonify(result), code

    @app.post("/api/profiles/from-template")
    def profiles_from_template():
        payload = request.get_json(force=True, silent=True) or {}
        tid = payload.get("templateId")
        if not tid:
            return jsonify({"error": "templateId required"}), 400
        tpl = get_template(tid)
        if not tpl:
            return jsonify({"error": "template not found"}), 404
        try:
            count = max(1, min(int(payload.get("count") or 1), 50))
        except (TypeError, ValueError):
            count = 1
        created = []
        for i in range(count):
            overrides = {}
            if payload.get("name"):
                overrides["name"] = payload["name"] if count == 1 else f"{payload['name']} {i+1}"
            elif count > 1:
                overrides["name"] = f"{tpl.get('name') or 'Profile'} {i+1}"
            if "clientId" in payload:
                overrides["clientId"] = payload.get("clientId")
            body = template_to_profile_payload(tpl, overrides)
            created.append(create_profile(body))
        return jsonify({"ok": True, "profiles": created, "count": len(created)})

    @app.get("/api/profiles/<profile_id>/cookies")
    def cookies_get(profile_id: str):
        if not get_profile(profile_id):
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "cookies": load_cookies(profile_id)})

    @app.put("/api/profiles/<profile_id>/cookies")
    def cookies_put(profile_id: str):
        if not get_profile(profile_id):
            return jsonify({"error": "not found"}), 404
        payload = request.get_json(force=True, silent=True)
        if isinstance(payload, list):
            cookies = payload
        elif isinstance(payload, dict):
            cookies = payload.get("cookies") or []
        else:
            cookies = []
        saved = save_cookies(profile_id, cookies)
        return jsonify({"ok": True, "cookies": saved, "count": len(saved)})

    @app.post("/api/profiles/<profile_id>/cookies/import")
    def cookies_import(profile_id: str):
        if not get_profile(profile_id):
            return jsonify({"error": "not found"}), 404
        payload = request.get_json(force=True, silent=True)
        if payload is None:
            cookies = import_cookies(request.get_data(as_text=True))
        elif isinstance(payload, dict) and "cookies" in payload:
            cookies = import_cookies(payload["cookies"])
        else:
            cookies = import_cookies(payload)
        existing = load_cookies(profile_id)
        by_key = {(c.get("domain"), c.get("name"), c.get("path")): c for c in existing}
        for c in cookies:
            by_key[(c.get("domain"), c.get("name"), c.get("path"))] = c
        saved = save_cookies(profile_id, list(by_key.values()))
        return jsonify({"ok": True, "imported": len(cookies), "cookies": saved, "count": len(saved)})

    @app.get("/api/profiles/<profile_id>/cookies/export")
    def cookies_export(profile_id: str):
        if not get_profile(profile_id):
            return jsonify({"error": "not found"}), 404
        body = export_cookies_json(profile_id)
        return Response(
            body,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{profile_id}-cookies.json"'},
        )

    @app.post("/api/proxy/test")
    def proxy_test():
        payload = request.get_json(force=True, silent=True) or {}
        custom = payload.get("customProxy") or ""
        if not custom:
            host = payload.get("host") or payload.get("proxyHost")
            port = payload.get("port") or payload.get("proxyPort")
            user = payload.get("username") or payload.get("user") or payload.get("proxyUser") or ""
            password = payload.get("password") or payload.get("pass") or payload.get("proxyPass") or ""
            proto = (payload.get("protocol") or payload.get("type") or "http").lower()
            if host and port:
                if user and password:
                    custom = f"{proto}://{user}:{password}@{host}:{port}"
                elif user:
                    custom = f"{proto}://{user}@{host}:{port}"
                else:
                    custom = f"{proto}://{host}:{port}"
        parsed = parse_proxy(custom, "custom")
        if not parsed:
            return jsonify({"ok": False, "success": False, "error": "Could not parse proxy"}), 400
        return jsonify(test_proxy(parsed, timeout=float(payload.get("timeout", 8))))

    @app.get("/api/clients")
    def clients_list():
        return jsonify(load_clients())

    @app.post("/api/clients")
    def clients_create():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify({"ok": True, "client": create_client(payload)}), 201

    @app.put("/api/clients/<client_id>")
    def clients_update(client_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        updated = update_client(client_id, payload)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "client": updated})

    @app.delete("/api/clients/<client_id>")
    def clients_delete(client_id: str):
        if not delete_client(client_id):
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})

    @app.get("/api/templates")
    def templates_list():
        return jsonify(load_templates())

    @app.post("/api/templates")
    def templates_create():
        payload = request.get_json(force=True, silent=True) or {}
        return jsonify({"ok": True, "template": create_template(payload)}), 201

    @app.put("/api/templates/<template_id>")
    def templates_update(template_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        updated = update_template(template_id, payload)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "template": updated})

    @app.delete("/api/templates/<template_id>")
    def templates_delete(template_id: str):
        if not delete_template(template_id):
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})

    @app.get("/api/jobs")
    def jobs_list():
        try:
            limit = int(request.args.get("limit", 50))
        except ValueError:
            limit = 50
        return jsonify({"ok": True, "jobs": list_jobs(limit=limit)})

    @app.post("/api/jobs")
    def jobs_create():
        payload = request.get_json(force=True, silent=True) or {}
        ids = payload.get("profileIds") or []
        if isinstance(ids, str):
            ids = [ids]
        try:
            job = create_job(
                payload.get("type"),
                ids,
                url=payload.get("url"),
                direct=bool(payload.get("direct", False)),
                cdp=bool(payload.get("cdp", False)),
            )
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "job": job}), 201

    @app.get("/api/jobs/<job_id>")
    def jobs_get(job_id: str):
        job = get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "job": job})

    @app.post("/api/jobs/<job_id>/cancel")
    def jobs_cancel(job_id: str):
        result = cancel_job(job_id)
        return jsonify(result), (200 if result.get("ok") else 404)

    @app.get("/api/backups")
    def backups_list():
        return jsonify({"ok": True, "backups": list_backups(request.args.get("profileId"))})

    @app.post("/api/backups/restore")
    def backups_restore():
        payload = request.get_json(force=True, silent=True) or {}
        result = restore_backup(
            backup_path=payload.get("backupPath"),
            backup_id=payload.get("backupId"),
            profile_id=payload.get("profileId"),
        )
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.get("/api/audit")
    def audit_list():
        try:
            limit = int(request.args.get("limit", 100))
        except ValueError:
            limit = 100
        return jsonify({"ok": True, "items": read_audit(limit=limit)})

    @app.post("/api/maintenance/reap")
    def maintenance_reap():
        reaped = reap_orphans()
        for item in reaped:
            audit_log("reap", profile_id=item.get("profileId"), ok=True, detail=item)
        return jsonify({"ok": True, "reaped": reaped, "count": len(reaped)})

    @app.post("/api/v1/profile/start")
    def v1_profile_start():
        payload = request.get_json(force=True, silent=True) or {}
        pid = payload.get("profile_id") or payload.get("id")
        if not pid:
            return jsonify({"code": -1, "msg": "profile_id required"}), 400
        result = launch_profile(
            pid,
            url=payload.get("url"),
            direct=bool(payload.get("direct", False)),
            cdp=bool(payload.get("cdp", False)),
        )
        if result.get("ok"):
            return jsonify({"code": 0, "msg": "success", "data": result})
        return jsonify({"code": -1, "msg": result.get("error") or "failed", "data": result}), 500

    @app.post("/api/v1/profile/stop")
    def v1_profile_stop():
        payload = request.get_json(force=True, silent=True) or {}
        pid = payload.get("profile_id") or payload.get("id")
        if not pid:
            return jsonify({"code": -1, "msg": "profile_id required"}), 400
        return jsonify({"code": 0, "msg": "success", "data": stop_profile(pid)})

    @app.get("/api/v1/profile/list")
    def v1_profile_list():
        return jsonify({"code": 0, "msg": "success", "data": {"list": load_profiles()}})

    @app.get("/api/github-accounts")
    def github_accounts():
        return jsonify([])

    @app.post("/api/github-accounts")
    def github_accounts_create():
        return jsonify({"ok": True, "account": {"id": "local", "login": "local-dev"}}), 201

    @app.delete("/api/github-accounts/<acc_id>")
    def github_accounts_delete(acc_id: str):
        return jsonify({"ok": True})

    @app.post("/api/github-accounts/<acc_id>/verify")
    def github_verify(acc_id: str):
        return jsonify({"ok": False, "error": "GitHub integration not enabled in native build"})

    @app.post("/api/github-accounts/<acc_id>/set-default")
    def github_default(acc_id: str):
        return jsonify({"ok": True})

    @app.get("/api/ads")
    def ads():
        return jsonify({})

    @app.get("/api/donation-config")
    def donation_config():
        return jsonify({"enabled": False})

    @app.post("/api/profiles/<profile_id>/open-folder")
    def open_folder(profile_id: str):
        path = chrome_profile_dir(profile_id)
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass
        return jsonify({"ok": True, "path": str(path)})

    @app.post("/api/profiles/switch-all-direct")
    def switch_all_direct():
        return jsonify({"ok": True})

    @app.post("/api/update/download")
    def update_download():
        return jsonify({"ok": False, "error": "Updater disabled in native build"})

    @app.get("/api/update/status")
    def update_status():
        return jsonify({"status": "disabled"})

    @app.post("/api/update/apply")
    def update_apply():
        return jsonify({"ok": False})

    @app.get("/")
    def index():
        return send_from_directory(static_dir(), "index.html")

    @app.get("/<path:path>")
    def static_proxy(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        return send_from_directory(static_dir(), path)

    return app
