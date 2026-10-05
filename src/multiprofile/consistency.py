"""Profile consistency engine for Multiprofile Native.

Pure, deterministic logic — no browser, no network.  Given a profile, an
optional device preset, optional network diagnostics, and an optional expected
fingerprint, it cross-checks the signals a real browser would expose and
reports PASS / WARNING / ERROR per domain with human-readable messages.

Domains:
    geo       ip_country <-> timezone <-> language
    device    device preset <-> viewport <-> DPR <-> touch
    platform  os <-> user-agent <-> Client Hints platform
    mobile    mobile category <-> viewport <-> touch
    browser   browser family <-> Client Hints brands
    gpu_webgl expected webgl policy <-> runtime capability (when provided)
    webgpu    WebGL renderer <-> WebGPU adapter vendor

Each report also carries a **score** out of 100. The score exists so the
console can rank profiles and so an operator can see a profile getting worse
without reading six messages, but it is deliberately not a confidence rating
about being undetected: it says how internally coherent the signals *we can
see* are, and nothing about the surfaces nobody has checked yet.

Two rules keep it honest. A domain that could not be evaluated -- no proxy, so
no IP country to compare a timezone against -- is **excluded from the
denominator** rather than scored as a pass or a fail. Scoring it as a pass
would let a profile with nothing configured outrank one with a real setup, and
scoring it as a fail would punish an operator for a check they never asked
for. And any ERROR caps the total, because a single flat contradiction is not
something four passes elsewhere should be able to average away.
"""
from __future__ import annotations

import os
from typing import Any

PASS = "PASS"
WARNING = "WARNING"
ERROR = "ERROR"

#: Marker for a check that had nothing to compare. Messages already began with
#: this prefix; the score needs it as data rather than prose.
INSUFFICIENT_PREFIX = "insufficient data:"

#: How much each domain is worth. Geo and platform carry the most because they
#: are the contradictions a site checks first and the ones that are hardest to
#: explain away: an IP in one country with a timezone in another, or a
#: User-Agent that disagrees with its own Client Hints.
_DOMAIN_WEIGHTS: dict[str, int] = {
    "geo": 30,
    "platform": 25,
    "device": 15,
    "mobile": 10,
    "browser": 10,
    "gpu_webgl": 5,
    "webgpu": 5,
}

#: A profile with a flat contradiction cannot score above this, whatever else
#: passes.
_ERROR_CEILING = 40

#: A WARNING keeps this fraction of its domain's weight: it is a real signal,
#: not a pass, but it is not the same as a contradiction either.
_WARNING_CREDIT = 0.4


def _is_insufficient(check: dict[str, Any]) -> bool:
    if check.get("insufficient") is True:
        return True
    return str(check.get("message") or "").startswith(INSUFFICIENT_PREFIX)


def score_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn per-domain results into a 0-100 score and its explanation.

    Returns the score, the domains that counted, and the ones that were
    skipped for want of anything to compare -- so a low score can always be
    traced to a specific check rather than appearing as a bare number.
    """
    earned = 0.0
    possible = 0
    skipped: list[str] = []
    penalised: list[str] = []
    for check in checks:
        domain = str(check.get("domain") or "")
        weight = _DOMAIN_WEIGHTS.get(domain, 0)
        if not weight:
            continue
        if _is_insufficient(check):
            skipped.append(domain)
            continue
        possible += weight
        status = check.get("status")
        if status == PASS:
            earned += weight
        elif status == WARNING:
            earned += weight * _WARNING_CREDIT
            penalised.append(domain)
        else:
            penalised.append(domain)

    if not possible:
        # Nothing could be evaluated. A score of 100 would be a lie and a 0
        # would be a different one, so the report says so instead.
        return {"score": None, "evaluated": [], "skipped": skipped,
                "penalised": [], "reason": "nothing could be evaluated"}

    score = round(100 * earned / possible)
    if any(c.get("status") == ERROR and not _is_insufficient(c) for c in checks):
        score = min(score, _ERROR_CEILING)
    return {
        "score": score,
        "evaluated": [d for d in _DOMAIN_WEIGHTS if d not in skipped
                      and any(c.get("domain") == d for c in checks)],
        "skipped": skipped,
        "penalised": penalised,
        "reason": None,
    }

# ---------------------------------------------------------------------------
# IANA timezone -> ISO country code (common zones; unknown zones are reported
# as insufficient data rather than guessed).
# ---------------------------------------------------------------------------

TZ_COUNTRY: dict[str, str] = {
    # North America
    "America/New_York": "US", "America/Chicago": "US", "America/Denver": "US",
    "America/Los_Angeles": "US", "America/Anchorage": "US", "Pacific/Honolulu": "US",
    "America/Toronto": "CA", "America/Vancouver": "CA", "America/Montreal": "CA",
    "America/Mexico_City": "MX", "America/Bogota": "CO", "America/Sao_Paulo": "BR",
    "America/Buenos_Aires": "AR", "America/Santiago": "CL", "America/Lima": "PE",
    # Europe
    "Europe/London": "GB", "Europe/Dublin": "IE", "Europe/Lisbon": "PT",
    "Europe/Madrid": "ES", "Europe/Paris": "FR", "Europe/Berlin": "DE",
    "Europe/Rome": "IT", "Europe/Amsterdam": "NL", "Europe/Brussels": "BE",
    "Europe/Zurich": "CH", "Europe/Vienna": "AT", "Europe/Prague": "CZ",
    "Europe/Warsaw": "PL", "Europe/Stockholm": "SE", "Europe/Oslo": "NO",
    "Europe/Copenhagen": "DK", "Europe/Helsinki": "FI", "Europe/Athens": "GR",
    "Europe/Istanbul": "TR", "Europe/Moscow": "RU", "Europe/Kiev": "UA",
    "Europe/Bucharest": "RO", "Europe/Budapest": "HU",
    # Asia
    "Asia/Dubai": "AE", "Asia/Kolkata": "IN", "Asia/Karachi": "PK",
    "Asia/Dhaka": "BD", "Asia/Colombo": "LK", "Asia/Kathmandu": "NP",
    "Asia/Bangkok": "TH", "Asia/Ho_Chi_Minh": "VN",
    "Asia/Jakarta": "ID", "Asia/Singapore": "SG",
    "Asia/Kuala_Lumpur": "MY", "Asia/Manila": "PH", "Asia/Hong_Kong": "HK",
    "Asia/Shanghai": "CN", "Asia/Taipei": "TW", "Asia/Seoul": "KR",
    "Asia/Tokyo": "JP", "Asia/Jerusalem": "IL", "Asia/Riyadh": "SA",
    "Asia/Tehran": "IR",
    # Oceania
    "Australia/Sydney": "AU", "Australia/Melbourne": "AU", "Australia/Perth": "AU",
    "Pacific/Auckland": "NZ",
    # Africa
    "Africa/Cairo": "EG", "Africa/Lagos": "NG", "Africa/Nairobi": "KE",
    "Africa/Johannesburg": "ZA", "Africa/Casablanca": "MA",
}

# Mobile viewport envelope: (min_w, max_w, min_h, max_h)
_MOBILE_VIEWPORT = (300, 600, 560, 1400)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _ua_platform(ua: str) -> str | None:
    """Best-effort OS platform token from a user-agent string."""
    u = (ua or "").lower()
    if "android" in u:
        return "Android"
    if "iphone" in u or "ipad" in u:
        return "iOS"
    if "macintosh" in u or "mac os x" in u:
        return "macOS"
    if "windows nt" in u or "win64" in u or "wow64" in u:
        return "Windows"
    if "linux" in u:
        return "Linux"
    if "cros" in u:
        return "ChromeOS"
    return None


def _ua_browser_family(ua: str) -> str | None:
    u = ua or ""
    if "Edg/" in u or "Edge/" in u:
        return "Edge"
    if "OPR/" in u or "Opera" in u:
        return "Opera"
    if "Firefox/" in u:
        return "Firefox"
    if "Chrome/" in u or "Chromium/" in u:
        return "Chrome"
    if "Version/" in u and "Safari/" in u:
        return "Safari"
    return None


def _ch_of(fp: dict[str, Any] | None) -> dict[str, Any]:
    """Extract client-hints view from a fingerprint dict (both shapes)."""
    if not isinstance(fp, dict):
        return {}
    ch = fp.get("clientHints")
    if isinstance(ch, dict):
        return ch
    out: dict[str, Any] = {}
    for k in ("platform", "brands", "mobile", "architecture", "bitness",
              "platformVersion", "model", "uaFullVersion"):
        v = fp.get("ch_" + k) if k != "platform" else fp.get("ch_platform")
        if v is not None:
            out[k] = v
    return out


def _host_locale_language() -> str | None:
    """The language a real browser on this machine would send, or None.

    Used only on a DIRECT route, where the host is the exit and so the host's
    own locale is the honest reference. ``C`` and ``POSIX`` are not languages
    and produce no answer.
    """
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw = (os.environ.get(var) or "").strip()
        if not raw or raw.split(".")[0] in ("C", "POSIX", ""):
            continue
        tag = raw.split(".")[0].split("@")[0].replace("_", "-")
        if tag:
            return tag
    return None


def _diag_value(diagnostics: Any, key: str) -> Any:
    if diagnostics is None:
        return None
    if isinstance(diagnostics, dict):
        return diagnostics.get(key)
    return getattr(diagnostics, key, None)


def _norm_country_code(cc: Any) -> str | None:
    if not cc:
        return None
    s = str(cc).strip().upper()
    return s if len(s) == 2 else None


def _lang_region(language: Any) -> str | None:
    if not language:
        return None
    parts = str(language).replace("_", "-").split("-")
    if len(parts) >= 2 and len(parts[-1]) == 2:
        return parts[-1].upper()
    return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ProfileConsistencyEngine:
    """Cross-checks profile signals for internal coherence."""

    # ------------------------------------------------------------------ API
    def check(
        self,
        profile: dict[str, Any],
        *,
        device_preset: dict[str, Any] | None = None,
        diagnostics: Any = None,
        fingerprint_expected: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = profile or {}
        fp = fingerprint_expected if isinstance(fingerprint_expected, dict) else None

        checks = [
            self._check_geo(profile, diagnostics),
            self._check_device(profile, device_preset, fp),
            self._check_platform(profile, fp),
            self._check_mobile(profile, device_preset, fp),
            self._check_browser(profile, fp),
            self._check_gpu_webgl(profile, fp),
            self._check_webgpu(fp),
        ]
        status = PASS
        for c in checks:
            if c["status"] == ERROR:
                status = ERROR
                break
        if status != ERROR:
            for c in checks:
                if c["status"] == WARNING:
                    status = WARNING
                    break
        scored = score_checks(checks)
        return {
            "profile_id": profile.get("id"),
            "status": status,
            "score": scored["score"],
            "score_detail": scored,
            "checks": checks,
        }

    # --------------------------------------------------------------- domains
    def _check_geo(self, profile: dict[str, Any], diagnostics: Any) -> dict[str, Any]:
        domain = "geo"
        cc = _norm_country_code(_diag_value(diagnostics, "country_code"))
        tz = (profile.get("timezone") or "").strip()
        tz_cc = TZ_COUNTRY.get(tz)
        lang_cc = _lang_region(profile.get("language"))

        comparators = [v for v in (tz_cc, lang_cc) if v]
        if cc is None:
            # "No proxy" is not "nothing knowable". On a DIRECT route the host
            # is the exit, so there is a country to compare against after all,
            # and excluding the domain let a no-proxy profile announce New
            # York from this machine's address with a clean score (F43).
            direct = self._check_geo_direct(profile)
            if direct is not None:
                return direct
            return {
                "domain": domain, "status": WARNING,
                "expected": "IP geolocation to compare against",
                "actual": {"country_code": None, "timezone": tz or None,
                           "timezone_country": tz_cc, "language": profile.get("language"),
                           "language_country": lang_cc},
                "message": "insufficient data: no IP geolocation (country_code) to "
                           "compare timezone/language against",
            }
        if not comparators:
            return {
                "domain": domain, "status": WARNING,
                "expected": "a timezone or language region to compare against",
                "actual": {"country_code": cc, "timezone": tz or None,
                           "language": profile.get("language")},
                "message": "insufficient data: IP country known but no timezone or "
                           "language region to compare against",
            }
        known = [cc] + comparators
        distinct = set(known)
        if len(distinct) > 1:
            return {
                "domain": domain, "status": WARNING,
                "expected": "single coherent country",
                "actual": {"country_code": cc, "timezone": tz or None,
                           "timezone_country": tz_cc, "language": profile.get("language"),
                           "language_country": lang_cc},
                "message": (
                    f"geolocation conflict: IP country {cc or 'unknown'} vs "
                    f"timezone {tz or 'unset'} ({tz_cc or 'unknown'}) vs "
                    f"language region {lang_cc or 'unknown'}. "
                    "A mismatched IP/timezone pair is a common detection signal."
                ),
            }
        return {
            "domain": domain, "status": PASS,
            "expected": distinct.pop(),
            "actual": {"country_code": cc, "timezone": tz or None,
                       "timezone_country": tz_cc, "language": profile.get("language"),
                       "language_country": lang_cc},
            "message": "IP country, timezone and language region are coherent",
        }

    def _check_geo_direct(self, profile: dict[str, Any]) -> dict[str, Any] | None:
        """Score the geo domain for a profile with no proxy, or return None.

        Returns None -- leaving the caller to report insufficient data -- when
        the route is not DIRECT, when this host's own zone cannot be resolved,
        or when the launch would claim no timezone at all. Those are the cases
        where there genuinely is nothing to compare.

        What is compared is the **effective** identity, taken from the same
        :func:`geo.align_to_exit` the launch uses, not the stored fields. A
        profile still carrying the creation default of ``America/New_York``
        with ``geoAuto`` set will claim the host's zone when it launches, and
        scoring the stored value would report a conflict that the launch
        resolves -- a false alarm teaches an operator to ignore the score.

        The language is compared against the **host's locale** rather than
        against the country's majority language. ``en-US`` on an Indian
        connection is what a great many real browsers there send; calling it a
        conflict would be noise. Claiming ``pt-BR`` from an ``en-US`` host is
        a different matter, and that is what this catches.
        """
        try:
            from .geo import align_to_exit, host_timezone
            from .network import NetworkMode, network_config_for_profile

            if network_config_for_profile(profile).mode != NetworkMode.DIRECT:
                return None
        except Exception:  # noqa: BLE001 - the score must never break a page load
            return None

        host_cc = TZ_COUNTRY.get(host_timezone() or "")
        if not host_cc:
            return None

        plan = align_to_exit(profile, None, None, direct=True)
        eff_tz = (plan.get("timezone") or "").strip()
        eff_tz_cc = TZ_COUNTRY.get(eff_tz)
        if not eff_tz:
            return None

        eff_lang = plan.get("language") or profile.get("language") or None
        host_lang = _host_locale_language()

        actual = {
            "country_code": host_cc, "timezone": eff_tz,
            "timezone_country": eff_tz_cc, "language": eff_lang,
            "language_country": _lang_region(eff_lang),
            "source": "host (no proxy: this machine is the exit)",
        }

        conflicts: list[str] = []
        if eff_tz_cc and eff_tz_cc != host_cc:
            conflicts.append(
                f"timezone {eff_tz} ({eff_tz_cc}) but with no proxy the traffic "
                f"leaves from this machine, in {host_cc}"
            )
        elif not eff_tz_cc:
            conflicts.append(
                f"timezone {eff_tz} is not a zone this engine can place, so it "
                f"cannot be checked against the host country {host_cc}"
            )
        if eff_lang and host_lang:
            if eff_lang.split("-")[0].lower() != host_lang.split("-")[0].lower():
                conflicts.append(
                    f"language {eff_lang} but this host's locale is {host_lang}"
                )

        if conflicts:
            # The stock warning is about the IP/timezone pair, so it only
            # belongs here when the timezone is what disagrees. Appending it
            # to a language-only conflict would describe the wrong problem.
            tail = (
                " A mismatched IP/timezone pair is a common detection signal."
                if eff_tz_cc != host_cc else ""
            )
            return {
                "domain": "geo", "status": WARNING,
                "expected": f"claims coherent with a direct connection from {host_cc}",
                "actual": actual,
                "message": (
                    "no proxy, so the exit IP is this machine's: "
                    + "; ".join(conflicts) + "." + tail
                ),
            }
        return {
            "domain": "geo", "status": PASS,
            "expected": host_cc,
            "actual": actual,
            "message": (
                f"no proxy: timezone {eff_tz} and language {eff_lang or 'unset'} "
                f"agree with a direct connection from {host_cc}"
            ),
        }

    def _check_webgpu(self, fp: dict[str, Any] | None) -> dict[str, Any]:
        """WebGL and WebGPU must name the same card.

        A page can read both, and they use different vocabularies for the same
        hardware. Measured before F26, a profile claimed an RTX 4060 in WebGL
        while ``requestAdapter()`` returned null -- a combination no Windows
        machine produces. This catches the same class of disagreement before
        a launch rather than after.
        """
        domain = "webgpu"
        gpu = (fp or {}).get("gpu") if isinstance(fp, dict) else None
        if not isinstance(gpu, dict) or not gpu.get("renderer"):
            return {
                "domain": domain, "status": WARNING, "insufficient": True,
                "expected": "a claimed WebGL renderer to compare against",
                "actual": None,
                "message": f"{INSUFFICIENT_PREFIX} no WebGL renderer claimed",
            }
        renderer = str(gpu.get("renderer") or "")
        adapter = gpu.get("webgpu")
        if not isinstance(adapter, dict) or not adapter.get("vendor"):
            return {
                "domain": domain, "status": WARNING,
                "expected": "a WebGPU adapter identity for the claimed card",
                "actual": {"renderer": renderer, "webgpu": adapter or None},
                "message": (
                    "the profile claims a GPU in WebGL but has no WebGPU adapter "
                    "identity, so the two surfaces can disagree in the page"
                ),
            }
        vendor = str(adapter.get("vendor") or "").lower()
        expected = None
        for needle, name in (("nvidia", "nvidia"), ("amd", "amd"), ("radeon", "amd"),
                             ("intel", "intel"), ("apple", "apple"),
                             ("qualcomm", "qualcomm"), ("adreno", "qualcomm"),
                             ("mali", "arm"), ("arm", "arm")):
            if needle in renderer.lower():
                expected = name
                break
        if expected and vendor != expected:
            return {
                "domain": domain, "status": ERROR,
                "expected": expected,
                "actual": {"renderer": renderer, "webgpu_vendor": vendor},
                "message": (
                    f"WebGL names a {expected} card while the WebGPU adapter says "
                    f"{vendor}. One page cannot have two different graphics cards."
                ),
            }
        return {
            "domain": domain, "status": PASS,
            "expected": expected or vendor,
            "actual": {"renderer": renderer, "webgpu_vendor": vendor,
                       "architecture": adapter.get("architecture")},
            "message": "WebGL and WebGPU describe the same card",
        }

    def _check_device(
        self,
        profile: dict[str, Any],
        preset: dict[str, Any] | None,
        fp: dict[str, Any] | None,
    ) -> dict[str, Any]:
        domain = "device"
        if not isinstance(preset, dict):
            return {
                "domain": domain, "status": WARNING,
                "expected": "a device preset",
                "actual": None,
                "message": "device preset unavailable: cannot cross-check viewport, DPR and touch",
            }

        vp = preset.get("viewport") or {}
        p_w, p_h = vp.get("width"), vp.get("height")
        p_dpr = preset.get("device_scale_factor")
        p_touch = preset.get("touch")
        p_mobile = preset.get("mobile")

        problems: list[str] = []
        # Internal coherence of the preset itself
        if p_mobile is True and p_touch is False:
            problems.append("preset declares mobile=True but touch=False")
        if p_mobile is True and isinstance(p_w, (int, float)) and p_w > _MOBILE_VIEWPORT[1]:
            problems.append(f"preset mobile viewport width {p_w}px exceeds mobile range")

        # Cross-check against the expected fingerprint, when available
        if fp is not None:
            screen = fp.get("screen") or {}
            f_w, f_h, f_dpr = screen.get("w"), screen.get("h"), screen.get("dpr")
            f_touch = fp.get("touch")
            if all(isinstance(v, (int, float)) for v in (p_w, p_h, f_w, f_h)) and (p_w, p_h) != (f_w, f_h):
                problems.append(
                    f"preset viewport {int(p_w)}x{int(p_h)} != fingerprint screen {int(f_w)}x{int(f_h)}"
                )
            if isinstance(p_dpr, (int, float)) and isinstance(f_dpr, (int, float)) and p_dpr != f_dpr:
                problems.append(f"preset DPR {p_dpr} != fingerprint DPR {f_dpr}")
            if isinstance(p_touch, bool) and isinstance(f_touch, (int, float)):
                if (f_touch > 0) != p_touch:
                    problems.append(
                        f"preset touch={p_touch} contradicts fingerprint touch points={f_touch}"
                    )

        if problems:
            return {
                "domain": domain, "status": WARNING,
                "expected": "device preset, viewport, DPR and touch in agreement",
                "actual": {"viewport": {"width": p_w, "height": p_h}, "dpr": p_dpr,
                           "touch": p_touch, "mobile": p_mobile},
                "message": "device mismatch: " + "; ".join(problems),
            }
        return {
            "domain": domain, "status": PASS,
            "expected": "device preset, viewport, DPR and touch in agreement",
            "actual": {"viewport": {"width": p_w, "height": p_h}, "dpr": p_dpr,
                       "touch": p_touch, "mobile": p_mobile},
            "message": "device preset, viewport, DPR and touch are coherent",
        }

    def _check_platform(self, profile: dict[str, Any], fp: dict[str, Any] | None) -> dict[str, Any]:
        domain = "platform"
        ua = profile.get("userAgent") or ""
        ua_plat = _ua_platform(ua)
        ch = _ch_of(fp)
        ch_plat = ch.get("platform")

        if ua_plat is None or not ch_plat:
            return {
                "domain": domain, "status": WARNING,
                "expected": "OS, user-agent and Client Hints platform in agreement",
                "actual": {"ua_platform": ua_plat, "ch_platform": ch_plat},
                "message": "insufficient data: cannot verify OS/user-agent/Client-Hints agreement",
            }

        ch_norm = str(ch_plat).strip().lower()
        ua_norm = ua_plat.strip().lower()
        # "Android" CH platform must pair with an Android UA, etc.
        match = (
            (ch_norm == "android" and ua_norm == "android")
            or (ch_norm == "ios" and ua_norm == "ios")
            or (ch_norm == "windows" and ua_norm == "windows")
            or (ch_norm in ("macos", "mac os") and ua_norm == "macos")
            or (ch_norm == "linux" and ua_norm == "linux")
            or (ch_norm == "chromeos" and ua_norm == "chromeos")
        )
        if match:
            return {
                "domain": domain, "status": PASS,
                "expected": ua_plat,
                "actual": {"ua_platform": ua_plat, "ch_platform": ch_plat},
                "message": f"user-agent OS ({ua_plat}) matches Client Hints platform ({ch_plat})",
            }
        return {
            "domain": domain, "status": ERROR,
            "expected": f"Client Hints platform coherent with UA OS ({ua_plat})",
            "actual": {"ua_platform": ua_plat, "ch_platform": ch_plat},
            "message": (
                f"OS mismatch: user-agent reports {ua_plat} but Client Hints "
                f"platform is '{ch_plat}'. This is a hard detection signal."
            ),
        }

    def _check_mobile(
        self,
        profile: dict[str, Any],
        preset: dict[str, Any] | None,
        fp: dict[str, Any] | None,
    ) -> dict[str, Any]:
        domain = "mobile"
        is_mobile = profile.get("isMobile")
        if is_mobile is None and isinstance(preset, dict):
            is_mobile = preset.get("mobile")
        if is_mobile is None and isinstance(fp, dict):
            is_mobile = fp.get("isMobile")

        # Touch signal
        touch: bool | None = None
        if isinstance(fp, dict) and isinstance(fp.get("touch"), (int, float)):
            touch = fp["touch"] > 0
        elif isinstance(preset, dict) and isinstance(preset.get("touch"), bool):
            touch = preset["touch"]

        # Viewport signal
        w = h = None
        if isinstance(preset, dict) and isinstance(preset.get("viewport"), dict):
            w, h = preset["viewport"].get("width"), preset["viewport"].get("height")
        if (w, h) == (None, None) and isinstance(fp, dict) and isinstance(fp.get("screen"), dict):
            w, h = fp["screen"].get("w"), fp["screen"].get("h")

        actual = {"isMobile": bool(is_mobile) if is_mobile is not None else None,
                  "touch": touch, "viewport": {"width": w, "height": h}}

        if is_mobile is None:
            return {
                "domain": domain, "status": WARNING,
                "expected": "mobile category declared",
                "actual": actual,
                "message": "insufficient data: mobile category unknown",
            }

        problems: list[str] = []
        if is_mobile:
            if touch is False:
                problems.append("mobile profile declares touch=False (mobile devices have touch)")
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                min_w, max_w, min_h, max_h = _MOBILE_VIEWPORT
                if not (min_w <= w <= max_w and min_h <= h <= max_h):
                    problems.append(
                        f"mobile viewport {int(w)}x{int(h)} outside mobile range "
                        f"{min_w}-{max_w}x{min_h}-{max_h}"
                    )
            elif touch is None and (w, h) == (None, None):
                return {
                    "domain": domain, "status": WARNING,
                    "expected": "touch enabled and a mobile-range viewport",
                    "actual": actual,
                    "message": "insufficient data: no touch/viewport signal for a mobile profile",
                }
        else:
            if isinstance(w, (int, float)) and isinstance(h, (int, float)):
                min_w, max_w, min_h, max_h = _MOBILE_VIEWPORT
                if min_w <= w <= max_w and min_h <= h <= max_h and touch:
                    problems.append(
                        f"desktop profile uses a mobile-range viewport {int(w)}x{int(h)} with touch"
                    )

        if problems:
            return {
                "domain": domain, "status": ERROR if is_mobile else WARNING,
                "expected": "touch enabled and mobile-range viewport" if is_mobile
                            else "desktop-range viewport",
                "actual": actual,
                "message": "mobile consistency failure: " + "; ".join(problems),
            }
        return {
            "domain": domain, "status": PASS,
            "expected": "mobile" if is_mobile else "desktop",
            "actual": actual,
            "message": "mobile category, touch and viewport are coherent",
        }

    def _check_browser(self, profile: dict[str, Any], fp: dict[str, Any] | None) -> dict[str, Any]:
        domain = "browser"
        ua = profile.get("userAgent") or ""
        family = _ua_browser_family(ua)
        ch = _ch_of(fp)
        brands = ch.get("brands") if isinstance(ch.get("brands"), list) else None

        if family is None or not brands:
            return {
                "domain": domain, "status": WARNING,
                "expected": "Client Hints brands containing the browser family",
                "actual": {"browser_family": family,
                           "brands": [b.get("brand") for b in brands] if brands else None},
                "message": "insufficient data: cannot verify browser/Client-Hints brand agreement",
            }

        brand_names = [str(b.get("brand") or "") for b in brands if isinstance(b, dict)]
        hit = any(family.lower() in name.lower() for name in brand_names)
        if hit:
            return {
                "domain": domain, "status": PASS,
                "expected": family,
                "actual": {"browser_family": family, "brands": brand_names},
                "message": f"user-agent browser ({family}) present in Client Hints brands",
            }
        return {
            "domain": domain, "status": WARNING,
            "expected": f"Client Hints brand containing '{family}'",
            "actual": {"browser_family": family, "brands": brand_names},
            "message": (
                f"browser mismatch: user-agent reports {family} but Client Hints "
                f"brands are {brand_names}"
            ),
        }

    def _check_gpu_webgl(self, profile: dict[str, Any], fp: dict[str, Any] | None) -> dict[str, Any]:
        domain = "gpu_webgl"
        expected_policy = (profile.get("webglPolicy") or profile.get("webgl") or "default")
        expected_policy = str(expected_policy).strip().lower() or "default"

        runtime = None
        if isinstance(fp, dict) and isinstance(fp.get("webglRuntime"), dict):
            runtime = fp["webglRuntime"]
        expected_gpu = fp.get("gpu") if isinstance(fp, dict) else None

        if runtime is None and not isinstance(expected_gpu, dict):
            return {
                "domain": domain, "status": WARNING,
                "expected": "WebGL vendor/renderer coherent with expected GPU",
                "actual": None,
                "message": "insufficient data: no runtime WebGL capability provided",
            }

        actual = {"webgl_policy": expected_policy,
                  "expected_gpu": expected_gpu, "runtime": runtime}

        if expected_policy in ("disabled", "disable", "off"):
            renderer = str((runtime or {}).get("renderer") or "").lower()
            if renderer and "swiftshader" not in renderer and "llvmpipe" not in renderer \
                    and "disabled" not in renderer:
                return {
                    "domain": domain, "status": WARNING,
                    "expected": "software/disabled WebGL renderer",
                    "actual": actual,
                    "message": (
                        "WebGL is expected to be disabled/software, but the runtime "
                        f"reports renderer '{(runtime or {}).get('renderer')}'"
                    ),
                }
            return {
                "domain": domain, "status": PASS,
                "expected": "software/disabled WebGL renderer",
                "actual": actual,
                "message": "WebGL state matches the expected disabled/software policy",
            }

        # Default policy: a real GPU renderer is expected (ANGLE/vendor string).
        renderer = str(((runtime or {}).get("renderer") or (expected_gpu or {}).get("renderer") or ""))
        vendor = str(((runtime or {}).get("vendor") or (expected_gpu or {}).get("vendor") or ""))
        if renderer or vendor:
            return {
                "domain": domain, "status": PASS,
                "expected": "hardware WebGL renderer",
                "actual": actual,
                "message": f"WebGL vendor/renderer present ({vendor} / {renderer})",
            }
        return {
            "domain": domain, "status": WARNING,
            "expected": "hardware WebGL renderer",
            "actual": actual,
            "message": "insufficient data: WebGL vendor/renderer not reported",
        }
