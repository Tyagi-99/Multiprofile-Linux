"""Workstream 2 (v1.7.0): idle parking, bounded-concurrency health staggering,
/proc metrics, metrics routes, structured profile logging, audit secret-safety.

No 30-minute waits anywhere: idle tests use a 2s timeout plus backdated
activity timestamps. Chromium is never launched: the launcher's stop path is
stubbed at the parking-module boundary.
"""
from __future__ import annotations

import os
import subprocess
import time

import pytest
from flask import Flask

import multiprofile.parking as parking
from multiprofile import metrics as proc_metrics
from multiprofile.api.metrics_routes import register_metrics_routes
from multiprofile.audit import read_audit
from multiprofile.config import get_settings
from multiprofile.profile_log import audit_event, log_event, read_profile_log
from multiprofile.storage.profiles import create_profile, set_runtime

pytestmark = pytest.mark.skipif(
    not os.path.isdir("/proc"), reason="/proc required for these tests"
)


@pytest.fixture()
def xdg_tmp(tmp_path, monkeypatch):
    """Isolate all XDG state (profiles, runtime, audit, activity, logs)."""
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("MULTIPROFILE_PARKING_ENABLED", "1")
    return tmp_path


@pytest.fixture()
def parked_profile(xdg_tmp, monkeypatch):
    """A profile marked 'running' in the on-disk runtime state."""
    profile = create_profile({"name": "park-test"})
    pid = profile["id"]
    set_runtime(pid, "running", 424242)  # fake pid; parking never signals it
    calls = []

    def fake_stop(profile_id: str):
        calls.append(profile_id)
        set_runtime(profile_id, "idle", None)
        return {"ok": True, "stopped": 424242}

    monkeypatch.setattr(parking, "_stop_via_launcher", fake_stop)
    monkeypatch.setattr(parking, "job_running_for", lambda _pid: False)
    return pid, calls


# ---------------------------------------------------------------------------
# Config surface
# ---------------------------------------------------------------------------

def test_config_defaults(xdg_tmp):
    s = get_settings()
    assert s.parking_enabled is True
    assert s.parking_idle_timeout_s == 1800
    assert s.health_stagger is True
    assert s.health_max_concurrent == 4
    assert s.health_jitter_s == 30
    assert s.metrics_cache_ttl_s == 5


# ---------------------------------------------------------------------------
# Idle parking
# ---------------------------------------------------------------------------

def test_park_triggers_after_idle_timeout(xdg_tmp, parked_profile, monkeypatch):
    pid, calls = parked_profile
    monkeypatch.setenv("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", "2")
    parking.touch_activity(pid, when=time.time() - 3)  # idle 3s > 2s timeout
    assert parking.is_idle(pid) is True
    assert parking.park_profile(pid) is True
    assert calls == [pid]  # graceful stop path used exactly once


def test_active_profile_not_parked(xdg_tmp, parked_profile, monkeypatch):
    pid, calls = parked_profile
    monkeypatch.setenv("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", "2")
    parking.touch_activity(pid)  # just now: active
    assert parking.is_idle(pid) is False
    assert parking.park_profile(pid) is False
    assert calls == []  # stop path must never run for an active profile


def test_profile_with_running_job_not_parked(xdg_tmp, parked_profile, monkeypatch):
    pid, calls = parked_profile
    monkeypatch.setenv("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", "2")
    parking.touch_activity(pid, when=time.time() - 60)  # clearly idle
    monkeypatch.setattr(parking, "job_running_for", lambda _p: True)
    assert parking.park_profile(pid) is False
    assert calls == []
    entries = read_audit(limit=5)
    assert any(
        e["action"] == "park_refused"
        and (e.get("detail") or {}).get("reason") == "job_running_for_profile"
        for e in entries
    ), "refusal must leave an audit note"


def test_park_refused_when_not_running(xdg_tmp, monkeypatch):
    profile = create_profile({"name": "not-running"})
    pid = profile["id"]
    set_runtime(pid, "idle", None)
    calls = []
    monkeypatch.setattr(parking, "_stop_via_launcher",
                        lambda p: calls.append(p) or {"ok": True})
    assert parking.park_profile(pid) is False
    assert calls == []


def test_park_refused_when_unknown_profile(xdg_tmp, monkeypatch):
    calls = []
    monkeypatch.setattr(parking, "_stop_via_launcher",
                        lambda p: calls.append(p) or {"ok": True})
    assert parking.park_profile("profile_nope") is False
    assert calls == []


def test_running_profile_without_activity_is_never_parked_immediately(
    xdg_tmp, parked_profile, monkeypatch
):
    """Bootstrap safety net: first observation of a running profile seeds 'now'."""
    pid, calls = parked_profile
    monkeypatch.setenv("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", "2")
    assert parking.last_activity_ts(pid) is None
    assert parking.is_idle(pid) is False  # seeded, not idle
    assert parking.park_profile(pid) is False
    assert calls == []


# ---------------------------------------------------------------------------
# /proc metrics — honest numbers, honest UNAVAILABLE
# ---------------------------------------------------------------------------

def test_proc_metrics_live_sleep_process(xdg_tmp):
    proc = subprocess.Popen(["sleep", "30"])
    try:
        sample = proc_metrics.sample_pid(proc.pid)
        assert sample["status"] == "OK", sample
        assert sample["pid"] == proc.pid
        assert isinstance(sample["memory_rss_mb"], float) and sample["memory_rss_mb"] > 0
        assert sample["sample"] == "point-in-time"
        assert "measured_at" in sample
    finally:
        proc.kill()
        proc.wait()


def test_proc_metrics_dead_pid_unavailable(xdg_tmp):
    proc = subprocess.Popen(["sleep", "30"])
    proc.kill()
    proc.wait()
    time.sleep(0.2)  # let the kernel reap it
    sample = proc_metrics.sample_pid(proc.pid)
    assert sample["status"] == "UNAVAILABLE"
    assert "reason" in sample and sample["reason"]
    # Never fabricate a zero reading:
    assert "memory_rss_mb" not in sample or sample.get("memory_rss_mb") is None


def test_proc_metrics_invalid_pid_unavailable(xdg_tmp):
    sample = proc_metrics.sample_pid(-5)
    assert sample["status"] == "UNAVAILABLE"


def test_measure_profile_without_pid_is_unavailable(xdg_tmp):
    profile = create_profile({"name": "metrics-test"})
    payload = proc_metrics.measure_profile(profile["id"])
    assert payload["status"] == "UNAVAILABLE"
    assert payload["reason"]
    assert payload["cpu_percent"] is None


def test_summary_totals_counts_running(xdg_tmp):
    p1 = create_profile({"name": "sum-1"})
    p2 = create_profile({"name": "sum-2"})
    set_runtime(p1["id"], "running", 999991)  # dead pids -> UNAVAILABLE
    set_runtime(p2["id"], "running", 999992)
    summary = proc_metrics.summary_totals()
    assert summary["running_profiles"] == 2
    assert summary["unavailable"] == 2
    assert len(summary["profiles"]) == 2
    # Unavailable entries must not contribute fabricated totals:
    assert summary["total_memory_rss_mb"] == 0.0
    assert summary["avg_cpu_percent"] is None


# ---------------------------------------------------------------------------
# Metrics routes (register_metrics_routes only — WS3 wires create_app)
# ---------------------------------------------------------------------------

@pytest.fixture()
def metrics_app(xdg_tmp):
    app = Flask(__name__)
    register_metrics_routes(app)
    app.config["TESTING"] = True
    return app


def test_profile_metrics_route(metrics_app):
    profile = create_profile({"name": "route-test"})
    client = metrics_app.test_client()
    resp = client.get(f"/api/profiles/{profile['id']}/metrics")
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("cpu_percent", "memory_rss_mb", "uptime_s", "state", "measured_at"):
        assert key in body, key
    assert body["ok"] is False  # no live pid -> honestly UNAVAILABLE
    assert body["status"] == "UNAVAILABLE"


def test_profile_metrics_route_404(metrics_app):
    client = metrics_app.test_client()
    assert client.get("/api/profiles/profile_nope/metrics").status_code == 404


def test_metrics_summary_route(metrics_app):
    client = metrics_app.test_client()
    resp = client.get("/api/metrics/summary")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "running_profiles" in body and "profiles" in body


# ---------------------------------------------------------------------------
# Health-check staggering
# ---------------------------------------------------------------------------

def test_compute_health_delays_wave_structure():
    ids = [f"p{i}" for i in range(10)]
    delays = parking.compute_health_delays(
        ids, max_concurrent=4, jitter_s=30, seed=7
    )
    assert [p for p, _ in delays] == ids
    # Wave 0 (first 4) is pure jitter [0, 30); wave 1 starts at wave_gap.
    wave_gap = max(5.0, 30 / 2.0)
    for _, d in delays[:4]:
        assert 0 <= d < 30
    for _, d in delays[4:8]:
        assert d >= wave_gap
    # Deterministic with seed:
    again = parking.compute_health_delays(
        ids, max_concurrent=4, jitter_s=30, seed=7
    )
    assert delays == again


def test_schedule_health_checks_empty_db(xdg_tmp, tmp_path):
    """WS1 profiles_runtime table present but no running profiles: no crash."""
    from multiprofile.storage import db
    db.configure(str(tmp_path / "control.db"))
    try:
        summary = parking.schedule_health_checks(dry_run=True)
        assert summary["source"] == "ws1"
        assert summary["enqueued"] == 0
        assert "no running profiles" in summary["reason"]
    finally:
        db.configure(None)


def _make_running(db, profile_id: str, pid: int) -> None:
    """Seed a 'running' runtime row via the valid state-machine path."""
    db.set_profile_state(profile_id, "starting")
    db.set_profile_state(profile_id, "running")
    db.upsert_profile_runtime(profile_id, pid=pid)


def _seed_running(db, n: int) -> list[str]:
    ids = []
    for i in range(n):
        pid = f"hc-profile-{i}"
        _make_running(db, pid, 50000 + i)
        ids.append(pid)
    return ids


def test_schedule_health_checks_staggers_running_profiles(xdg_tmp, tmp_path, monkeypatch):
    from multiprofile.storage import db
    db.configure(str(tmp_path / "control.db"))
    try:
        # 10 running profiles, 4 per wave, tiny jitter: verify wave plan via dry_run.
        monkeypatch.setenv("MULTIPROFILE_HEALTH_MAX_CONCURRENT", "4")
        monkeypatch.setenv("MULTIPROFILE_HEALTH_JITTER_S", "2")
        ids = _seed_running(db, 10)
        summary = parking.schedule_health_checks(dry_run=True)
        assert summary["source"] == "ws1"
        assert summary["reason"].startswith("dry_run")
        waves = summary["waves"]
        assert len(waves) == 3  # 4 + 4 + 2
        assert [w["profile_id"] for w in waves[0]] == ids[:4]
        wave_gap = max(5.0, 2 / 2.0)
        for entry in waves[0]:
            assert entry["delay_s"] < 2  # pure jitter in wave 0
        for entry in waves[1]:
            assert entry["delay_s"] >= wave_gap
    finally:
        db.configure(None)


def test_enqueue_health_check_uses_durable_queue(xdg_tmp, tmp_path):
    """Real WS1 backend: enqueue returns a durable job id recorded in the db."""
    from multiprofile.jobs import JOB_TYPES, get_durable_job
    from multiprofile.storage import db
    assert "health_check" in JOB_TYPES
    db.configure(str(tmp_path / "control.db"))
    try:
        job_id = parking.enqueue_health_check("profile_hc", 1.5)
        assert isinstance(job_id, int)
        record = get_durable_job(job_id)
        assert record is not None
        assert record["job_type"] == "health_check"
        assert record["profile_id"] == "profile_hc"
        assert record["status"] == "queued"
        assert record["payload"]["stagger_delay_s"] == 1.5
    finally:
        db.configure(None)


def test_job_running_for_sees_durable_queued_job(xdg_tmp, tmp_path, monkeypatch):
    """A queued health_check job blocks parking via the real durable queue."""
    from multiprofile.storage import db
    db.configure(str(tmp_path / "control.db"))
    try:
        pid = "profile_guarded"
        _make_running(db, pid, 61000)
        assert parking.job_running_for(pid) is False
        parking.enqueue_health_check(pid, 0.0)
        assert parking.job_running_for(pid) is True
    finally:
        db.configure(None)


def test_park_job_does_not_self_block(xdg_tmp, tmp_path, monkeypatch):
    """The in-flight 'park' job must not trip the running-job guard.

    The worker marks the job 'running' before dispatching handle_park, so
    excluding 'park'-type jobs is what lets the contract work at all.
    """
    from multiprofile.jobs import enqueue
    from multiprofile.storage import db
    db.configure(str(tmp_path / "control.db"))
    try:
        profile = create_profile({"name": "self-park"})
        pid = profile["id"]
        set_runtime(pid, "running", 63000)
        _make_running(db, pid, 63000)
        monkeypatch.setenv("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", "2")
        parking.touch_activity(pid, when=time.time() - 5)
        calls = []
        monkeypatch.setattr(
            parking, "_stop_via_launcher",
            lambda p: calls.append(p) or (set_runtime(p, "idle", None), {"ok": True, "stopped": 63000})[1],
        )
        enqueue("park", pid, payload={})
        claimed = db.claim_next_job()  # worker marks running before dispatch
        assert claimed and claimed["status"] == "running"
        # The guard sees the running park job but must not block on it:
        assert parking.job_running_for(pid) is False
        assert parking.park_profile(pid) is True
        assert calls == [pid]
    finally:
        db.configure(None)


def test_non_park_job_still_blocks(xdg_tmp, tmp_path, monkeypatch):
    """End-to-end: idle profile + queued durable job -> park refused, audit note."""
    from multiprofile.storage import db
    db.configure(str(tmp_path / "control.db"))
    try:
        profile = create_profile({"name": "guarded-park"})
        pid = profile["id"]
        set_runtime(pid, "running", 62000)
        _make_running(db, pid, 62000)
        monkeypatch.setenv("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", "2")
        parking.touch_activity(pid, when=time.time() - 60)  # idle
        parking.enqueue_health_check(pid, 0.0)  # but a job is queued
        calls = []
        monkeypatch.setattr(parking, "_stop_via_launcher",
                            lambda p: calls.append(p) or {"ok": True})
        assert parking.park_profile(pid) is False
        assert calls == []
        entries = read_audit(limit=5)
        assert any(
            e["action"] == "park_refused"
            and (e.get("detail") or {}).get("reason") == "job_running_for_profile"
            for e in entries
        )
    finally:
        db.configure(None)


def test_read_profiles_runtime_prefers_ws1(xdg_tmp, tmp_path):
    from multiprofile.storage import db
    db.configure(str(tmp_path / "control.db"))
    try:
        _make_running(db, "p-ws1", 70000)
        source, rows = parking.read_profiles_runtime()
        assert source == "ws1"
        by_id = {r["profile_id"]: r for r in rows}
        assert by_id["p-ws1"]["state"] == "running"
        assert by_id["p-ws1"]["pid"] == 70000
    finally:
        db.configure(None)


# ---------------------------------------------------------------------------
# Audit entries + secret safety
# ---------------------------------------------------------------------------

def test_audit_log_gains_entry_on_park(xdg_tmp, parked_profile, monkeypatch):
    pid, _ = parked_profile
    monkeypatch.setenv("MULTIPROFILE_PARKING_IDLE_TIMEOUT_S", "2")
    parking.touch_activity(pid, when=time.time() - 120)  # 2 min idle
    assert read_audit(limit=5) == [] or True  # tolerate pre-existing entries
    before = len(read_audit(limit=1000))
    assert parking.park_profile(pid) is True
    entries = read_audit(limit=1000)
    assert len(entries) > before
    parked = [e for e in entries if e["action"] == "profile_parked"]
    assert parked, "parking must write a 'profile_parked' audit entry"
    assert parked[0]["profileId"] == pid
    assert "idle_min" in (parked[0].get("detail") or {})


def test_profile_log_event_roundtrip(xdg_tmp):
    log_event("profile_abc", "info", "config_changed", {"field": "startUrl"})
    entries = read_profile_log("profile_abc")
    assert entries and entries[0]["event"] == "config_changed"
    assert entries[0]["profileId"] == "profile_abc"
    assert entries[0]["level"] == "info"


def test_secrets_never_reach_audit_or_profile_logs(xdg_tmp, tmp_path):
    pw = "s3cr3t-pw-98765"
    token = "tok-secret-abc123"
    cookie_val = "session-cookie-zzz999"
    detail = {
        "proxyPass": pw,
        "proxy": {"host": "10.0.0.1", "password": pw},
        "token": token,
        "cookies": [{"name": "sid", "value": cookie_val}],
        "note": "harmless text",
    }
    audit_event("config_changed", profile_id="profile_sec", ok=True, detail=detail)
    log_event("profile_sec", "info", "config_changed", detail)

    from multiprofile.storage.xdg import audit_path, data_dir
    raw_audit = audit_path().read_text(encoding="utf-8")
    log_dir = data_dir() / "profile-logs"
    raw_logs = "".join(p.read_text(encoding="utf-8") for p in log_dir.glob("*.jsonl"))

    for secret in (pw, token, cookie_val):
        assert secret not in raw_audit, f"secret leaked into audit log: {secret[:8]}..."
        assert secret not in raw_logs, f"secret leaked into profile log: {secret[:8]}..."
    assert "***REDACTED***" in raw_audit
    assert "***REDACTED***" in raw_logs
    # Non-secret content survives redaction:
    assert "harmless text" in raw_audit
    assert "10.0.0.1" in raw_audit
