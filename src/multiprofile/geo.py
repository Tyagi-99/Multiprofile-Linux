"""Where a profile says it is, and in what language.

Three surfaces have to agree here, and before this they were set by three
different things or not set at all:

* the **timezone**, which the profile carries and F22 anchored on the engine;
* the **language**, which the fingerprint carries but which Chrome's own UI
  and ``Accept-Language`` did not follow, because no ``--lang`` was passed;
* the **geolocation**, which nothing set, so ``getCurrentPosition`` resolved
  against the host -- the machine's real surroundings, identical for every
  profile on it.

A profile routed through a Brazilian proxy that reports ``America/New_York``,
asks for pages in ``en-US`` and, when a site requests a position, answers with
coordinates in the operator's actual city is not three small mismatches. It is
the same contradiction stated three times, and the geolocation one is the
worst of them because it is the operator's real location rather than a wrong
guess.

This module holds the country data needed to make the three agree, and the
rule for deciding which one wins when they disagree. The rule is: **the exit
IP is the authority**. Everything else is something we claim, and a claim that
disagrees with the address the packets actually came from is the one that is
wrong.

Coordinates are the country's principal city with a deterministic per-profile
offset of a few kilometres, not a fixed point. A fixed point would be a
shared constant across every profile in the country -- the same linking
mistake the audio fingerprint made in F24 -- and real geolocation is never
exact to the metre anyway, so the accuracy figure is honest about that.
"""
from __future__ import annotations

import hashlib
from typing import Any

#: ISO country -> (timezone, language, latitude, longitude, city).
#: Deliberately one representative zone per country rather than an exhaustive
#: table: a guess that is wrong in a plausible way is worse than declining to
#: guess, so a country that is not here produces no claim at all.
COUNTRY_PROFILE: dict[str, tuple[str, str, float, float, str]] = {
    "US": ("America/New_York", "en-US", 40.7128, -74.0060, "New York"),
    "CA": ("America/Toronto", "en-CA", 43.6532, -79.3832, "Toronto"),
    "GB": ("Europe/London", "en-GB", 51.5074, -0.1278, "London"),
    "IE": ("Europe/Dublin", "en-IE", 53.3498, -6.2603, "Dublin"),
    "AU": ("Australia/Sydney", "en-AU", -33.8688, 151.2093, "Sydney"),
    "NZ": ("Pacific/Auckland", "en-NZ", -36.8485, 174.7633, "Auckland"),
    "DE": ("Europe/Berlin", "de-DE", 52.5200, 13.4050, "Berlin"),
    "FR": ("Europe/Paris", "fr-FR", 48.8566, 2.3522, "Paris"),
    "ES": ("Europe/Madrid", "es-ES", 40.4168, -3.7038, "Madrid"),
    "IT": ("Europe/Rome", "it-IT", 41.9028, 12.4964, "Rome"),
    "PT": ("Europe/Lisbon", "pt-PT", 38.7223, -9.1393, "Lisbon"),
    "NL": ("Europe/Amsterdam", "nl-NL", 52.3676, 4.9041, "Amsterdam"),
    "BE": ("Europe/Brussels", "nl-BE", 50.8503, 4.3517, "Brussels"),
    "CH": ("Europe/Zurich", "de-CH", 47.3769, 8.5417, "Zurich"),
    "AT": ("Europe/Vienna", "de-AT", 48.2082, 16.3738, "Vienna"),
    "PL": ("Europe/Warsaw", "pl-PL", 52.2297, 21.0122, "Warsaw"),
    "CZ": ("Europe/Prague", "cs-CZ", 50.0755, 14.4378, "Prague"),
    "SE": ("Europe/Stockholm", "sv-SE", 59.3293, 18.0686, "Stockholm"),
    "NO": ("Europe/Oslo", "nb-NO", 59.9139, 10.7522, "Oslo"),
    "DK": ("Europe/Copenhagen", "da-DK", 55.6761, 12.5683, "Copenhagen"),
    "FI": ("Europe/Helsinki", "fi-FI", 60.1699, 24.9384, "Helsinki"),
    "GR": ("Europe/Athens", "el-GR", 37.9838, 23.7275, "Athens"),
    "RO": ("Europe/Bucharest", "ro-RO", 44.4268, 26.1025, "Bucharest"),
    "HU": ("Europe/Budapest", "hu-HU", 47.4979, 19.0402, "Budapest"),
    "TR": ("Europe/Istanbul", "tr-TR", 41.0082, 28.9784, "Istanbul"),
    "RU": ("Europe/Moscow", "ru-RU", 55.7558, 37.6173, "Moscow"),
    "UA": ("Europe/Kiev", "uk-UA", 50.4501, 30.5234, "Kyiv"),
    "BR": ("America/Sao_Paulo", "pt-BR", -23.5505, -46.6333, "Sao Paulo"),
    "MX": ("America/Mexico_City", "es-MX", 19.4326, -99.1332, "Mexico City"),
    "AR": ("America/Buenos_Aires", "es-AR", -34.6037, -58.3816, "Buenos Aires"),
    "CL": ("America/Santiago", "es-CL", -33.4489, -70.6693, "Santiago"),
    "CO": ("America/Bogota", "es-CO", 4.7110, -74.0721, "Bogota"),
    "PE": ("America/Lima", "es-PE", -12.0464, -77.0428, "Lima"),
    "IN": ("Asia/Kolkata", "en-IN", 19.0760, 72.8777, "Mumbai"),
    "PK": ("Asia/Karachi", "en-PK", 24.8607, 67.0011, "Karachi"),
    "BD": ("Asia/Dhaka", "bn-BD", 23.8103, 90.4125, "Dhaka"),
    "TH": ("Asia/Bangkok", "th-TH", 13.7563, 100.5018, "Bangkok"),
    "ID": ("Asia/Jakarta", "id-ID", -6.2088, 106.8456, "Jakarta"),
    "SG": ("Asia/Singapore", "en-SG", 1.3521, 103.8198, "Singapore"),
    "MY": ("Asia/Kuala_Lumpur", "ms-MY", 3.1390, 101.6869, "Kuala Lumpur"),
    "PH": ("Asia/Manila", "en-PH", 14.5995, 120.9842, "Manila"),
    "VN": ("Asia/Ho_Chi_Minh", "vi-VN", 10.8231, 106.6297, "Ho Chi Minh City"),
    "HK": ("Asia/Hong_Kong", "zh-HK", 22.3193, 114.1694, "Hong Kong"),
    "CN": ("Asia/Shanghai", "zh-CN", 31.2304, 121.4737, "Shanghai"),
    "TW": ("Asia/Taipei", "zh-TW", 25.0330, 121.5654, "Taipei"),
    "KR": ("Asia/Seoul", "ko-KR", 37.5665, 126.9780, "Seoul"),
    "JP": ("Asia/Tokyo", "ja-JP", 35.6762, 139.6503, "Tokyo"),
    "AE": ("Asia/Dubai", "ar-AE", 25.2048, 55.2708, "Dubai"),
    "SA": ("Asia/Riyadh", "ar-SA", 24.7136, 46.6753, "Riyadh"),
    "IL": ("Asia/Jerusalem", "he-IL", 32.0853, 34.7818, "Tel Aviv"),
    "EG": ("Africa/Cairo", "ar-EG", 30.0444, 31.2357, "Cairo"),
    "NG": ("Africa/Lagos", "en-NG", 6.5244, 3.3792, "Lagos"),
    "KE": ("Africa/Nairobi", "en-KE", -1.2921, 36.8219, "Nairobi"),
    "ZA": ("Africa/Johannesburg", "en-ZA", -26.2041, 28.0473, "Johannesburg"),
    "MA": ("Africa/Casablanca", "ar-MA", 33.5731, -7.5898, "Casablanca"),
}

#: How far a profile's claimed position may sit from the city centre, in
#: degrees -- roughly 8 km of latitude at the equator.
_JITTER_DEG = 0.075

#: Reported accuracy in metres. Wide on purpose: a browser that answers with a
#: metre-accurate position it could only have got from GPS, on a desktop with
#: no GPS, is claiming a capability the machine does not have.
_ACCURACY_M = 1200.0


def _jitter(seed_material: str, index: int) -> float:
    """A stable offset in [-1, 1) derived from the profile."""
    digest = hashlib.sha256(f"{seed_material}:{index}".encode()).digest()
    value = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return value * 2 - 1


def geolocation_for(country_code: str, seed_material: str) -> dict[str, Any] | None:
    """Coordinates to claim for a country, offset per profile.

    Returns None for a country not in the table rather than guessing: an
    invented position that lands in the sea, or in the wrong country, is worse
    than leaving the surface alone.
    """
    entry = COUNTRY_PROFILE.get((country_code or "").upper())
    if not entry:
        return None
    _tz, _lang, lat, lon, city = entry
    return {
        "latitude": round(lat + _JITTER_DEG * _jitter(seed_material, 1), 6),
        "longitude": round(lon + _JITTER_DEG * _jitter(seed_material, 2), 6),
        "accuracy": _ACCURACY_M,
        "city": city,
    }


def country_for_timezone(timezone: str) -> str | None:
    """The country a timezone implies, using the consistency engine's table."""
    from .consistency import TZ_COUNTRY

    return TZ_COUNTRY.get((timezone or "").strip())


def align_to_exit(
    profile: dict[str, Any],
    fingerprint: dict[str, Any] | None = None,
    diagnostics: Any = None,
) -> dict[str, Any]:
    """Decide the timezone, language and position this launch should claim.

    The exit IP wins. Everything else is a claim, and a claim that disagrees
    with the address the packets came from is the one that is wrong -- so when
    the proxy check reports a country, an *unset* timezone or language is
    filled in from it, and a set one that disagrees is reported rather than
    silently overwritten.

    Reporting rather than overwriting matters: an operator may be deliberately
    running a US persona through a German exit, and silently rewriting their
    configuration at launch would be a worse surprise than telling them the
    two disagree. The consistency engine already raises that as a WARNING; the
    value here is that the *unset* case stops defaulting to America/New_York
    regardless of where the traffic leaves from.

    Returns ``{"timezone", "language", "languages", "geolocation", "source",
    "conflicts"}``. ``source`` says which input decided it, so a launch note
    can explain the choice instead of presenting it as a fact from nowhere.
    """
    profile = profile or {}
    seed_material = str(
        (fingerprint or {}).get("seed") or profile.get("seed") or profile.get("id") or ""
    )

    cc = None
    if diagnostics is not None:
        cc = _diag_country(diagnostics)
    cc = (cc or "").upper() or None

    # A value the operator chose is theirs. A value that is only the creation
    # default is not, and `geoAuto` is how the two are told apart -- without
    # it every profile carries America/New_York and en-US from the moment it
    # exists, so the "unset" branch below could never fire and a fleet on
    # exits all over the world all announced the same city.
    auto = bool(profile.get("geoAuto"))
    tz = (profile.get("timezone") or "").strip() or None
    lang = (profile.get("language") or (fingerprint or {}).get("language") or "").strip() or None
    if auto:
        tz = None
        lang = None

    conflicts: list[str] = []
    source = "profile"

    if cc and cc in COUNTRY_PROFILE:
        exit_tz, exit_lang, *_ = COUNTRY_PROFILE[cc]
        if not tz:
            tz, source = exit_tz, "exit_ip"
        elif country_for_timezone(tz) not in (None, cc):
            conflicts.append(
                f"timezone {tz} implies {country_for_timezone(tz)} but the exit IP is in {cc}"
            )
        if not lang:
            lang = exit_lang
            source = "exit_ip" if source == "profile" else source
        elif lang.split("-")[-1].upper() not in (cc, lang.upper()):
            conflicts.append(
                f"language {lang} does not match the exit country {cc}"
            )

    # Position follows the **timezone the page will see**, not the exit IP.
    #
    # Those are the same country in the ordinary case. They differ only when
    # the operator has deliberately pointed a persona at an exit somewhere
    # else, and there the choice matters: a profile reporting America/New_York
    # through a German exit, answering a position request with coordinates in
    # Berlin, would be showing the page a third value rather than resolving
    # anything. Keeping the position with the timezone at least makes the
    # surfaces the page reads agree with each other, and the disagreement with
    # the exit IP is reported in `conflicts` for the operator to settle.
    geo_cc = country_for_timezone(tz or "") or (cc if cc in COUNTRY_PROFILE else None)
    geolocation = geolocation_for(geo_cc, seed_material) if geo_cc else None

    languages = None
    if lang:
        languages = [lang]
        base = lang.split("-")[0]
        if base and base != lang:
            languages.append(base)
        if base != "en":
            languages.append("en")

    return {
        "timezone": tz,
        "language": lang,
        "languages": languages,
        "geolocation": geolocation,
        "country": geo_cc,
        "source": source,
        "conflicts": conflicts,
    }


def _diag_country(diagnostics: Any) -> str | None:
    if isinstance(diagnostics, dict):
        return diagnostics.get("countryCode") or diagnostics.get("country_code")
    return getattr(diagnostics, "country_code", None)
