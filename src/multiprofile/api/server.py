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
from ..browser.layout_manager import get_layout_manager
from ..browser.runtime_state import get_runtime
from ..browser.work_area import WorkAreaError, WorkAreaProvider
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
from ..storage.templates import (
    create_template,
    delete_template,
    get_template,
    load_templates,
    template_to_profile_payload,
    update_template,
)
from ..storage.github_accounts import (
    add_account as gh_add_account,
    delete_account as gh_delete_account,
    list_accounts as gh_list_accounts,
    set_default as gh_set_default,
    verify_account as gh_verify_account,
)
from ..storage.gateways import (
    create_gateway,
    delete_gateway,
    get_gateway,
    load_gateways,
    update_gateway,
    resolve_gateway,
)
from ..storage.profiles import (
    clone_profile,
    create_profile,
    delete_profile,
    duplicate_profile,
    filter_profiles,
    get_profile,
    load_profiles,
    reap_orphans,
    update_profile,
)
from ..network import network_config_for_profile, check_proxy_before_launch, ProxyConfig
from ..browser.health import get_profile_health, get_all_health

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


def _with_launch_state(profiles: list) -> list:
    out = []
    for p in profiles:
        q = dict(p)
        rt = get_runtime(q.get("id") or "")
        if rt:
            q["launchState"] = rt.launch_state.value if hasattr(rt.launch_state, "value") else rt.launch_state
            q["cdpPort"] = rt.cdp_port
        else:
            q.setdefault("launchState", None)
            q.setdefault("cdpPort", None)
        out.append(q)
    return out

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
            wa = WorkAreaProvider().detect()
            work_area_info = {"x": wa.x, "y": wa.y, "w": wa.w, "h": wa.h, "source": wa.source, "ok": True}
        except WorkAreaError as e:
            work_area_info = {"ok": False, "error": "work_area_unavailable", "message": str(e), "reasons": e.reasons}
        except Exception as e:
            work_area_info = {"ok": False, "error": "work_area_unavailable", "message": str(e)}

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
            return jsonify(_with_launch_state(load_profiles()))
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
        result = filter_profiles(
                q=q,
                client_id=client_id,
                status=status,
                tag=tag,
                limit=limit,
                offset=offset,
            )
        if isinstance(result, dict) and "items" in result:
            result = dict(result)
            result["items"] = _with_launch_state(result["items"])
            return jsonify(result)
        if isinstance(result, list):
            return jsonify(_with_launch_state(result))
        return jsonify(result)

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
        passphrase = payload.get("passphrase") or None
        result = create_backup(profile_id, include_user_data=bool(include), passphrase=passphrase)
        # Never return passphrase in response
        result.pop("passphrase", None)
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
                tile_required=bool(
                    payload.get("tileRequired")
                    or (payload.get("options") or {}).get("tileRequired")
                    or False
                ),
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


    @app.post("/api/windows/tile")
    def windows_tile():
        """Fail-closed CDP tiling for running profiles. Never kills browsers."""
        payload = request.get_json(force=True, silent=True) or {}
        ids = payload.get("profileIds")
        if isinstance(ids, str):
            ids = [ids]
        try:
            result = get_layout_manager().tile_running(ids)
        except Exception as e:
            result = {"ok": False, "error": "tile_failed", "message": str(e), "results": []}
        # Always 200 with ok flag — client decides; browsers stay up.
        return jsonify(result)

    @app.get("/api/windows/layout")
    def windows_layout():
        try:
            snap = get_layout_manager().layout_snapshot()
        except Exception as e:
            snap = {"ok": False, "error": str(e), "profiles": []}
        return jsonify(snap)

    @app.get("/api/backups")
    def backups_list():
        return jsonify({"ok": True, "backups": list_backups(request.args.get("profileId"))})

    @app.post("/api/backups/restore")
    def backups_restore():
        payload = request.get_json(force=True, silent=True) or {}
        # passphrase is consumed here and never forwarded to logs or audit
        passphrase = payload.get("passphrase") or None
        result = restore_backup(
            backup_path=payload.get("backupPath"),
            backup_id=payload.get("backupId"),
            profile_id=payload.get("profileId"),
            passphrase=passphrase,
        )
        # Ensure passphrase is never in the response
        result.pop("passphrase", None)
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
        return jsonify(gh_list_accounts())

    @app.post("/api/github-accounts")
    def github_accounts_create():
        payload = request.get_json(silent=True) or {}
        result = gh_add_account(
            str(payload.get("token") or ""),
            label=str(payload.get("label") or ""),
            is_default=bool(payload.get("is_default")),
        )
        if not result.get("ok"):
            return jsonify({"ok": False, "error": result.get("error") or "connect failed"}), 400
        return jsonify(result), 201

    @app.delete("/api/github-accounts/<acc_id>")
    def github_accounts_delete(acc_id: str):
        ok = gh_delete_account(acc_id)
        if not ok:
            return jsonify({"ok": False, "error": "account not found"}), 404
        return jsonify({"ok": True})

    @app.post("/api/github-accounts/<acc_id>/verify")
    def github_verify(acc_id: str):
        result = gh_verify_account(acc_id)
        if not result.get("success"):
            return jsonify(result), 400
        return jsonify(result)

    @app.post("/api/github-accounts/<acc_id>/set-default")
    def github_default(acc_id: str):
        ok = gh_set_default(acc_id)
        if not ok:
            return jsonify({"ok": False, "error": "account not found"}), 404
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

    # ------------------------------------------------------------------ #
    #  Clone profile                                                       #
    # ------------------------------------------------------------------ #
    @app.post("/api/profiles/<profile_id>/clone")
    def profiles_clone(profile_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        mode = payload.get("mode") or "config_only"
        if mode not in ("config_only", "config_and_cookies", "full_session"):
            return jsonify({"error": "mode must be config_only|config_and_cookies|full_session"}), 400
        cloned = clone_profile(profile_id, mode=mode, name=payload.get("name"))
        if not cloned:
            return jsonify({"error": "not found"}), 404
        audit_log("profile_clone", profile_id=profile_id, ok=True, detail={"mode": mode, "newId": cloned.get("id")})
        return jsonify({"ok": True, "profile": cloned, "mode": mode}), 201

    # ------------------------------------------------------------------ #
    #  Network health                                                      #
    # ------------------------------------------------------------------ #
    @app.get("/api/profiles/<profile_id>/network-health")
    def profiles_network_health(profile_id: str):
        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "not found"}), 404
        net = network_config_for_profile(profile)
        if not net.needs_proxy_check():
            return jsonify({
                "ok": True,
                "mode": net.mode.value,
                "status": "DIRECT",
                "message": "Direct connection — no proxy configured",
                "proxyHealth": None,
            })
        try:
            timeout = float(request.args.get("timeout") or 8)
            from ..network import check_proxy
            result = check_proxy(net.proxy, timeout=timeout)
            audit_log("proxy_test", profile_id=profile_id, ok=result.ok,
                      detail={"latencyMs": result.latency_ms, "egressIp": result.egress_ip})
            return jsonify({
                "ok": result.ok,
                "mode": net.mode.value,
                "proxyHealth": result.to_dict(),
                # Never expose credentials
                "proxy": net.proxy.to_dict() if net.proxy else None,
            })
        except Exception as e:
            return jsonify({"ok": False, "mode": net.mode.value, "error": str(e)}), 500

    # ------------------------------------------------------------------ #
    #  Profile health                                                      #
    # ------------------------------------------------------------------ #
    @app.get("/api/profiles/<profile_id>/health")
    def profiles_health(profile_id: str):
        if not get_profile(profile_id):
            return jsonify({"error": "not found"}), 404
        h = get_profile_health(profile_id)
        return jsonify(h.to_dict())

    @app.get("/api/health/profiles")
    def all_profiles_health():
        ids = request.args.getlist("id") or None
        results = get_all_health(ids)
        return jsonify({"ok": True, "profiles": results, "count": len(results)})

    # ------------------------------------------------------------------ #
    #  Gateways CRUD                                                       #
    # ------------------------------------------------------------------ #
    @app.get("/api/gateways")
    def gateways_list():
        return jsonify({"ok": True, "gateways": load_gateways(safe=True)})

    @app.post("/api/gateways")
    def gateways_create():
        payload = request.get_json(force=True, silent=True) or {}
        gw = create_gateway(payload)
        audit_log("gateway_create", ok=True, detail={"gatewayId": gw.get("id"), "providerType": gw.get("providerType")})
        return jsonify({"ok": True, "gateway": gw}), 201

    @app.get("/api/gateways/<gw_id>")
    def gateways_get(gw_id: str):
        gw = get_gateway(gw_id, safe=True)
        if not gw:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "gateway": gw})

    @app.put("/api/gateways/<gw_id>")
    def gateways_update(gw_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        updated = update_gateway(gw_id, payload)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "gateway": updated})

    @app.delete("/api/gateways/<gw_id>")
    def gateways_delete(gw_id: str):
        if not delete_gateway(gw_id):
            return jsonify({"error": "not found"}), 404
        audit_log("gateway_delete", ok=True, detail={"gatewayId": gw_id})
        return jsonify({"ok": True})

    @app.get("/api/gateways/<gw_id>/health")
    def gateways_health(gw_id: str):
        provider = resolve_gateway(gw_id)
        if not provider:
            return jsonify({"error": "not found"}), 404
        try:
            h = provider.health()
            return jsonify({"ok": h.ok, "gatewayId": gw_id, "health": h.to_dict()})
        except Exception as e:
            return jsonify({"ok": False, "gatewayId": gw_id, "error": str(e)}), 500

    @app.post("/api/gateways/<gw_id>/provision")
    def gateways_provision(gw_id: str):
        provider = resolve_gateway(gw_id)
        if not provider:
            return jsonify({"error": "not found"}), 404
        result = provider.provision()
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.get("/")
    def index():
        return send_from_directory(static_dir(), "index.html")

    @app.get("/<path:path>")
    def static_proxy(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        return send_from_directory(static_dir(), path)

    # ------------------------------------------------------------------ #
    #  v1.6.0 extension routes (guarded: app must boot even if a package   #
    #  is mid-development)                                                #
    # ------------------------------------------------------------------ #
    try:
        from ..devices.routes import register_devices_routes
        register_devices_routes(app)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("devices routes not registered: %s", e)
    try:
        from ..network_routes import register_network_routes
        register_network_routes(app)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("network routes not registered: %s", e)

    return app
