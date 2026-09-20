from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from .. import __version__
from ..browser.launcher import find_chrome, launch_profile, stop_profile, extension_src
from ..cookies_store import (
    export_cookies_json,
    import_cookies,
    load_cookies,
    save_cookies,
)
from ..fingerprint import fingerprint_report
from ..proxy_util import parse_proxy, test_proxy
from ..storage.profiles import (
    create_profile,
    delete_profile,
    duplicate_profile,
    get_profile,
    load_profiles,
    update_profile,
)
from ..storage.xdg import chrome_profile_dir, data_dir, profiles_path
from .user_agents import USER_AGENTS

log = logging.getLogger("multiprofile.api")


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


def create_app() -> Flask:
    app = Flask(__name__)

    # ---- Health ----
    @app.get("/api/health")
    def health():
        chrome_path = None
        chrome_ok = False
        try:
            chrome_path = find_chrome()
            chrome_ok = True
        except FileNotFoundError:
            chrome_path = None

        static_ok = False
        try:
            static_ok = (static_dir() / "index.html").exists()
        except Exception:
            static_ok = False

        extension_ok = False
        try:
            extension_ok = (extension_src() / "manifest.json").exists()
        except Exception:
            extension_ok = False

        profiles_writable = False
        try:
            p = profiles_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            test = p.parent / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            profiles_writable = True
        except Exception:
            profiles_writable = False

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
            }
        )

    # ---- User agents ----
    @app.get("/api/user-agents")
    def user_agents():
        return jsonify(USER_AGENTS)

    # ---- Profiles CRUD ----
    @app.get("/api/profiles")
    def profiles():
        return jsonify(load_profiles())

    @app.post("/api/profiles")
    def profiles_create():
        payload = request.get_json(force=True, silent=True) or {}
        if "userAgentId" not in payload and "uaId" in payload:
            payload["userAgentId"] = payload["uaId"]
        profile = create_profile(payload)
        return jsonify({"ok": True, "profile": profile})

    @app.put("/api/profiles/<profile_id>")
    def profiles_update(profile_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        updated = update_profile(profile_id, payload)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "profile": updated})

    @app.delete("/api/profiles/<profile_id>")
    def profiles_delete(profile_id: str):
        ok = delete_profile(profile_id)
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True})

    @app.post("/api/profiles/<profile_id>/launch")
    def profiles_launch(profile_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        # Apply proxy unless explicitly direct=true
        direct = bool(payload.get("direct", False))
        result = launch_profile(
            profile_id,
            url=payload.get("url"),
            direct=direct,
        )
        code = 200 if result.get("ok") else 500
        return jsonify(result), code

    @app.post("/api/profiles/<profile_id>/stop")
    def profiles_stop(profile_id: str):
        return jsonify(stop_profile(profile_id))

    @app.post("/api/profiles/<profile_id>/duplicate")
    def profiles_duplicate(profile_id: str):
        dup = duplicate_profile(profile_id)
        if not dup:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "profile": dup})

    @app.get("/api/profiles/<profile_id>/fingerprint")
    def profiles_fingerprint(profile_id: str):
        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "not found"}), 404
        return jsonify(fingerprint_report(profile))

    # ---- Cookies ----
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
            raw = request.get_data(as_text=True)
            cookies = import_cookies(raw)
        elif isinstance(payload, dict) and "cookies" in payload:
            cookies = import_cookies(payload["cookies"])
        else:
            cookies = import_cookies(payload)
        # merge with existing
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

    # ---- Proxy test ----
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
        result = test_proxy(parsed, timeout=float(payload.get("timeout", 8)))
        return jsonify(result)

    # ---- AdsPower-style thin aliases ----
    @app.post("/api/v1/profile/start")
    def v1_profile_start():
        payload = request.get_json(force=True, silent=True) or {}
        pid = payload.get("profile_id") or payload.get("id")
        if not pid:
            return jsonify({"code": -1, "msg": "profile_id required"}), 400
        result = launch_profile(pid, url=payload.get("url"), direct=bool(payload.get("direct", False)))
        if result.get("ok"):
            return jsonify({"code": 0, "msg": "success", "data": result})
        return jsonify({"code": -1, "msg": result.get("error") or "failed", "data": result}), 500

    @app.post("/api/v1/profile/stop")
    def v1_profile_stop():
        payload = request.get_json(force=True, silent=True) or {}
        pid = payload.get("profile_id") or payload.get("id")
        if not pid:
            return jsonify({"code": -1, "msg": "profile_id required"}), 400
        result = stop_profile(pid)
        return jsonify({"code": 0, "msg": "success", "data": result})

    @app.get("/api/v1/profile/list")
    def v1_profile_list():
        return jsonify({"code": 0, "msg": "success", "data": {"list": load_profiles()}})

    # ---- SPA compatibility stubs ----
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

    # ---- Static SPA ----
    @app.get("/")
    def index():
        return send_from_directory(static_dir(), "index.html")

    @app.get("/<path:path>")
    def static_proxy(path: str):
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        folder = static_dir()
        return send_from_directory(folder, path)

    return app
