"""One diagnosis, ordered by whether it blocks, short enough to paste.

The roadmap asks for *"concise operator diagnostics for identity and network
failures"*. The information already existed in four places -- identity
faults, permission faults, the identity matrix, the engine verdict, and each
profile's route resolution -- and an operator debugging a profile that would
not load a page had to know which to read, and in what order.

These tests drive it to the **failing** states, because a diagnostic that has
only ever been run on a healthy machine is a diagnostic nobody has tested.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.delenv("MULTIPROFILE_ENGINE_BINARY", raising=False)
    monkeypatch.delenv("MULTIPROFILE_ENGINE_REQUIRE_PINNED", raising=False)
    from multiprofile import engine, engine_policy

    engine.reset_engine_version_cache()
    engine_policy.reset_cache()
    return tmp_path


def test_a_healthy_installation_reports_no_blocking_problems():
    from multiprofile.doctor import run_doctor

    report = run_doctor()
    assert report.ok, [f.to_dict() for f in report.blocking]
    assert report.facts.get("engine", {}).get("version"), report.facts


def test_the_report_carries_the_facts_an_operator_would_ask_for():
    from multiprofile.doctor import run_doctor

    facts = run_doctor().facts
    for key in ("engine", "sandbox", "secrets", "identityMatrix", "profiles"):
        assert key in facts, (key, facts)


def test_the_text_form_is_short_enough_to_paste():
    from multiprofile.doctor import format_report, run_doctor

    text = format_report(run_doctor())
    assert text.count("\n") < 40, f"{text.count(chr(10))} lines is not concise"
    assert "engine" in text and "identity" in text


def test_no_credentials_or_addresses_reach_the_report():
    """It is meant to be pasted into a bug report."""
    import json

    from multiprofile.doctor import format_report, run_doctor
    from multiprofile.storage.profiles import create_profile

    create_profile({
        "name": "secretive", "proxyType": "http", "proxyHost": "10.1.2.3",
        "proxyPort": 8080, "proxyUser": "agent", "proxyPass": "s3cr3t-pw",
    })
    report = run_doctor()
    blob = json.dumps(report.to_dict()) + format_report(report)
    assert "s3cr3t-pw" not in blob
    assert "agent" not in blob


# --------------------------------------------------------------------------
# The failing states
# --------------------------------------------------------------------------

def test_a_missing_engine_is_blocking(monkeypatch):
    from multiprofile import engine_policy
    from multiprofile.doctor import run_doctor

    monkeypatch.setenv("MULTIPROFILE_ENGINE_BINARY", "/nonexistent/chrome")
    engine_policy.reset_cache()
    report = run_doctor()
    engine_findings = [f for f in report.blocking if f.area == "engine"]
    assert engine_findings, [f.to_dict() for f in report.findings]


def test_an_unusable_route_is_blocking_and_names_the_profile():
    """Authenticated SOCKS5 (F56): Chromium cannot use it, so the profile
    will not load a page, and the diagnosis has to say which profile."""
    from multiprofile.doctor import run_doctor
    from multiprofile.storage.profiles import create_profile

    created = create_profile({
        "name": "socks-auth", "proxyType": "socks5",
        "proxyHost": "127.0.0.1", "proxyPort": 1080,
        "proxyUser": "u", "proxyPass": "p",
    })
    report = run_doctor()
    blocking = [f for f in report.blocking if f.area == "network"]
    assert blocking, [f.to_dict() for f in report.findings]
    assert any(f.profile_id == created["id"] for f in blocking), blocking
    assert not report.ok


def test_a_blocked_route_is_blocking():
    """A profile naming a pool that does not exist cannot launch."""
    from multiprofile.doctor import run_doctor
    from multiprofile.storage.profiles import create_profile

    created = create_profile({
        "name": "no-pool", "proxyType": "none", "poolId": "pool_missing",
    })
    report = run_doctor()
    assert any(f.profile_id == created["id"] and f.severity == "blocking"
               for f in report.findings), [f.to_dict() for f in report.findings]


def test_one_failing_check_does_not_hide_the_others(monkeypatch):
    """A diagnostic that dies on its first problem is worse than none."""
    import multiprofile.doctor as doctor_mod

    def explode(report):
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor_mod, "_engine", explode)
    report = doctor_mod.run_doctor()
    assert report.facts.get("sandbox"), "later checks did not run"
    assert any("check failed" in f.summary for f in report.findings), report.findings


def test_blocking_findings_sort_first():
    from multiprofile.doctor import run_doctor
    from multiprofile.storage.profiles import create_profile

    create_profile({
        "name": "bad", "proxyType": "socks5", "proxyHost": "127.0.0.1",
        "proxyPort": 1080, "proxyUser": "u", "proxyPass": "p",
    })
    findings = run_doctor().findings
    severities = [f.severity for f in findings]
    assert severities == sorted(
        severities, key={"blocking": 0, "warning": 1, "info": 2}.get), severities


# --------------------------------------------------------------------------
# Reachable from the CLI and the API
# --------------------------------------------------------------------------

def test_the_cli_exits_non_zero_when_something_blocks():
    from multiprofile.__main__ import main
    from multiprofile.storage.profiles import create_profile

    create_profile({
        "name": "blocker", "proxyType": "socks5", "proxyHost": "127.0.0.1",
        "proxyPort": 1080, "proxyUser": "u", "proxyPass": "p",
    })
    with pytest.raises(SystemExit) as exc:
        main(["--doctor"])
    assert exc.value.code == 1


def test_the_cli_exits_zero_when_healthy():
    from multiprofile.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--doctor"])
    assert exc.value.code == 0


def test_the_api_returns_the_report_with_200_even_when_broken():
    """A non-200 would make a client treat a successful diagnosis of a broken
    install as a failed request."""
    from multiprofile.api.server import create_app
    from multiprofile.storage.profiles import create_profile

    create_profile({
        "name": "blocker", "proxyType": "socks5", "proxyHost": "127.0.0.1",
        "proxyPort": 1080, "proxyUser": "u", "proxyPass": "p",
    })
    response = create_app().test_client().get("/api/doctor")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is False
    assert any(f["severity"] == "blocking" for f in body["findings"])
