"""Tests for the device preset system (src/multiprofile/devices/).

Parametrized over every registered preset. Runtime/browser-dependent checks
run only when a REAL Chromium binary exists on the host, where "real" is
established by one of two evidence paths: --headless --dump-dom emitting
real HTML, or a live DevTools protocol session with a genuine
Chromium/Chrome Browser version string. Otherwise they pytest.skip.
Nothing is ever faked.
"""
from __future__ import annotations

import pytest
from flask import Flask

from multiprofile.devices import (
    ANDROID,
    DESKTOP,
    IOS,
    MOBILE,
    PARTIAL,
    TABLET,
    DevicePresetValidator,
    find_chromium_binary,
    register_devices_routes,
)
from multiprofile.devices.preset import IPADOS, LINUX, MACOS, WINDOWS
from multiprofile.devices.registry import registry
from multiprofile.devices.validator import FAIL, NOT_RUN, PASS, PENDING

validator = DevicePresetValidator()

ALL_IDS = registry.ids()
assert len(ALL_IDS) >= 33

CHROMIUM_BIN, CHROMIUM_EVIDENCE = find_chromium_binary()
HAS_REAL_CHROMIUM = CHROMIUM_BIN is not None

MOBILE_CONFIG_KEYS = {
    "user_agent",
    "client_hints",
    "viewport",
    "device_scale_factor",
    "mobile",
    "touch",
    "max_touch_points",
    "hardware_concurrency",
    "device_memory_gb",
    "language",
    "timezone",
}
HINT_KEYS = {
    "brands",
    "full_version_list",
    "platform",
    "platform_version",
    "architecture",
    "model",
    "mobile",
}


#: Every test here depends on `validation_results`, which launches a real
#: Chromium for each preset, so the whole module needs a browser environment
#: -- not just the tests that assert on the runtime check.
#:
#: It was unmarked, so it ran in the `python` matrix jobs. Those have no
#: display, and a GitHub runner has Chrome preinstalled, so the validator
#: found a browser, launched it without an X server, and the viewport
#: round-trip came back scaled: "applied 411x918, page reports 980x2189". It
#: failed on one Python version and passed on the other two in the same run,
#: which is the worst way for a test to be wrong -- it reads as a flake and
#: gets re-run rather than read.
pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def validation_results():
    """Validate every preset once per test module (runtime check is cached)."""
    return {pid: validator.validate(registry.get(pid)) for pid in ALL_IDS}


def _check(results, pid, name):
    for c in results[pid]["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"check {name!r} missing for {pid}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_count_at_least_33():
    assert registry.count() >= 33


def test_registry_exact_category_counts():
    assert len(registry.list(category=MOBILE)) == 27  # 20 Android + 7 iOS-class
    assert len(registry.list(category=TABLET)) == 3
    assert len(registry.list(category=DESKTOP)) == 3
    assert registry.count() == 33


def test_registry_categories_present():
    cats = {p.category for p in registry.list()}
    assert {DESKTOP, MOBILE, TABLET} <= cats


def test_registry_platforms_include_android_and_ios_class():
    plats = {p.platform for p in registry.list()}
    assert ANDROID in plats
    assert IOS in plats
    assert IPADOS in plats
    assert WINDOWS in plats and LINUX in plats and MACOS in plats


def test_registry_get_unknown_returns_none():
    assert registry.get("no-such-device") is None


def test_registry_list_filters():
    android = registry.list(platform=ANDROID)
    assert len(android) == 21  # 20 phones + Galaxy Tab S9
    assert all(p.platform == ANDROID for p in android)
    ios = registry.list(platform=IOS)
    assert len(ios) == 7


def test_registry_ids_unique():
    assert len(set(ALL_IDS)) == len(ALL_IDS)


# ---------------------------------------------------------------------------
# Per-preset static checks (parametrized over the whole registry)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pid", ALL_IDS)
def test_schema_valid(pid, validation_results):
    assert _check(validation_results, pid, "schema")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_ua_nonempty_and_format_sane(pid, validation_results):
    p = registry.get(pid)
    assert p.user_agent and len(p.user_agent) > 40
    assert _check(validation_results, pid, "ua_format")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_client_hints_coherent(pid, validation_results):
    p = registry.get(pid)
    assert set(p.client_hints.keys()) == HINT_KEYS
    assert _check(validation_results, pid, "client_hints_coherence")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_viewport_positive(pid, validation_results):
    assert _check(validation_results, pid, "viewport_sane")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_mobile_flag_matches_category(pid, validation_results):
    assert _check(validation_results, pid, "mobile_flag_matches_category")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_touch_matches_category(pid, validation_results):
    p = registry.get(pid)
    if p.category in (MOBILE, TABLET):
        assert p.touch and p.max_touch_points > 0
    else:
        assert not p.touch
    assert _check(validation_results, pid, "touch_matches_category")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_dpr_in_range(pid, validation_results):
    p = registry.get(pid)
    assert 1.0 <= p.device_scale_factor <= 4.0
    assert _check(validation_results, pid, "dpr_sane")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_screen_matches_viewport_x_dpr(pid, validation_results):
    p = registry.get(pid)
    dpr = p.device_scale_factor
    assert abs(p.screen["width"] - p.viewport["width"] * dpr) <= 1
    assert abs(p.screen["height"] - p.viewport["height"] * dpr) <= 1
    assert _check(validation_results, pid, "screen_matches_viewport_x_dpr")["status"] == PASS


@pytest.mark.parametrize("pid", ALL_IDS)
def test_mobile_config_dict_exact_keys(pid):
    cfg = registry.get(pid).to_mobile_config_dict()
    assert set(cfg.keys()) == MOBILE_CONFIG_KEYS, f"{pid}: {sorted(cfg.keys())}"
    assert set(cfg["client_hints"].keys()) == HINT_KEYS
    assert set(cfg["viewport"].keys()) == {"width", "height"}


@pytest.mark.parametrize("pid", ALL_IDS)
def test_validate_honest_status(pid, validation_results):
    """Asserts the gate LOGIC, not a fixed status.

    - No real Chromium on host -> status PENDING, runtime_launch NOT_RUN
      with an honest "no Chromium binary on host" reason.
    - Real Chromium present -> runtime_launch must PASS via a genuine CDP
      round-trip (Emulation.setDeviceMetricsOverride applied to a live
      browser, geometry read back and matching), and the overall status
      must follow the validator's honesty rule:
        FAIL in checks                -> UNSUPPORTED
        NOT_RUN in checks             -> PENDING
        any PARTIAL/UNAVAILABLE cap   -> PARTIAL
        otherwise                     -> VALIDATED
    """
    result = validation_results[pid]
    rt = _check(validation_results, pid, "runtime_launch")
    statuses = [c["status"] for c in result["checks"]]
    if not HAS_REAL_CHROMIUM:
        assert result["status"] == PENDING, f"{pid}: expected PENDING without Chromium"
        assert NOT_RUN in statuses, f"{pid}: expected a NOT_RUN check without Chromium"
        assert rt["status"] == NOT_RUN
        assert "no Chromium binary on host" in rt["detail"]
    else:
        assert rt["status"] == PASS, (
            f"{pid}: real Chromium present but launch check failed: {rt['detail']}"
        )
        p = registry.get(pid)
        if FAIL in statuses:
            expected = "UNSUPPORTED"
        elif NOT_RUN in statuses:
            expected = PENDING
        elif any(
            v.get("status") in (PARTIAL, "UNAVAILABLE")
            for v in p.capability_matrix.values()
        ):
            expected = "PARTIAL"
        else:
            expected = "VALIDATED"
        assert result["status"] == expected, (
            f"{pid}: status {result['status']} != gate-logic expectation {expected}"
        )


# ---------------------------------------------------------------------------
# Honesty requirements
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pid", [p.id for p in registry.list(platform=IOS)] + [p.id for p in registry.list(platform=IPADOS)])
def test_ios_class_engine_is_partial_with_webkit_note(pid):
    p = registry.get(pid)
    assert p.platform in (IOS, IPADOS)
    assert p.platform != "iOS"  # never bare "iOS"
    engine = p.capability_matrix["engine"]
    assert engine["status"] == PARTIAL
    assert "WebKit" in engine["note"] and "Chromium" in engine["note"]


def test_macos_class_partial_with_documented_note():
    p = registry.get("macos-chrome")
    partials = [v for v in p.capability_matrix.values() if v["status"] == PARTIAL]
    assert partials, "macOS-class preset must document PARTIAL capabilities"
    assert any("macOS GPU/media characteristics not reproducible on Linux" in v["note"] for v in partials)


@pytest.mark.parametrize("pid", ALL_IDS)
def test_webgl_policy_always_real(pid):
    assert registry.get(pid).webgl_policy == "real"


@pytest.mark.parametrize("pid", ALL_IDS)
def test_capability_statuses_honest(pid):
    p = registry.get(pid)
    for cap, v in p.capability_matrix.items():
        assert v["status"] in ("SUPPORTED", "PARTIAL", "UNAVAILABLE"), f"{pid}/{cap}"
        assert v["note"], f"{pid}/{cap} needs a documented reason"
    assert p.limitations, f"{pid} must document limitations"


# ---------------------------------------------------------------------------
# Routes (throwaway Flask app; coordinator wires the real app later)
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    app = Flask(__name__)
    register_devices_routes(app)
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_devices_list_contract(client):
    resp = client.get("/api/devices")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["devices"]) == registry.count()
    expected_keys = {
        "id", "name", "manufacturer", "model", "category", "platform",
        "viewport", "device_scale_factor", "touch", "mobile",
        "support", "validation_date",
    }
    for d in body["devices"]:
        assert set(d.keys()) == expected_keys, f"{d.get('id')}: {sorted(d.keys())}"
        assert set(d["viewport"].keys()) == {"width", "height"}
        assert d["support"] in ("VALIDATED", "PARTIAL", "PENDING", "UNSUPPORTED")
        assert d["validation_date"]


def test_devices_list_support_mirrors_validator(client, validation_results):
    body = client.get("/api/devices").get_json()
    by_id = {d["id"]: d for d in body["devices"]}
    for pid, result in validation_results.items():
        assert by_id[pid]["support"] == result["status"]


def test_device_detail_contract(client):
    pid = "pixel-9"
    resp = client.get(f"/api/devices/{pid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == pid
    assert "capability_matrix" in body and isinstance(body["capability_matrix"], dict)
    assert "limitations" in body and isinstance(body["limitations"], list) and body["limitations"]
    assert body["user_agent"]


def test_device_detail_404(client):
    resp = client.get("/api/devices/no-such-device")
    assert resp.status_code == 404


def test_device_validate_contract(client):
    pid = "iphone-16-pro"
    resp = client.get(f"/api/devices/{pid}/validate")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["device_id"] == pid
    assert body["status"] in ("VALIDATED", "PARTIAL", "PENDING", "UNSUPPORTED")
    assert body["validated_at"]
    assert isinstance(body["checks"], list) and len(body["checks"]) >= 10
    names = {c["name"] for c in body["checks"]}
    assert {"schema", "ua_format", "client_hints_coherence", "viewport_sane",
            "mobile_flag_matches_category", "touch_matches_category", "dpr_sane",
            "screen_matches_viewport_x_dpr", "browser_compat", "runtime_launch",
            "fingerprint_consistency"} <= names


def test_device_validate_404(client):
    resp = client.get("/api/devices/no-such-device/validate")
    assert resp.status_code == 404


def test_chromium_reality_report():
    """Documents whether runtime validation could actually run."""
    if not HAS_REAL_CHROMIUM:
        pytest.skip(f"no real Chromium binary on host ({CHROMIUM_EVIDENCE})")
    # Evidence must name the proof path that was used (either gate is fine).
    assert ("real HTML" in CHROMIUM_EVIDENCE) or (
        "DevTools protocol" in CHROMIUM_EVIDENCE
    ), f"binary evidence must name the proof path: {CHROMIUM_EVIDENCE}"
