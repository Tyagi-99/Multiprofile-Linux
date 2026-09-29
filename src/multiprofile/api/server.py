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
from ..browser import identity_faults
from ..browser import manager as browser_manager
from ..browser.health import get_all_health, get_profile_health
from ..browser.launcher import extension_src, find_chrome, launch_profile, stop_profile
from ..browser.layout_manager import get_layout_manager
from ..browser.runtime_state import get_runtime
from ..browser.sandbox import probe_sandbox
from ..browser.work_area import WorkAreaError, WorkAreaProvider
from ..config import get_settings
from ..cookies_store import (
    export_cookies_json,
    import_cookies,
    load_cookies,
    save_cookies,
)
from ..engine import detect_engine_version
from ..fingerprint import fingerprint_report
from ..jobs import cancel_job, create_job, get_job, list_jobs
from ..network import network_config_for_profile
from ..profile_import import DEFAULT_PROXY_TYPE, import_profiles
from ..proxy_util import parse_proxy, test_proxy
from ..storage.clients import (
    create_client,
    delete_client,
    load_clients,
    update_client,
)
from ..storage.gateways import (
    create_gateway,
    delete_gateway,
    get_gateway,
    load_gateways,
    resolve_gateway,
    update_gateway,
)
from ..storage.github_accounts import (
    add_account as gh_add_account,
)
from ..storage.github_accounts import (
    delete_account as gh_delete_account,
)
from ..storage.github_accounts import (
    list_accounts as gh_list_accounts,
)
from ..storage.github_accounts import (
    set_default as gh_set_default,
)
from ..storage.github_accounts import (
    verify_account as gh_verify_account,
)
from ..storage.profiles import (
    clone_profile,
    create_profile,
    delete_profile,
    duplicate_profile,
    get_profile,
    load_profiles,
    query_page,
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

# v1.7.0 API hardening (Workstream 3): validation, cursor pagination, auth,
# live events. WS1 (events bus / jobs table) and WS2 (metrics routes) are
# wired defensively — the app must boot even if those modules are absent.
from .auth import register_auth, register_request_guard
from .event_bus import emit_job_progress, emit_profile_status, get_bus
from .pagination import decode_cursor, encode_cursor, paginate, parse_limit
from .sse import register_events_routes
from .user_agents import user_agent_catalog
from .validation import (
    ValidationError,
    register_error_handlers,
    require_json,
    v_str,
    validate_clone_payload,
    validate_job_payload,
    validate_profile_payload,
    validate_proxy_test_payload,
)

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


def _secrets_status() -> dict:
    """Secrets-at-rest state for /api/health. Never raises."""
    try:
        from ..secrets import status

        return status()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": "unknown", "keychain": False, "message": str(e)}


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


# The launch state machine is finer-grained than the operator's vocabulary:
# POSITIONING and CDP_READY are steps inside "starting", and five distinct
# failures are all "error" to someone scanning a table. Collapse them here so
# every client speaks the same four words the console colours.
_OPERATOR_STATE = {
    "RUNNING": "running",
    "VERIFIED": "running",
    "LAUNCHING": "starting",
    "CDP_READY": "starting",
    "WINDOW_FOUND": "starting",
    "POSITIONING": "starting",
    "CREATED": "stopped",
    "LAUNCH_FAILED": "error",
    "CDP_FAILED": "error",
    "WINDOW_NOT_FOUND": "error",
    "POSITION_FAILED": "error",
    "VERIFY_FAILED": "error",
}


def _operator_state(launch_state: str | None) -> str | None:
    """Collapse a LaunchState to the word the operator table shows."""
    if not launch_state:
        return None
    return _OPERATOR_STATE.get(str(launch_state).upper())


#: Dropped from list rows. The generated fingerprint is 2,114 bytes of a
#: 2,795-byte row -- 76% of everything the list endpoint sends -- and the
#: console's table never reads it: the inspector fetches
#: ``/api/profiles/<id>/fingerprint`` when an operator opens one. Sending it
#: with every row is what made a larger page expensive, so a page of 500 cost
#: 1.4 MB of which 1.1 MB was never looked at.
_LIST_OMITTED_FIELDS = ("fingerprint",)


def _list_rows(profiles: list) -> list:
    """Project profiles for the operator table."""
    rows = _with_launch_state(profiles)
    return [
        {k: v for k, v in row.items() if k not in _LIST_OMITTED_FIELDS}
        for row in rows
    ]


def _with_launch_state(profiles: list) -> list:
    """Project runtime truth onto profile rows for the operator table.

    The console has to scan state, proxy result, last launch and -- when a
    launch failed -- the reason, without opening each profile. All of it
    already exists: the in-memory registry holds the launch state, the proxy
    probe result and the launch error, and control.db holds the durable
    started_at. None of it reached the list response before, so a blocked
    launch showed only as "error" with the reason swallowed.

    Credential-free by construction: only the probe verdict and the egress IP
    are exposed, never the proxy password.
    """
    try:
        from ..storage import db as _control_db
        durable = {r.get("profile_id"): r for r in _control_db.list_profile_runtimes()}
    except Exception as e:  # noqa: BLE001 - listing must not fail on a DB hiccup
        log.debug("control.db runtime lookup failed: %s", e)
        durable = {}

    out = []
    for p in profiles:
        q = dict(p)
        pid = q.get("id") or ""
        rt = get_runtime(pid)
        if rt:
            q["launchState"] = rt.launch_state.value if hasattr(rt.launch_state, "value") else rt.launch_state
            q["cdpPort"] = rt.cdp_port
            q["pid"] = rt.pid
            # The operator must see why a launch failed, in the row.
            q["launchError"] = rt.error
            q["launchErrorCode"] = rt.error_code
            # Proxy verdict from the pre-launch probe. None = never checked.
            q["proxyOk"] = rt.proxy_ok
            q["egressIp"] = rt.egress_ip
            q["lastProxyCheck"] = rt.last_proxy_check
        else:
            q.setdefault("launchState", None)
            q.setdefault("cdpPort", None)
            q.setdefault("launchError", None)
            q.setdefault("launchErrorCode", None)
            q.setdefault("proxyOk", None)
            q.setdefault("egressIp", None)
            q.setdefault("lastProxyCheck", None)
        row = durable.get(pid) or {}
        q["lastLaunch"] = row.get("started_at")
        # Operator-facing state. Parking lives on the profile itself; after
        # that control.db is the durable truth, and the live registry fills
        # the gap for a profile the launcher has touched but not yet recorded.
        q["state"] = (
            ("parked" if str(q.get("status") or "").lower() == "parked" else None)
            or _operator_state(q.get("launchState"))
            or row.get("state")
            or "stopped"
        )
        out.append(q)
    return out

#: The most profiles one "select all" may resolve to. Not a storage limit --
#: the query is cheap either way -- but a guard on what a single click can set
#: in motion: launching ten thousand browsers because someone cleared a filter
#: is not a thing the operator meant.
SELECT_ALL_CAP = 5000

#: The largest page the profile list will serve. Higher than the default cap
#: because a list row lost its fingerprint (2,795 bytes -> 663), so a full page
#: is about 320 KB rather than 1.4 MB, and the database returns it in roughly a
#: millisecond. It bounds one response, not one query.
PROFILE_LIST_MAX_LIMIT = 500


def _read_all_audit() -> list:
    """Read the full audit log for keyset pagination (newest-unbounded)."""
    try:
        return read_audit(limit=1_000_000)
    except Exception as e:
        log.warning("audit read failed: %s", e)
        return []


def _runtime_state_name(profile_id: str) -> str | None:
    """Best-effort current launch state name for event payloads."""
    try:
        rt = get_runtime(profile_id or "")
    except Exception:
        return None
    if not rt:
        return None
    state = getattr(rt, "launch_state", None)
    return getattr(state, "value", state)


def create_app() -> Flask:
    app = Flask(__name__)
    _start_reaper()

    # v1.7.0: validation errors -> 400 {"error": {"code": "invalid_input", ...}}
    register_error_handlers(app)
    # v1.7.0: non-localhost requests require a bearer token; localhost is
    # exempt by deliberate tradeoff (see auth.py for the honest assessment).
    # Order matters: the browser guard runs before the token gate so a
    # rebound or cross-origin request is refused on its shape, whatever
    # credentials it carries.
    register_request_guard(app)
    register_auth(app)

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

        from ..storage.db import db_path as _db_path

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
                # Where profiles actually live. Reporting profiles.json here
                # after the move to SQLite would have pointed an operator
                # debugging a missing profile at a file that no longer exists
                # -- and on a migrated install, at one holding stale data.
                "profiles_path": str(_db_path()),
                "profiles_store": "sqlite",
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
                # Which key backend is protecting the proxy passwords. The
                # operator must be able to see when it is the weaker one.
                "secrets": _secrets_status(),
                # What version profiles are allowed to claim, and on what
                # authority. source="unavailable" means the claim on the wire
                # was never compared to the engine running it (F5).
                "engine": detect_engine_version(chrome_path).to_dict(),
                # Steps on the identity path that failed quietly (F11).
                # "faults": [] is a statement, not an absence: nothing has
                # been swallowed since this process started.
                "identity": identity_faults.health_summary(),
                "reaped": reaped,
            }
        )

    @app.get("/api/user-agents")
    def user_agents():
        return jsonify(user_agent_catalog())

    @app.get("/api/profiles")
    def profiles():
        # Cursor pagination: ?cursor=<opaque>&limit=N (default 25, max 100,
        # clamped silently, applied limit echoed back). Filters still apply
        # before the keyset cut. Sort key is the profile id (unique, so no
        # keyset bugs on ties).
        q = request.args.get("q")
        client_id = request.args.get("clientId")
        status = request.args.get("status")
        tag = request.args.get("tag")
        limit = parse_limit(request.args.get("limit"), maximum=PROFILE_LIST_MAX_LIMIT)

        # The database orders, filters and cuts the page. This used to collect
        # every match into Python first -- in chunks of 500, each of which
        # re-read the whole store -- so one page of 25 out of 10,000 profiles
        # took five seconds. The cursor contract is unchanged: keyset on id,
        # opaque cursor, `total` counts every match rather than the page.
        position = decode_cursor(request.args.get("cursor"))
        after_id = str(position[1]) if position is not None else None
        result = query_page(
            q=q, client_id=client_id, status=status, tag=tag,
            after_id=after_id, limit=limit,
        )
        items = result["items"]
        next_cursor = None
        if result["has_more"] and items:
            last_id = str(items[-1].get("id") or "")
            next_cursor = encode_cursor(last_id, last_id)
        return jsonify({
            "items": _list_rows(items),
            "next_cursor": next_cursor,
            "limit": result["limit"],
            "total": result["total"],
        })

    @app.get("/api/profiles/ids")
    def profiles_ids():
        """Every profile id matching the filters, for select-all.

        Selection in the console is bounded by the page, so "select all" means
        "select the 25 rows you can see" and a bulk action on a filtered set of
        400 means paging through it sixteen times. Resolving the filter to ids
        server-side is what lets the client act on the whole match; it returns
        ids rather than profiles because the client already has everything else
        it needs and a full set of 10,000 profiles is megabytes it will not use.

        Capped: a selection larger than the cap is refused with the count, so
        the client can say what it would have selected instead of silently
        acting on a truncated set -- which is how a "delete all" quietly
        deletes some.
        """
        q = request.args.get("q")
        client_id = request.args.get("clientId")
        status = request.args.get("status")
        tag = request.args.get("tag")
        result = query_page(
            q=q, client_id=client_id, status=status, tag=tag,
            limit=SELECT_ALL_CAP + 1,
        )
        ids = [str(p.get("id") or "") for p in result["items"]]
        if len(ids) > SELECT_ALL_CAP:
            return jsonify({
                "ok": False,
                "error": "selection_too_large",
                "total": result["total"],
                "cap": SELECT_ALL_CAP,
                "message": (
                    f"{result['total']} profiles match; narrow the filter or "
                    f"act on at most {SELECT_ALL_CAP} at a time"
                ),
            }), 413
        return jsonify({"ids": ids, "total": result["total"], "cap": SELECT_ALL_CAP})

    @app.get("/api/profiles/<profile_id>")
    def profiles_detail(profile_id: str):
        """One profile, in full.

        There was no such route. The console's detail page fetched
        ``/api/profiles?limit=500`` and searched the page for the id it
        wanted -- but the list clamps ``limit`` to 100, so with more than a
        hundred profiles the console could not open any of them past the
        first hundred. Measured with 150 profiles: asking for 500 returned
        100, and the detail page for #149 rendered "not found" while the
        profile was sitting in the store.

        Nothing reported the truncation, which is why it survived: the
        request succeeded, the page rendered, and the answer was wrong.

        Unlike a list row this keeps the fingerprint, because the thing
        asking for one profile is the thing that wants its detail.
        """
        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "not_found", "id": profile_id}), 404
        return jsonify(_with_launch_state([profile])[0])

    @app.post("/api/profiles")
    def profiles_create():
        payload = require_json()
        field_warnings = validate_profile_payload(payload, partial=False)
        if "userAgentId" not in payload and "uaId" in payload:
            payload["userAgentId"] = payload["uaId"]
        # A profile created in a workspace joins that workspace's proxy pool
        # unless it names one itself. This is the point of a workspace: the
        # operator sets the route once, not once per profile.
        if not payload.get("poolId"):
            from ..storage.clients import workspace_default_pool

            inherited = workspace_default_pool(payload.get("clientId"))
            if inherited:
                payload["poolId"] = inherited
        profile = create_profile(payload)
        # Field warnings first: a setting that never took effect explains the
        # consistency warnings underneath it more often than the reverse.
        warnings = field_warnings + _all_warnings(profile)
        audit_log("profile_create", profile_id=profile.get("id"), ok=True)
        return jsonify({"ok": True, "profile": profile, "warnings": warnings})

    @app.put("/api/profiles/<profile_id>")
    def profiles_update(profile_id: str):
        payload = require_json()
        field_warnings = validate_profile_payload(payload, partial=True)
        updated = update_profile(profile_id, payload)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "ok": True,
            "profile": updated,
            "warnings": field_warnings + _all_warnings(updated),
        })

    @app.delete("/api/profiles/<profile_id>")
    def profiles_delete(profile_id: str):
        purge = request.args.get("purgeStorage", "1")
        purge_storage = str(purge).lower() not in ("0", "false", "no")
        ok = delete_profile(profile_id, purge_storage=purge_storage)
        if not ok:
            return jsonify({"error": "not found"}), 404
        audit_log("delete", profile_id=profile_id, ok=True, detail={"purgeStorage": purge_storage})
        return jsonify({"ok": True, "purgeStorage": purge_storage})

    @app.post("/api/profiles/import")
    def profiles_import():
        """Create one profile per proxy in a pasted list.

        The fleet-filling operation the product was missing: create, clone,
        duplicate and from-template all made one profile at a time, so an
        operator arriving with a block of proxies from a provider had to type
        each one in.

        Body: ``{"proxies": "<text or list>", "namePrefix": "...",
        "proxyType": "socks5", "tags": [...], "clientId": ..., "poolId": ...,
        "startUrl": ...}``. Everything but ``proxies`` is optional; the extra
        fields are applied to every profile in the batch.

        Partial success by design. A bad line does not reject the batch --
        someone pasting 150 proxies usually has one typo, and failing all 150
        for it means finding the typo by bisection. The response reports every
        row by the input line it came from, so a caller fixes two lines rather
        than re-pasting everything.
        """
        payload = require_json()
        raw = payload.get("proxies")
        if raw is None:
            raw = payload.get("proxyList") or payload.get("list")
        if not isinstance(raw, (str, list)) or not raw:
            raise ValidationError(
                "proxies",
                "give a list of proxies, as newline-separated text or an array",
            )

        base: dict[str, object] = {}
        for key in ("tags", "clientId", "poolId", "startUrl", "timezone",
                    "language", "webrtcMode", "color", "notes"):
            if key in payload and payload[key] is not None:
                base[key] = payload[key]

        report = import_profiles(
            raw,
            name_prefix=str(payload.get("namePrefix") or "Imported"),
            proxy_type=str(payload.get("proxyType") or DEFAULT_PROXY_TYPE),
            base=base,
        )
        audit_log(
            "profiles_import",
            ok=True,
            detail={k: v for k, v in report["counts"].items()},
        )
        # 207: some rows may have failed while others were created, and the
        # caller has to look at the body either way. A flat 200 would let a
        # client treat "created 0 of 150" as success.
        status = 200 if report["counts"]["failed"] == 0 else 207
        return jsonify(report), status

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
        # INTEGRATION POINT (WS1): best-effort profile.status event from the
        # route. The browser supervisor should publish the authoritative
        # transition from the real state machine once it exists.
        old_state = _runtime_state_name(profile_id)
        result = launch_profile(
            profile_id,
            url=payload.get("url"),
            direct=bool(payload.get("direct", False)),
            cdp=bool(payload.get("cdp", False)),
        )
        emit_profile_status(
            get_bus(), profile_id, old_state,
            "running" if result.get("ok") else "error",
        )
        audit_log("launch", profile_id=profile_id, ok=bool(result.get("ok")), detail=result.get("error"))
        code = 200 if result.get("ok") else 500
        return jsonify(result), code

    @app.post("/api/profiles/<profile_id>/stop")
    def profiles_stop(profile_id: str):
        # INTEGRATION POINT (WS1): best-effort profile.status event from the
        # route; the supervisor owns the authoritative transition.
        old_state = _runtime_state_name(profile_id)
        result = stop_profile(profile_id)
        emit_profile_status(get_bus(), profile_id, old_state, "idle")
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
        payload = require_json()
        tid = payload.get("templateId")
        if not tid:
            return jsonify({"error": "templateId required"}), 400
        if not isinstance(tid, str):
            raise ValidationError("templateId", "must be a string")
        if "name" in payload and payload["name"] is not None:
            v_str(payload["name"], "name", min_len=1, max_len=120)
        if "clientId" in payload and payload["clientId"] is not None:
            v_str(payload["clientId"], "clientId", min_len=1, max_len=128)
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
        # Network config door: validate scheme whitelist, port range, timeout
        # before anything touches the network.
        payload = validate_proxy_test_payload(require_json())
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

    # ------------------------------------------------------------------ #
    #  Workspaces — clients plus operating defaults                        #
    # ------------------------------------------------------------------ #
    @app.get("/api/workspaces")
    def workspaces_list():
        """Clients with defaultPoolId and tags filled in for older records."""
        from ..storage.clients import load_workspaces

        return jsonify(load_workspaces())

    # ------------------------------------------------------------------ #
    #  Shared proxy pools                                                  #
    # ------------------------------------------------------------------ #
    @app.get("/api/pools")
    def pools_list():
        from ..storage.pools import load_pools, pool_usage, to_safe_dict

        out = []
        for p in load_pools():
            safe = to_safe_dict(p)
            safe["usage"] = pool_usage(p["id"])
            safe["profileCount"] = len(p.get("assignments") or {})
            out.append(safe)
        return jsonify(out)

    @app.post("/api/pools")
    def pools_create():
        from ..storage.pools import create_pool, to_safe_dict

        payload = require_json()
        return jsonify({"ok": True, "pool": to_safe_dict(create_pool(payload))})

    @app.get("/api/pools/<pool_id>")
    def pools_get(pool_id: str):
        from ..storage.pools import get_pool, pool_usage, to_safe_dict

        pool = get_pool(pool_id)
        if not pool:
            return jsonify({"error": "not found"}), 404
        safe = to_safe_dict(pool)
        safe["usage"] = pool_usage(pool_id)
        return jsonify(safe)

    @app.put("/api/pools/<pool_id>")
    def pools_update(pool_id: str):
        from ..storage.pools import to_safe_dict, update_pool

        payload = require_json()
        updated = update_pool(pool_id, payload)
        if not updated:
            return jsonify({"error": "not found"}), 404
        return jsonify({"ok": True, "pool": to_safe_dict(updated)})

    @app.delete("/api/pools/<pool_id>")
    def pools_delete(pool_id: str):
        from ..storage.pools import delete_pool

        if not delete_pool(pool_id):
            return jsonify({"error": "not found"}), 404
        # Profiles keep their poolId: resolving it now blocks with "no longer
        # exists", which is the honest state. Silently clearing it would turn
        # a pooled profile into a direct one without anyone deciding that.
        audit_log("pool_delete", ok=True, detail={"poolId": pool_id})
        return jsonify({"ok": True})

    @app.post("/api/pools/<pool_id>/assign")
    def pools_assign(pool_id: str):
        """Resolve (and record) which member a profile draws from."""
        from ..storage.pools import assign

        payload = require_json()
        profile_id = str(payload.get("profileId") or "").strip()
        if not profile_id:
            return jsonify({"error": "profileId is required"}), 400
        member, reason = assign(pool_id, profile_id)
        if member is None:
            return jsonify({"ok": False, "error": "unassignable", "message": reason}), 409
        audit_log("pool_assign", profile_id=profile_id, ok=True,
                  detail={"poolId": pool_id, "memberId": member["id"]})
        return jsonify({
            "ok": True,
            "reason": reason,
            # Never the password.
            "member": {k: v for k, v in member.items() if k != "password"},
        })

    @app.post("/api/pools/<pool_id>/release")
    def pools_release(pool_id: str):
        from ..storage.pools import release

        payload = require_json()
        profile_id = str(payload.get("profileId") or "").strip()
        if not profile_id:
            return jsonify({"error": "profileId is required"}), 400
        released = release(pool_id, profile_id)
        if released:
            audit_log("pool_release", profile_id=profile_id, ok=True, detail={"poolId": pool_id})
        return jsonify({"ok": True, "released": released})

    # ------------------------------------------------------------------ #
    #  Profile notes — a timestamped log beside the pinned summary         #
    # ------------------------------------------------------------------ #
    @app.get("/api/profiles/<profile_id>/notes")
    def profile_notes_list(profile_id: str):
        from ..storage.notes import list_notes

        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "not found"}), 404
        return jsonify({"summary": profile.get("notes") or "", "entries": list_notes(profile_id)})

    @app.post("/api/profiles/<profile_id>/notes")
    def profile_notes_add(profile_id: str):
        from ..storage.notes import add_note

        payload = require_json()
        try:
            entry = add_note(profile_id, payload.get("text") or "", kind=payload.get("kind") or "note")
        except ValueError as e:
            return jsonify({"error": "invalid", "message": str(e)}), 400
        if entry is None:
            return jsonify({"error": "not found"}), 404
        audit_log("profile_note_add", profile_id=profile_id, ok=True)
        return jsonify({"ok": True, "entry": entry}), 201

    @app.delete("/api/profiles/<profile_id>/notes/<note_id>")
    def profile_notes_delete(profile_id: str, note_id: str):
        from ..storage.notes import delete_note

        if not delete_note(profile_id, note_id):
            return jsonify({"error": "not found"}), 404
        audit_log("profile_note_delete", profile_id=profile_id, ok=True)
        return jsonify({"ok": True})

    def _durable_job_epoch(value) -> float:
        """``created_at`` is ISO text in the jobs table, epoch float in legacy."""
        if not value:
            return 0.0
        try:
            from datetime import datetime

            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001 - an unsortable date must not hide a job
            return 0.0

    def _normalize_durable_job(row: dict) -> dict:
        """Present a durable row in the shape the console already reads.

        The two queues were never given a common shape: legacy uses ``type``
        and an epoch ``createdAt`` with a string id, the jobs table uses
        ``job_type``, ISO ``created_at`` and an integer id. The id is prefixed
        so the two can never collide in one list.
        """
        return {
            "id": f"durable:{row.get('id')}",
            "type": row.get("job_type"),
            "status": row.get("status"),
            "createdAt": _durable_job_epoch(row.get("created_at")),
            "updatedAt": _durable_job_epoch(row.get("updated_at")),
            "profileIds": [row.get("profile_id")] if row.get("profile_id") else [],
            "attempts": row.get("attempts"),
            "maxAttempts": row.get("max_attempts"),
            "error": row.get("error"),
            "result": row.get("result"),
            "source": "durable",
        }

    def _list_all_jobs() -> list:
        """Every job from both queues; None when neither can be read.

        Two generations coexist (see ``multiprofile.jobs``): the legacy
        in-process queue that ``POST /api/jobs`` still writes to, and the
        durable SQLite queue whose workers start at boot. Nothing enqueues
        into the durable one from the API yet (F20), but ``parking`` can, and
        a crash recovery re-queues into it -- so a durable job that did exist
        was invisible to every client. Listing both is the smaller half of
        F20; moving the writer is its own task.
        """
        legacy_jobs: list = []
        legacy_failed = False
        try:
            try:
                legacy_jobs = list_jobs(limit=100_000)
            except TypeError:
                # Forward-compat: WS1's jobs table may not accept limit=.
                legacy_jobs = list_jobs()
            legacy_jobs = [{**j, "source": j.get("source") or "legacy"} for j in legacy_jobs]
        except Exception as e:  # noqa: BLE001 - one queue must not hide the other
            log.warning("legacy job queue unavailable: %s", e)
            legacy_failed = True

        durable_jobs: list = []
        durable_failed = False
        try:
            from ..jobs import list_durable_jobs

            durable_jobs = [_normalize_durable_job(r) for r in list_durable_jobs(limit=100_000)]
        except Exception as e:  # noqa: BLE001 - the control DB may be absent
            log.warning("durable job queue unavailable: %s", e)
            durable_failed = True

        if legacy_failed and durable_failed:
            return None
        return legacy_jobs + durable_jobs

    @app.get("/api/jobs")
    def jobs_list():
        all_jobs = _list_all_jobs()
        if all_jobs is None:
            return (
                jsonify(
                    {
                        "error": {
                            "code": "unavailable",
                            "message": "job system not initialized",
                        }
                    }
                ),
                503,
            )
        limit = parse_limit(request.args.get("limit"))
        page = paginate(
            all_jobs,
            cursor=request.args.get("cursor"),
            limit=limit,
            # (createdAt, id): id tie-breaks identical timestamps.
            key_fn=lambda j: (float(j.get("createdAt") or 0), str(j.get("id") or "")),
        )
        return jsonify(
            {
                "ok": True,
                "items": page["items"],
                "jobs": page["items"],  # legacy key kept for the v1.6.0 UI
                "next_cursor": page["next_cursor"],
                "limit": page["limit"],
                "total": page["total"],
            }
        )

    @app.post("/api/jobs")
    def jobs_create():
        # Job enqueue door: type enum, profileIds, flags, url validated here —
        # nothing invalid may reach the launcher/jobs layer.
        payload = validate_job_payload(require_json())
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
        # INTEGRATION POINT (WS1): best-effort job.progress event from the
        # route. The job worker should publish authoritative progress events
        # (queued -> running -> completed/failed) once it exists.
        emit_job_progress(
            get_bus(), job.get("id"), job.get("type"),
            ids[0] if ids else None, "queued", detail=f"{len(ids)} profile(s)",
        )
        return jsonify({"ok": True, "job": job}), 201

    @app.get("/api/jobs/<job_id>")
    def jobs_get(job_id: str):
        try:
            job = get_job(job_id)
        except Exception as e:
            log.warning("job lookup unavailable: %s", e)
            job = None
            unavailable = True
        else:
            unavailable = False
        if unavailable:
            return (
                jsonify(
                    {
                        "error": {
                            "code": "unavailable",
                            "message": "job system not initialized",
                        }
                    }
                ),
                503,
            )
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
        # Cursor pagination over the full log: ?cursor=&limit= (default 25,
        # max 100). Sort key is (ts, line index) — index tie-breaks identical
        # timestamps so entries are never skipped or duplicated.
        limit = parse_limit(request.args.get("limit"))
        entries = _read_all_audit()
        pairs = [
            ((str(e.get("ts") or ""), f"{i:010d}"), e)
            for i, e in enumerate(entries)
        ]
        page = paginate(
            pairs,
            cursor=request.args.get("cursor"),
            limit=limit,
            key_fn=lambda pair: pair[0],
        )
        page["items"] = [entry for _, entry in page["items"]]
        return jsonify({"ok": True, **page})

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
        payload = validate_clone_payload(require_json())
        mode = payload.get("mode") or "config_only"  # validated above
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
        # A route that was required and could not be resolved reads as DIRECT
        # on the dataclass, so it must be reported before the DIRECT branch --
        # otherwise a profile whose pool is empty is told it is "fine, direct"
        # while its next launch refuses to start.
        if net.blocked_reason:
            return jsonify({
                "ok": False,
                "mode": net.mode.value,
                "status": "BLOCKED",
                "message": net.blocked_reason,
                "proxyHealth": None,
            })
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

    # ------------------------------------------------------------------ #
    #  v1.7.0 Workstream 3 routes                                          #
    # ------------------------------------------------------------------ #
    # Live events (SSE). WS-vs-SSE decision: flask-sock/gevent-websocket are
    # not installed; SSE needs no new dependencies. Uses WS1's
    # multiprofile.api.events bus when present (resolved lazily inside the
    # bus accessor), else an in-process fallback bus.
    try:
        register_events_routes(app)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("events routes not registered: %s", e)

    # ------------------------------------------------------------------ #
    #  Parallel workstream modules (guarded imports)                       #
    # ------------------------------------------------------------------ #
    # WS2 metrics routes — absent until Workstream 2 lands; guarded so the
    # app boots either way.
    try:
        from .metrics_routes import register_metrics_routes
        register_metrics_routes(app)
    except ImportError:
        log.info("metrics routes not present (Workstream 2 pending)")
    except Exception as e:  # pragma: no cover - defensive
        log.warning("metrics routes not registered: %s", e)
    # ------------------------------------------------------------------ #
    #  v1.7.0 startup sequence: control-plane recovery + durable job      #
    #  workers + browser supervisor. Order matters: recover() FIRST so   #
    #  workers only pick up jobs left in a sane state. Everything is     #
    #  guarded: the app must boot even if the control DB cannot open or  #
    #  the supervisor is disabled.                                       #
    #                                                                    #
    #  The test suite sets MULTIPROFILE_NO_BACKGROUND=1 so daemon        #
    #  threads from create_app() cannot leak into the pytest process;    #
    #  production deployments leave it unset.                           #
    # ------------------------------------------------------------------ #
    _background = os.environ.get("MULTIPROFILE_NO_BACKGROUND", "").strip().lower() not in (
        "1", "true", "yes",
    )
    if not _background:
        log.info("background services disabled (MULTIPROFILE_NO_BACKGROUND=1)")

    if _background:
        try:
            from ..storage import db as _control_db

            recovered = _control_db.recover()
            log.info(
                "control DB recovered: %d jobs re-queued, %d failed, %d profiles marked error",
                recovered.get("recovered_jobs", 0),
                recovered.get("failed_jobs", 0),
                len(recovered.get("recovered_profiles", [])),
            )
        except Exception as e:  # noqa: BLE001 - fail-safe boot, no crash
            log.warning(
                "control DB unavailable; continuing without durable jobs: %s", e
            )
            _background = False

    if _background:
        try:
            from ..jobs import start_workers as _start_workers

            _start_workers()
            log.info("durable job workers started")
        except Exception as e:  # noqa: BLE001 - fail-safe boot, no crash
            log.warning("job workers failed to start: %s", e)

        # Browser supervisor (crash watchdog). start() is itself a no-op
        # when disabled by config; the guard here keeps a broken
        # supervisor module from ever taking down the API server.
        try:
            if get_settings().supervisor_enabled:
                from ..browser.supervisor import start_supervisor as _start_sup

                _start_sup()
                log.info("browser supervisor started")
            else:
                log.info("browser supervisor disabled by config; skipping")
        except Exception as e:  # noqa: BLE001 - fail-safe boot, no crash
            log.warning("browser supervisor failed to start; continuing without it: %s", e)

    return app
