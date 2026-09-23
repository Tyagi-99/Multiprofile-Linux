"""Tests for the fingerprint component package.

Covers: determinism, restart stability, per-profile isolation (no shared
mutable state), JS/HTTP coherence (navigator vs Sec-CH-UA* vs
userAgentMetadata), clone-gets-new-seed, canvas stability, WebGL honesty
(no fabricated vendor/renderer), MobileProfileConfig CDP plan contract,
from_preset_dict round-trip, inspector PASS/FAIL/UNKNOWN semantics, and
the upgraded fingerprint_report format.
"""
from __future__ import annotations

import dataclasses

import pytest

from multiprofile import fingerprint as fp_pkg
from multiprofile.fingerprint import (
    MobileProfileConfig,
    apply_cdp_plan,
    build_fingerprint,
    fingerprint_inspector_data,
    fingerprint_report,
    normalize_seed,
)
from multiprofile.fingerprint import (
    audio,
    battery,
    canvas,
    client_hints,
    fonts,
    hardware,
    media,
    navigator,
    network_info,
    plugins,
    screen,
    touch,
    webgl,
)

UA_WIN = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.0.0 "
    "Mobile/15E148 Safari/604.1"
)

COMPONENT_MODULES = [
    navigator, screen, client_hints, canvas, webgl, audio, fonts,
    media, plugins, network_info, battery, touch, hardware,
]

PROFILES = {
    "win": {"id": "p-win", "seed": 101, "userAgent": UA_WIN},
    "android": {"id": "p-android", "seed": 202, "userAgent": UA_ANDROID, "isMobile": True},
    "iphone": {"id": "p-iphone", "seed": 303, "userAgent": UA_IPHONE, "isMobile": True},
}


# ---------------------------------------------------------------------------
# determinism / restart stability / isolation
# ---------------------------------------------------------------------------

class TestDeterminismAndIsolation:
    @pytest.mark.parametrize("mod", COMPONENT_MODULES, ids=lambda m: m.__name__.split(".")[-1])
    def test_same_seed_identical_output(self, mod):
        p = dict(PROFILES["win"])
        a = mod.build(p["seed"], p)
        b = mod.build(p["seed"], p)
        assert a == b

    def test_restart_stability_rebuild_from_persisted_seed(self):
        # Simulate restart: profile reloaded with the same persisted seed.
        persisted = {"id": "p-win", "seed": 101, "userAgent": UA_WIN}
        before = {m.__name__: m.build(persisted["seed"], persisted) for m in COMPONENT_MODULES}
        reloaded = dict(persisted)  # "fresh" dict, same seed
        after = {m.__name__: m.build(reloaded["seed"], reloaded) for m in COMPONENT_MODULES}
        assert before == after
        fp1 = build_fingerprint(persisted)
        fp2 = build_fingerprint(reloaded)
        assert fp1 == fp2

    def test_different_seeds_different_canvas_and_media(self):
        pa = {"id": "a", "seed": 1111, "userAgent": UA_WIN}
        pb = {"id": "b", "seed": 2222, "userAgent": UA_WIN}
        assert canvas.build(1111, pa)["noiseSalt"] != canvas.build(2222, pb)["noiseSalt"]
        assert media.build(1111, pa)["deviceIds"] != media.build(2222, pb)["deviceIds"]

    def test_canvas_stable_across_rebuilds(self):
        p = {"id": "c", "seed": 4242, "userAgent": UA_WIN}
        first = canvas.build(4242, p)
        for _ in range(3):
            assert canvas.build(4242, p) == first
        assert first["mode"] == "per-profile-stable"
        assert "not a hardware claim" in first["note"]

    def test_no_shared_mutable_state_between_profiles(self):
        pa = {"id": "a", "seed": 1111, "userAgent": UA_WIN}
        pb = {"id": "b", "seed": 2222, "userAgent": UA_ANDROID, "isMobile": True}
        ra = {m.__name__: m.build(1111, pa) for m in COMPONENT_MODULES}
        rb = {m.__name__: m.build(2222, pb) for m in COMPONENT_MODULES}
        # Mutate one profile's results deeply; the other's rebuild is unaffected.
        ra["multiprofile.fingerprint.plugins"]["plugins"].append({"name": "MUTATED"})
        ra["multiprofile.fingerprint.client_hints"]["brands"].clear()
        rb2 = {m.__name__: m.build(2222, pb) for m in COMPONENT_MODULES}
        assert rb == rb2
        # profiles themselves are not mutated by builds
        assert "seed" in pa and pa["seed"] == 1111

    def test_clone_gets_new_seed_new_fingerprint(self):
        original = {"id": "orig", "seed": 5555, "userAgent": UA_WIN}
        clone = dict(original)
        clone["id"] = "clone"
        clone["seed"] = 99999  # clones always receive a fresh seed
        assert build_fingerprint(original) != build_fingerprint(clone)
        assert media.build(5555, original)["deviceIds"] != media.build(99999, clone)["deviceIds"]
        assert canvas.build(5555, original)["noiseSalt"] != canvas.build(99999, clone)["noiseSalt"]

    def test_normalize_seed_still_public(self):
        assert normalize_seed({"seed": 42}) == 42
        assert normalize_seed({"id": "x"}) == normalize_seed({"id": "x"})


# ---------------------------------------------------------------------------
# JS / HTTP coherence (desktop + android + ios-class)
# ---------------------------------------------------------------------------

class TestCoherence:
    @pytest.mark.parametrize("kind,expected_platform,expected_ch_platform", [
        ("win", "Win32", "Windows"),
        ("android", "Linux armv8l", "Android"),
        ("iphone", "iPhone", "iOS"),
    ])
    def test_navigator_client_hints_http_coherence(self, kind, expected_platform, expected_ch_platform):
        p = dict(PROFILES[kind])
        seed = p["seed"]
        nav = navigator.build(seed, p)
        ch = client_hints.build(seed, p)

        # navigator agrees with the pool
        assert nav["platform"] == expected_platform
        assert nav["userAgent"] == p["userAgent"]

        # client hints do not contradict navigator
        assert ch["platform"] == expected_ch_platform
        is_mobile = nav["maxTouchPoints"] > 0
        assert ch["mobile"] is is_mobile
        assert ch["userAgentMetadata"]["mobile"] is is_mobile
        assert ch["userAgentMetadata"]["platform"] == expected_ch_platform

        # HTTP headers agree with the JS surface
        headers = ch["secChUaHeaders"]
        assert headers["Sec-CH-UA-Mobile"] == ("?1" if is_mobile else "?0")
        assert headers["Sec-CH-UA-Platform"] == f'"{expected_ch_platform}"'
        assert headers["Sec-CH-UA-Platform-Version"] == f'"{ch["platformVersion"]}"'

        # brands carry the same Chrome major as the UA (or the 131 fallback)
        import re
        m = re.search(r"Chrome/(\d+)", p["userAgent"])
        major = m.group(1) if m else "131"
        assert ch["brands"][0]["brand"] == "Google Chrome"
        assert ch["brands"][0]["version"] == major
        assert ch["userAgentMetadata"]["brands"][0]["version"] == major
        # Sec-CH-UA header renders the same brands
        assert f'"Google Chrome";v="{major}"' in headers["Sec-CH-UA"]

    def test_ios_device_memory_not_exposed(self):
        p = dict(PROFILES["iphone"])
        assert navigator.build(p["seed"], p)["deviceMemory"] is None
        assert hardware.build(p["seed"], p)["deviceMemory"] is None

    def test_desktop_touch_zero_mobile_touch_nonzero(self):
        assert touch.build(101, PROFILES["win"])["maxTouchPoints"] == 0
        assert touch.build(101, PROFILES["win"])["touchEvents"] is False
        assert touch.build(202, PROFILES["android"])["maxTouchPoints"] > 0
        assert touch.build(202, PROFILES["android"])["touchEvents"] is True

    def test_device_preset_is_single_coherent_source(self):
        p = {"id": "x", "seed": 77, "userAgent": UA_WIN}
        preset = {"pool": "android"}
        nav = navigator.build(77, p, preset)
        ch = client_hints.build(77, p, preset)
        scr = screen.build(77, p, preset)
        assert nav["platform"] == "Linux armv8l"
        assert ch["platform"] == "Android"
        assert ch["mobile"] is True
        assert scr["orientation"]["type"] == "portrait-primary"

    def test_hardware_matches_navigator(self):
        p = dict(PROFILES["win"])
        nav = navigator.build(p["seed"], p)
        hw = hardware.build(p["seed"], p)
        assert hw["hardwareConcurrency"] == nav["hardwareConcurrency"]
        assert hw["deviceMemory"] == nav["deviceMemory"]
        assert hw["platform"] == nav["platform"]


# ---------------------------------------------------------------------------
# WebGL honesty
# ---------------------------------------------------------------------------

class TestWebglHonesty:
    def test_expected_contains_no_vendor_renderer(self):
        out = webgl.build(101, PROFILES["win"])
        expected = out["expected"]
        # no vendor/renderer keys and no fabricated vendor-like strings
        assert "vendor" not in expected and "renderer" not in expected
        blob = repr(expected)
        assert "Google Inc." not in blob
        assert "ANGLE" not in blob
        assert out["runtime"] is None

    def test_expected_states_policy_only(self):
        out = webgl.build(101, PROFILES["win"])
        assert "never pass --disable-gpu" in out["expected"]["policy"]

    def test_runtime_only_from_real_probe(self):
        probed = webgl.runtime_from_probe({
            "status": "REAL_GPU",
            "vendor": "Google Inc. (X)",
            "renderer": "ANGLE (X)",
            "version": "WebGL 2.0",
        })
        assert probed["vendor"] == "Google Inc. (X)"
        assert probed["renderer"] == "ANGLE (X)"


# ---------------------------------------------------------------------------
# PARTIAL surfaces
# ---------------------------------------------------------------------------

class TestPartialSurfaces:
    def test_audio_is_partial_with_reason(self):
        out = audio.build(101, PROFILES["win"])
        assert out["status"] == "PARTIAL"
        assert "driver" in out["detail"]

    def test_battery_is_partial_with_reason(self):
        out = battery.build(101, PROFILES["win"])
        assert out["status"] == "PARTIAL"
        assert "deprecated" in out["detail"]
        assert out["expected"]["policy"].startswith("surface host")

    def test_plugins_keep_desktop_chrome_set(self):
        desktop = plugins.build(101, PROFILES["win"])
        assert [p["name"] for p in desktop["plugins"]] == [
            "PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
        ]
        mobile_plg = plugins.build(202, PROFILES["android"])
        assert mobile_plg["plugins"] == []


# ---------------------------------------------------------------------------
# MobileProfileConfig
# ---------------------------------------------------------------------------

PIXEL7_PRESET = {
    "user_agent": UA_ANDROID,
    "client_hints": {
        "brands": [
            {"brand": "Google Chrome", "version": "131"},
            {"brand": "Chromium", "version": "131"},
            {"brand": "Not-A.Brand", "version": "99"},
        ],
        "full_version_list": [
            {"brand": "Google Chrome", "version": "131.0.0.0"},
            {"brand": "Chromium", "version": "131.0.0.0"},
            {"brand": "Not-A.Brand", "version": "99.0.0.0"},
        ],
        "platform": "Android",
        "platform_version": "13.0.0",
        "architecture": "",
        "model": "Pixel 7",
        "mobile": True,
    },
    "viewport": {"width": 412, "height": 915},
    "device_scale_factor": 2.625,
    "mobile": True,
    "touch": True,
    "max_touch_points": 5,
    "hardware_concurrency": 8,
    "device_memory_gb": 8.0,
    "language": "en-US",
    "timezone": "America/New_York",
}


class TestMobileProfileConfig:
    def test_exact_dataclass_fields(self):
        names = [f.name for f in dataclasses.fields(MobileProfileConfig)]
        assert names == [
            "user_agent", "ch_brands", "ch_full_version_list", "ch_platform",
            "ch_platform_version", "ch_architecture", "ch_model", "ch_mobile",
            "viewport_width", "viewport_height", "device_scale_factor",
            "mobile", "touch_enabled", "max_touch_points",
            "hardware_concurrency", "device_memory_gb", "locale", "timezone",
        ]

    def test_from_preset_dict_round_trip(self):
        cfg = MobileProfileConfig.from_preset_dict(PIXEL7_PRESET)
        assert cfg.user_agent == PIXEL7_PRESET["user_agent"]
        assert cfg.ch_platform == "Android"
        assert cfg.ch_platform_version == "13.0.0"
        assert cfg.ch_model == "Pixel 7"
        assert cfg.ch_mobile is True
        assert cfg.viewport_width == 412 and cfg.viewport_height == 915
        assert cfg.device_scale_factor == 2.625
        assert cfg.mobile is True and cfg.touch_enabled is True
        assert cfg.max_touch_points == 5
        assert cfg.hardware_concurrency == 8
        assert cfg.device_memory_gb == 8.0
        assert cfg.locale == "en-US"
        assert cfg.timezone == "America/New_York"
        assert len(cfg.ch_brands) == 3 and len(cfg.ch_full_version_list) == 3

    def test_to_cdp_plan_emits_exact_three_methods(self):
        cfg = MobileProfileConfig.from_preset_dict(PIXEL7_PRESET)
        plan = cfg.to_cdp_plan()
        assert [s["method"] for s in plan] == [
            "Emulation.setDeviceMetricsOverride",
            "Emulation.setTouchEmulationEnabled",
            "Network.setUserAgentOverride",
        ]
        # coherent params across the three commands
        metrics, touch_cmd, ua_cmd = (s["params"] for s in plan)
        assert metrics == {"width": 412, "height": 915,
                           "deviceScaleFactor": 2.625, "mobile": True}
        assert touch_cmd == {"enabled": True}
        assert ua_cmd["userAgent"] == UA_ANDROID
        meta = ua_cmd["userAgentMetadata"]
        assert meta["platform"] == "Android"
        assert meta["platformVersion"] == "13.0.0"
        assert meta["model"] == "Pixel 7"
        assert meta["mobile"] is True
        assert meta["brands"][0] == {"brand": "Google Chrome", "version": "131"}
        assert meta["fullVersionList"][0]["version"] == "131.0.0.0"

    def test_apply_cdp_plan_records_results(self):
        calls = []

        def fake_send(method, params):
            calls.append((method, params))
            return {"applied": True}

        cfg = MobileProfileConfig.from_preset_dict(PIXEL7_PRESET)
        results = apply_cdp_plan(fake_send, cfg.to_cdp_plan())
        assert len(calls) == 3
        assert all(r["ok"] for r in results)
        assert [r["method"] for r in results] == [c[0] for c in calls]

    def test_apply_cdp_plan_records_failure_without_raising(self):
        def bad_send(method, params):
            raise RuntimeError("cdp down")

        results = apply_cdp_plan(bad_send, [{"method": "X", "params": {}}])
        assert results[0]["ok"] is False
        assert "cdp down" in results[0]["error"]


# ---------------------------------------------------------------------------
# inspector
# ---------------------------------------------------------------------------

class TestInspector:
    EXPECTED_NAMES = [
        "Navigator", "Screen", "Client Hints", "Canvas", "WebGL", "Audio",
        "Media", "Fonts", "Plugins", "Network", "Timezone", "Language",
        "Hardware", "Touch", "Battery",
    ]

    def test_no_runtime_all_unknown_never_invented(self):
        data = fingerprint_inspector_data({"id": "p1", "seed": 101, "userAgent": UA_WIN})
        assert data["profile_id"] == "p1"
        assert [c["name"] for c in data["components"]] == self.EXPECTED_NAMES
        for comp in data["components"]:
            assert set(comp) == {"name", "expected", "runtime", "status", "note"}
            assert comp["runtime"] is None
            assert comp["status"] == "UNKNOWN"
            assert "live CDP session" in comp["note"]
            # expected values exist even when nothing is running
            assert comp["expected"]

    def test_matching_runtime_passes(self):
        profile = {"id": "p1", "seed": 101, "userAgent": UA_WIN}
        insp = fingerprint_inspector_data(profile)
        runtime_data = {}
        key_map = {
            "Navigator": "navigator", "Screen": "screen", "Client Hints": "clientHints",
            "Canvas": "canvas", "Audio": "audio", "Media": "media",
            "Fonts": "fonts", "Plugins": "plugins", "Network": "network",
            "Timezone": "timezone", "Language": "language",
            "Hardware": "hardware", "Touch": "touch", "Battery": "battery",
        }
        for comp in insp["components"]:
            if comp["name"] in key_map:
                runtime_data[key_map[comp["name"]]] = comp["expected"]
        # WebGL runtime comes from a real probe, not from expected config
        runtime_data["webgl"] = webgl.runtime_from_probe({
            "status": "REAL_GPU", "vendor": "V", "renderer": "R",
        })
        data = fingerprint_inspector_data(profile, runtime_data)
        statuses = {c["name"]: c["status"] for c in data["components"]}
        assert all(s == "PASS" for s in statuses.values()), statuses

    def test_mismatching_runtime_fails_with_note(self):
        profile = {"id": "p1", "seed": 101, "userAgent": UA_WIN}
        runtime_data = {"navigator": {
            "userAgent": "WRONG", "platform": "Win32", "language": "en-US",
            "languages": ["en-US"], "hardwareConcurrency": 4,
            "deviceMemory": 8, "maxTouchPoints": 0,
        }}
        data = fingerprint_inspector_data(profile, runtime_data)
        nav = next(c for c in data["components"] if c["name"] == "Navigator")
        assert nav["status"] == "FAIL"
        assert "userAgent" in nav["note"]

    def test_webgl_pass_on_real_probe_unknown_without(self):
        profile = {"id": "p1", "seed": 101, "userAgent": UA_WIN}
        data = fingerprint_inspector_data(profile, {
            "webgl": {"status": "SOFTWARE", "vendor": "Google Inc.",
                      "renderer": "ANGLE (Google, Vulkan 1.3.0 (SwiftShader))"},
        })
        w = next(c for c in data["components"] if c["name"] == "WebGL")
        assert w["status"] == "PASS"
        assert "SwiftShader" in w["note"]


# ---------------------------------------------------------------------------
# upgraded fingerprint_report
# ---------------------------------------------------------------------------

class TestFingerprintReport:
    def test_report_has_inspector_component_format(self):
        r = fingerprint_report({"id": "p1", "seed": 101, "userAgent": UA_WIN})
        assert "components" in r and "generated_at" in r
        for comp in r["components"]:
            assert set(comp) == {"name", "expected", "runtime", "status", "note"}
            assert comp["status"] in ("PASS", "FAIL", "UNKNOWN")

    def test_report_keeps_legacy_keys_for_api(self):
        r = fingerprint_report({"id": "p1", "seed": 101, "userAgent": UA_WIN})
        assert r["ok"] is True
        assert r["seed"] == 101
        assert "summary" in r and "Win32" in r["summary"]
        assert isinstance(r["warnings"], list)
        assert r["consistent"] is True
        assert "fingerprint" in r

    def test_report_warns_on_ua_platform_mismatch(self):
        r = fingerprint_report({"id": "p1", "seed": 101, "userAgent": UA_IPHONE})
        # pool is iPhone so UA/platform agree -> no warnings; force mismatch:
        r2 = fingerprint_report({
            "id": "p1", "seed": 101,
            "userAgent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)",
            "fingerprint": build_fingerprint({"id": "p1", "seed": 101, "userAgent": UA_WIN}),
        })
        assert r2["warnings"], "expected mismatch warnings for iPhone UA + Win32 platform"
        assert r2["consistent"] is False

    def test_package_reexports_legacy_names(self):
        assert fp_pkg.build_fingerprint is build_fingerprint
        assert fp_pkg.fingerprint_report is fingerprint_report
        assert fp_pkg.normalize_seed is normalize_seed
        assert hasattr(fp_pkg, "MobileProfileConfig")
