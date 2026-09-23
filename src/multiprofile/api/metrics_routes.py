"""Metrics API routes for Multiprofile-Linux v1.7.0.

Provides ``register_metrics_routes(app)``; Workstream 3 wires it into
``create_app()``. This module never touches the app factory itself.

Routes:
    GET /api/profiles/<profile_id>/metrics
        {"profileId", "cpu_percent", "memory_rss_mb", "uptime_s", "state",
         "measured_at", ...} — honest point-in-time samples; a profile with
        no live PID returns status UNAVAILABLE with the reason, never
        fabricated numbers.
    GET /api/metrics/summary
        Aggregate across running profiles.
"""
from __future__ import annotations

from flask import jsonify

from ..metrics import measure_profile, summary_totals
from ..storage.profiles import get_profile


def register_metrics_routes(app) -> None:
    """Register metrics routes on a Flask app. Called by Workstream 3."""

    @app.get("/api/profiles/<profile_id>/metrics")
    def profile_metrics(profile_id: str):
        if get_profile(profile_id) is None:
            return jsonify({"ok": False, "error": "profile not found"}), 404
        payload = measure_profile(profile_id)
        payload["ok"] = payload.get("status") != "UNAVAILABLE"
        return jsonify(payload)

    @app.get("/api/metrics/summary")
    def metrics_summary():
        payload = summary_totals()
        payload["ok"] = True
        return jsonify(payload)
