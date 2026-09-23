"""Flask routes for the device preset system.

Wired into the main app by the coordinator via register_devices_routes(app).
Uses plain @app.get decorators only; no auth changes; no secrets involved.

``GET /api/devices`` supports cursor pagination (``?cursor=&limit=``) using
the shared scheme from ``multiprofile.api.pagination``. For backward
compatibility the endpoint returns the full legacy list (no ``limit`` key)
when neither ``cursor`` nor ``limit`` is passed; when either is present the
response is ``{"devices": [...], "next_cursor": ..., "limit": N, "total": M}``.
"""
from __future__ import annotations

from flask import jsonify, request

from ..api.pagination import paginate, parse_limit
from .registry import registry
from .validator import DevicePresetValidator

_validator = DevicePresetValidator()


def _summary(device_id: str):
    preset = registry.get(device_id)
    if preset is None:
        return None
    result = _validator.validate(preset)
    return {
        "id": preset.id,
        "name": preset.name,
        "manufacturer": preset.manufacturer,
        "model": preset.model,
        "category": preset.category,
        "platform": preset.platform,
        "viewport": {"width": preset.viewport["width"], "height": preset.viewport["height"]},
        "device_scale_factor": preset.device_scale_factor,
        "touch": preset.touch,
        "mobile": preset.mobile,
        "support": result["status"],
        "validation_date": result["validated_at"],
    }


def register_devices_routes(app) -> None:
    """Register /api/devices* routes on a Flask app."""

    @app.get("/api/devices")
    def list_devices():
        devices = [_summary(p.id) for p in registry.list()]
        # Backward-compatible: only paginate when the client asks for it.
        if request.args.get("cursor") is None and request.args.get("limit") is None:
            return jsonify({"devices": devices})
        limit = parse_limit(request.args.get("limit"))
        page = paginate(
            devices,
            cursor=request.args.get("cursor"),
            limit=limit,
            key_fn=lambda d: (str(d.get("id") or ""), str(d.get("id") or "")),
        )
        return jsonify(
            {
                "devices": page["items"],
                "next_cursor": page["next_cursor"],
                "limit": page["limit"],
                "total": page["total"],
            }
        )

    @app.get("/api/devices/<device_id>")
    def get_device(device_id: str):
        preset = registry.get(device_id)
        if preset is None:
            return jsonify({"error": "unknown device", "device_id": device_id}), 404
        return jsonify(preset.to_dict())

    @app.get("/api/devices/<device_id>/validate")
    def validate_device(device_id: str):
        preset = registry.get(device_id)
        if preset is None:
            return jsonify({"error": "unknown device", "device_id": device_id}), 404
        return jsonify(_validator.validate(preset))
