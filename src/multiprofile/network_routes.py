"""Flask routes for network diagnostics and profile consistency.

Registered by the coordinator via ``register_network_routes(app)``.
Secrets are never included in responses, logs, or audit entries.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import jsonify, request

from .audit import audit_log
from .consistency import ProfileConsistencyEngine
from .network import NetworkConfig, network_config_for_profile, run_diagnostics
from .storage.profiles import get_profile

log = logging.getLogger("multiprofile.network_routes")

_SENSITIVE_KEYS = ("password", "passphrase", "secret", "token", "proxyPass")


def _scrub(value: Any) -> Any:
    """Recursively strip sensitive keys from a payload before logging/returning."""
    if isinstance(value, dict):
        return {
            k: ("***" if any(s in k.lower() for s in _SENSITIVE_KEYS) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _resolve_device_preset(profile: dict[str, Any]) -> dict[str, Any] | None:
    """Look up the device preset for a profile.

    The devices registry lives in the sibling ``devices`` package; a failed
    import must degrade gracefully (preset None -> engine WARNING), never
    crash the route.  The preset is returned as a plain dict with the keys
    the consistency engine understands (viewport, device_scale_factor,
    mobile, touch).
    """
    try:
        from .devices.registry import registry as _registry
    except ImportError:
        log.info("devices registry unavailable; device checks will warn")
        return None
    device_id = profile.get("deviceId") or profile.get("device_id")
    if not device_id:
        return None
    try:
        preset = _registry.get(device_id)
    except Exception as e:  # noqa: BLE001 - broad by design; the failure is logged, not hidden
        log.warning("device preset lookup failed: %s", e)
        return None
    if preset is None:
        log.info("unknown device id %r; device checks will warn", device_id)
        return None
    try:
        return preset.to_mobile_config_dict()
    except Exception as e:  # noqa: BLE001 - broad by design; the failure is logged, not hidden
        log.warning("device preset serialization failed: %s", e)
        return None


def register_network_routes(app) -> None:
    """Register network diagnostics + consistency routes on a Flask app."""

    # ------------------------------------------------------------------ #
    #  Network diagnostics                                                 #
    # ------------------------------------------------------------------ #
    @app.post("/api/profiles/<profile_id>/network/test")
    def network_test(profile_id: str):
        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "profile not found"}), 404
        net: NetworkConfig = network_config_for_profile(profile)
        try:
            timeout = float(request.args.get("timeout") or 10)
        except (TypeError, ValueError):
            timeout = 10.0
        try:
            diag = run_diagnostics(net, profile_id=profile_id, timeout=timeout)
        except Exception as e:  # unexpected failure -> 500 with {"error"}
            log.exception("network test failed for %s", profile_id)
            return jsonify({"error": str(e)}), 500

        # Audit WITHOUT secret values — diagnostics never carry credentials.
        audit_log(
            "network_test",
            profile_id=profile_id,
            ok=diag.health == "HEALTHY",
            detail={
                "mode": diag.mode,
                "health": diag.health,
                "egressIp": diag.egress_ip,
                "countryCode": diag.country_code,
                "latencyMs": diag.latency_ms,
                "error": diag.error,
            },
        )
        return jsonify(_scrub(diag.to_dict()))

    # ------------------------------------------------------------------ #
    #  Consistency check                                                   #
    # ------------------------------------------------------------------ #
    @app.get("/api/profiles/<profile_id>/consistency")
    def profile_consistency(profile_id: str):
        profile = get_profile(profile_id)
        if not profile:
            return jsonify({"error": "profile not found"}), 404
        preset = _resolve_device_preset(profile)
        engine = ProfileConsistencyEngine()
        try:
            result = engine.check(
                profile,
                device_preset=preset,
                diagnostics=profile.get("lastNetworkDiagnostics"),
                fingerprint_expected=profile.get("fingerprint"),
            )
        except Exception as e:
            log.exception("consistency check failed for %s", profile_id)
            return jsonify({"error": str(e)}), 500

        audit_log(
            "consistency_check",
            profile_id=profile_id,
            ok=result.get("status") != "ERROR",
            detail=_scrub({"status": result.get("status")}),
        )
        return jsonify(_scrub(result))
