"""Tests for Worker 3 — ProfileConsistencyEngine. Pure logic: no network, no browser."""
from __future__ import annotations

from multiprofile.consistency import ProfileConsistencyEngine
from multiprofile.fingerprint import build_fingerprint

ENGINE = ProfileConsistencyEngine()

ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _android_profile(**kw):
    p = {
        "id": "p_android",
        "userAgent": ANDROID_UA,
        "isMobile": True,
        "timezone": "America/New_York",
        "language": "en-US",
    }
    p.update(kw)
    return p


def _device_for(fp, *, mobile=True, touch=True):
    screen = (fp or {}).get("screen") or {}
    return {
        "viewport": {"width": screen.get("w"), "height": screen.get("h")},
        "device_scale_factor": screen.get("dpr"),
        "touch": touch,
        "mobile": mobile,
    }


def _check(result, domain):
    for c in result["checks"]:
        if c["domain"] == domain:
            return c
    raise AssertionError(f"missing domain {domain}")


# ---------------------------------------------------------------- PASS case
def test_coherent_us_android_passes():
    profile = _android_profile()
    fp = build_fingerprint(profile)
    preset = _device_for(fp, mobile=True, touch=True)
    result = ENGINE.check(
        profile,
        device_preset=preset,
        diagnostics={"country_code": "US", "country": "United States"},
        fingerprint_expected=fp,
    )
    assert result["profile_id"] == "p_android"
    assert result["status"] == "PASS", result
    for c in result["checks"]:
        assert c["status"] == "PASS", c


# ------------------------------------------------------------ WARNING cases
def test_germany_ip_vs_new_york_timezone_warns():
    profile = _android_profile()
    fp = build_fingerprint(profile)
    preset = _device_for(fp)
    result = ENGINE.check(
        profile,
        device_preset=preset,
        diagnostics={"country_code": "DE", "country": "Germany"},
        fingerprint_expected=fp,
    )
    geo = _check(result, "geo")
    assert geo["status"] == "WARNING"
    assert "DE" in geo["message"] or "Germany" in geo["message"]
    assert result["status"] == "WARNING"


def test_missing_data_warns_not_errors():
    profile = _android_profile()
    result = ENGINE.check(profile)  # no diagnostics, no fingerprint, no preset
    assert result["status"] == "WARNING"
    geo = _check(result, "geo")
    assert geo["status"] == "WARNING"
    assert "insufficient data" in geo["message"]
    device = _check(result, "device")
    assert device["status"] == "WARNING"
    assert "device preset unavailable" in device["message"]
    browser = _check(result, "browser")
    assert browser["status"] == "WARNING"
    gpu = _check(result, "gpu_webgl")
    assert gpu["status"] == "WARNING"


def test_language_region_conflict_warns():
    profile = _android_profile(language="de-DE", timezone="Europe/Berlin")
    fp = build_fingerprint(profile)
    result = ENGINE.check(
        profile,
        device_preset=_device_for(fp),
        diagnostics={"country_code": "US"},
        fingerprint_expected=fp,
    )
    geo = _check(result, "geo")
    assert geo["status"] == "WARNING"


# -------------------------------------------------------------- ERROR cases
def test_desktop_ua_claiming_android_ch_platform_errors():
    profile = {
        "id": "p_win",
        "userAgent": WIN_UA,
        "isMobile": False,
        "timezone": "America/New_York",
        "language": "en-US",
    }
    fp = build_fingerprint(profile)
    fp["ch_platform"] = "Android"  # forged: desktop UA + Android client hints
    fp["clientHints"] = {"platform": "Android", "brands": fp.get("ch_brands")}
    result = ENGINE.check(
        profile,
        device_preset=None,
        diagnostics={"country_code": "US"},
        fingerprint_expected=fp,
    )
    plat = _check(result, "platform")
    assert plat["status"] == "ERROR"
    assert "Windows" in plat["message"] and "Android" in plat["message"]
    assert result["status"] == "ERROR"


def test_mobile_touch_mismatch_detected():
    profile = _android_profile()
    fp = build_fingerprint(profile)
    # Runtime contradicts the mobile claim: no touch points, touch disabled.
    fp["touch"] = 0
    fp["isMobile"] = True
    preset = _device_for(fp, mobile=True, touch=False)
    result = ENGINE.check(
        profile,
        device_preset=preset,
        diagnostics={"country_code": "US"},
        fingerprint_expected=fp,
    )
    device = _check(result, "device")
    assert device["status"] == "WARNING"
    assert "touch" in device["message"].lower()
    mobile = _check(result, "mobile")
    assert mobile["status"] == "ERROR"
    assert "touch" in mobile["message"].lower()
    assert result["status"] == "ERROR"


def test_mobile_viewport_range_violation():
    profile = _android_profile()
    fp = build_fingerprint(profile)
    preset = {
        "viewport": {"width": 1920, "height": 1080},  # desktop viewport, mobile device
        "device_scale_factor": 1,
        "touch": True,
        "mobile": True,
    }
    result = ENGINE.check(
        profile,
        device_preset=preset,
        diagnostics={"country_code": "US"},
        fingerprint_expected=fp,
    )
    mobile = _check(result, "mobile")
    assert mobile["status"] == "ERROR"
    assert "1920x1080" in mobile["message"]


def test_browser_brand_mismatch_warns():
    profile = _android_profile()
    fp = build_fingerprint(profile)
    fp["ch_brands"] = [{"brand": "Mozilla Firefox", "version": "131"}]
    fp["clientHints"] = {"platform": "Android", "brands": fp["ch_brands"]}
    result = ENGINE.check(
        profile,
        device_preset=_device_for(fp),
        diagnostics={"country_code": "US"},
        fingerprint_expected=fp,
    )
    browser = _check(result, "browser")
    assert browser["status"] == "WARNING"
    assert "Chrome" in browser["message"]
    # Not a hard failure: overall stays WARNING, not ERROR
    assert result["status"] == "WARNING"


def test_desktop_viewport_on_mobile_flag_errors_overall():
    profile = _android_profile(isMobile=False)  # desktop claim, Android UA
    fp = build_fingerprint(profile)
    result = ENGINE.check(
        profile,
        device_preset=_device_for(fp, mobile=True, touch=True),
        diagnostics={"country_code": "US"},
        fingerprint_expected=fp,
    )
    mobile = _check(result, "mobile")
    # desktop-claimed profile on a mobile viewport + touch: flagged
    assert mobile["status"] in ("WARNING", "ERROR")


# ------------------------------------------------------------ result shape
def test_result_shape():
    profile = _android_profile()
    result = ENGINE.check(profile)
    assert set(result.keys()) == {"profile_id", "status", "checks"}
    assert result["status"] in ("PASS", "WARNING", "ERROR")
    domains = [c["domain"] for c in result["checks"]]
    assert domains == ["geo", "device", "platform", "mobile", "browser", "gpu_webgl"]
    for c in result["checks"]:
        assert set(c.keys()) == {"domain", "status", "expected", "actual", "message"}
        assert c["status"] in ("PASS", "WARNING", "ERROR")
        assert isinstance(c["message"], str) and c["message"]
