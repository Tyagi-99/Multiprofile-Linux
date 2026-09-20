from __future__ import annotations
import logging
from flask import Flask, jsonify, request, send_from_directory
from pathlib import Path

from ..browser.launcher import launch_profile, stop_profile
from ..storage.profiles import create_profile, get_profile, load_profiles, update_profile
from .user_agents import USER_AGENTS

log = logging.getLogger("multiprofile.api")


def static_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[2] / "static",
        Path("/usr/share/multiprofile/static"),
        Path(__file__).resolve().parents[3] / "src" / "static",
    ]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    raise FileNotFoundError("static UI not found")


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "app": "multiprofile-native", "version": "1.0.0"})

    @app.get("/api/user-agents")
    def user_agents():
        return jsonify(USER_AGENTS)

    @app.get("/api/profiles")
    def profiles():
        return jsonify(load_profiles())

    @app.post("/api/profiles")
    def profiles_create():
        payload = request.get_json(force=True, silent=True) or {}
        # map SPA field names
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

    @app.post("/api/profiles/<profile_id>/launch")
    def profiles_launch(profile_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        result = launch_profile(
            profile_id,
            url=payload.get("url"),
            direct=bool(payload.get("direct", True)),
        )
        code = 200 if result.get("ok") else 500
        return jsonify(result), code

    @app.post("/api/profiles/<profile_id>/stop")
    def profiles_stop(profile_id: str):
        return jsonify(stop_profile(profile_id))

    # SPA compatibility stubs
    @app.get("/api/github-accounts")
    def github_accounts():
        return jsonify([])

    @app.get("/api/ads")
    def ads():
        return jsonify({})

    @app.get("/api/donation-config")
    def donation():
        return jsonify({"enabled": False})

    @app.get("/")
    def index():
        return send_from_directory(static_dir(), "index.html")

    @app.get("/<path:path>")
    def static_proxy(path: str):
        # don't shadow /api
        if path.startswith("api/"):
            return jsonify({"error": "not found"}), 404
        folder = static_dir()
        return send_from_directory(folder, path)


    # ---- SPA compatibility aliases (original app.js paths) ----
    @app.get("/api/user-agents")
    def user_agents_alias():
        return jsonify(USER_AGENTS)

    @app.get("/api/user-agents")
    def user_agents_hyphen():
        return jsonify(USER_AGENTS)

    @app.get("/api/github-accounts")
    def github_accounts_alias():
        return jsonify([])

    @app.post("/api/github-accounts")
    def github_accounts_create():
        return jsonify({"ok": True, "account": {"id": "local", "login": "local-dev"}}), 201

    @app.delete("/api/github-accounts/<acc_id>")
    def github_accounts_delete(acc_id: str):
        return jsonify({"ok": True})

    @app.post("/api/github-accounts/<acc_id>/verify")
    def github_verify(acc_id: str):
        return jsonify({"ok": False, "error": "GitHub integration not enabled in native v1"})

    @app.post("/api/github-accounts/<acc_id>/set-default")
    def github_default(acc_id: str):
        return jsonify({"ok": True})

    @app.delete("/api/profiles/<profile_id>")
    def profiles_delete(profile_id: str):
        from ..storage.profiles import load_profiles, save_profiles
        items = [p for p in load_profiles() if p.get("id") != profile_id]
        save_profiles(items)
        return jsonify({"ok": True})

    @app.post("/api/profiles/<profile_id>/open-folder")
    def open_folder(profile_id: str):
        from ..storage.xdg import chrome_profile_dir
        import subprocess
        path = chrome_profile_dir(profile_id)
        try:
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass
        return jsonify({"ok": True, "path": str(path)})

    @app.post("/api/profiles/switch-all-direct")
    def switch_all_direct():
        return jsonify({"ok": True})

    @app.get("/api/donation-config")
    def donation_config():
        return jsonify({"enabled": False})

    @app.post("/api/proxy/test")
    def proxy_test():
        return jsonify({"ok": False, "error": "Proxy test not implemented in native v1"})

    @app.post("/api/update/download")
    def update_download():
        return jsonify({"ok": False, "error": "Updater disabled in native build"})

    @app.get("/api/update/status")
    def update_status():
        return jsonify({"status": "disabled"})

    @app.post("/api/update/apply")
    def update_apply():
        return jsonify({"ok": False})


    return app
