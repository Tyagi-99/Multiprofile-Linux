"""The limits, named and in one place.

The numbers existed before this and were scattered and anonymous:
``MAX_PROFILES`` in the profile store, ``MAX_CONCURRENT_PROFILES`` in the
browser manager, neither reachable from the other. An operator who hit one got
a refusal naming a number they had never chosen and could not find.

The property these tests defend hardest is the one this whole audit has been
about: **a reported limit must say whether anything enforces it.** A plan that
lists five limits of which two are real, without saying which, is the same
failure as F1 (a control that reported success while doing nothing) and F19
(an API vocabulary that matched nothing downstream) -- just wearing a
commercial hat. So ``enforced`` is asserted against reality here, not against
the plan's own claims.
"""
from __future__ import annotations

import pytest

from multiprofile import plans
from multiprofile.config import get_settings


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    # A previous test's override must not leak into a plan assertion.
    monkeypatch.delenv("MAX_PROFILES", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_PROFILES", raising=False)
    monkeypatch.delenv("MULTIPROFILE_PLAN", raising=False)
    return tmp_path


@pytest.fixture
def client():
    from multiprofile.api.server import create_app

    return create_app().test_client()


# --------------------------------------------------------------------------
# 1. Which plan is running
# --------------------------------------------------------------------------

def test_a_local_install_runs_the_self_hosted_plan():
    assert plans.active_plan().id == "self-hosted"


def test_the_self_hosted_plan_matches_what_the_product_was_built_for():
    """150 stored / 20 concurrent is the measured operating size (F33, F35).

    If these drift apart, one of them is lying to the operator.
    """
    plan = plans.active_plan()
    assert plan.max_profiles == 150
    assert plan.max_concurrent == 20


def test_naming_a_plan_changes_the_limits(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_PLAN", "solo")
    assert get_settings().max_profiles == 50
    assert get_settings().max_concurrent_profiles == 5


def test_an_unknown_plan_falls_back_and_says_so(monkeypatch):
    """A typo in an environment variable must not stop the app booting, and
    must not silently change the limits without telling anyone."""
    monkeypatch.setenv("MULTIPROFILE_PLAN", "enterprise-platinum")
    assert plans.active_plan().id == "self-hosted"
    body = plans.describe()
    assert body["recognised"] is False
    assert "enterprise-platinum" in body["warning"]


def test_a_recognised_plan_carries_no_warning():
    assert plans.describe()["recognised"] is True
    assert "warning" not in plans.describe()


# --------------------------------------------------------------------------
# 2. Where a number came from
# --------------------------------------------------------------------------

def test_an_environment_override_wins_and_is_labelled(monkeypatch):
    """On a local install the operator owns the machine; pretending the plan
    can overrule them would be theatre. But the report must say which it is,
    because "150 because the plan says so" and "150 because MAX_PROFILES says
    so" send someone to different places to change it."""
    monkeypatch.setenv("MAX_PROFILES", "400")
    limits = plans.limits()
    assert limits["maxProfiles"]["value"] == 400
    assert limits["maxProfiles"]["source"] == "env"
    assert limits["maxProfiles"]["planValue"] == 150


def test_without_an_override_the_source_is_the_plan():
    limits = plans.limits()
    assert limits["maxProfiles"]["source"] == "plan"
    assert limits["maxProfiles"]["value"] == limits["maxProfiles"]["planValue"]


def test_an_empty_override_is_not_an_override(monkeypatch):
    monkeypatch.setenv("MAX_PROFILES", "")
    assert plans.limits()["maxProfiles"]["source"] == "plan"


# --------------------------------------------------------------------------
# 3. Enforced means enforced
# --------------------------------------------------------------------------

def test_the_profile_limit_marked_enforced_is_actually_enforced(client, monkeypatch):
    """Asserted against the store, not against the plan's own claim."""
    monkeypatch.setenv("MULTIPROFILE_PLAN", "solo")
    assert plans.limits()["maxProfiles"]["enforced"] is True
    codes = []
    for i in range(60):
        codes.append(client.post("/api/profiles", json={"name": f"p{i}"}).status_code)
    from multiprofile.storage import db

    assert db.count_profiles() == 50, "the plan's limit did not hold"
    assert 409 in codes


def test_the_concurrency_limit_marked_enforced_reaches_the_browser_manager(monkeypatch):
    monkeypatch.setenv("MULTIPROFILE_PLAN", "solo")
    from multiprofile.browser import manager

    assert plans.limits()["maxConcurrent"]["enforced"] is True
    assert manager.max_concurrent() == 5


@pytest.mark.parametrize("key", ["maxWorkspaces", "maxApiTokens", "includedRuntimeHours"])
def test_a_limit_nothing_checks_is_declared_unenforced(key, monkeypatch):
    """The failure this audit kept finding was a control present in the
    interface and absent from the path. A plan that lists five limits of which
    two are real, without saying which, is that failure in commercial dress.
    """
    monkeypatch.setenv("MULTIPROFILE_PLAN", "solo")
    assert plans.limits()[key]["enforced"] is False


def test_every_limit_says_whether_it_is_enforced():
    for key, limit in plans.limits().items():
        assert "enforced" in limit, f"{key} does not say whether it is enforced"
        assert isinstance(limit["enforced"], bool)


# --------------------------------------------------------------------------
# 4. Usage against the limits
# --------------------------------------------------------------------------

def test_the_plan_reports_what_has_been_used(client):
    from multiprofile.storage import db
    from multiprofile.storage.profiles import create_profile

    pid = create_profile({"name": "Metered"})["id"]
    db.set_profile_state(pid, "starting")
    db.set_profile_state(pid, "running")

    body = client.get("/api/plan").get_json()
    assert body["usage"]["profiles"] == db.count_profiles()
    assert body["usage"]["running"] == 1
    assert body["usage"]["sessionsThisMonth"] == 1


def test_a_usage_field_that_cannot_be_read_is_null_not_zero(monkeypatch):
    """Zero would read as "an empty fleet" rather than as a broken query."""
    from multiprofile.storage import db

    def boom(*a, **k):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(db, "list_profile_runtimes", boom)
    assert plans.describe()["usage"]["running"] is None


# --------------------------------------------------------------------------
# 5. The endpoint
# --------------------------------------------------------------------------

def test_the_plan_endpoint_answers_with_plan_limits_and_usage(client):
    body = client.get("/api/plan").get_json()
    assert body["ok"] is True
    assert body["plan"]["id"] == "self-hosted"
    assert body["limits"]["maxProfiles"]["value"] == 150
    assert "usage" in body


def test_the_plan_endpoint_never_leaks_a_limit_without_its_provenance(client):
    for key, limit in client.get("/api/plan").get_json()["limits"].items():
        assert limit["source"] in ("plan", "env"), key
