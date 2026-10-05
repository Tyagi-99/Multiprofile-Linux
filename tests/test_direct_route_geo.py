"""F43: a profile with no proxy must claim the machine it actually leaves from.

F31 stopped every profile announcing ``America/New_York`` regardless of where
its traffic left from, by filling an unset timezone from the exit IP's country.
That fix reaches every route with a proxy on it. It reaches no route without
one, because a DIRECT route runs no proxy check -- ``needs_proxy_check()`` is
False for ``DIRECT`` -- so no exit country is ever learned, ``cc`` is None, and
the fill-in branch cannot fire. The profile keeps the creation default set in
``storage/profiles.py`` and tells every page it is in New York while its
packets leave from the operator's real address. Measured on the development
host, whose zone is ``Asia/Kolkata``: a ten-and-a-half hour contradiction on
the pair that is checked more routinely than any other.

The consistency score did not catch it either. ``_check_geo`` saw no IP
country, returned WARNING, and the geo domain was excluded from the
denominator by design. That exclusion is honest for "no proxy configured yet";
it was wrong for "no proxy, ever" -- on a direct route the egress country is
perfectly knowable, because it is this machine's.

The fix is the smallest one that is true: on DIRECT the host is the exit, so
the host's own zone fills an unset timezone. It is not a claim about somewhere
else, it is a measurement of here. Everything downstream falls out of the
existing design -- the position follows the timezone, so it lands near the
country's principal city with a per-profile offset rather than resolving
against the operator's real surroundings.

These tests pin that, that an operator's own choice still wins, that a proxied
profile is untouched (F31 must not regress), and that nothing is invented when
the host zone cannot be resolved.
"""
from __future__ import annotations

import pytest

from multiprofile import geo
from multiprofile.consistency import PASS, WARNING, ProfileConsistencyEngine

#: The real function, captured before the autouse fixture replaces it, so
#: the tests in section 3 can exercise it rather than the stand-in.
_REAL_HOST_TIMEZONE = geo.host_timezone

HOST_TZ = "Asia/Kolkata"
HOST_CC = "IN"


@pytest.fixture(autouse=True)
def host_is_india(monkeypatch):
    """Pin the host zone and locale so these tests describe a fixed machine
    rather than whoever's laptop happens to run them."""
    monkeypatch.setattr(geo, "host_timezone", lambda: HOST_TZ)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)


def _profile(**over):
    """A profile as ``storage.profiles._new_profile`` actually creates one."""
    base = {
        "id": "p1", "seed": 4242,
        "timezone": "America/New_York",   # the creation default
        "geoAuto": True,                  # ...and the flag saying it is only that
        "language": "en-US",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# 1. The finding
# --------------------------------------------------------------------------

def test_a_no_proxy_profile_claims_the_host_zone():
    """The defect, stated directly: this returned America/New_York."""
    plan = geo.align_to_exit(_profile(), None, None, direct=True)
    assert plan["timezone"] == HOST_TZ
    assert plan["source"] == "host"


def test_the_position_follows_the_host_zone():
    """Not a separate fix -- the position already follows the timezone, so
    resolving the zone stops getCurrentPosition answering with the operator's
    real surroundings and gives a seeded point in the right country instead."""
    plan = geo.align_to_exit(_profile(), None, None, direct=True)
    assert plan["country"] == HOST_CC
    assert plan["geolocation"]["city"] == "Mumbai"


def test_two_direct_profiles_get_different_positions():
    """Same country, same honest answer -- but a fixed point shared by every
    profile is the linking mistake F24 was about."""
    a = geo.align_to_exit(_profile(id="a", seed=1), None, None, direct=True)
    b = geo.align_to_exit(_profile(id="b", seed=2), None, None, direct=True)
    assert a["geolocation"]["latitude"] != b["geolocation"]["latitude"]
    assert a["timezone"] == b["timezone"] == HOST_TZ


def test_the_position_is_stable_for_one_profile():
    """The seed promise: the same profile is the same machine every launch."""
    one = geo.align_to_exit(_profile(), None, None, direct=True)
    two = geo.align_to_exit(_profile(), None, None, direct=True)
    assert one["geolocation"] == two["geolocation"]


# --------------------------------------------------------------------------
# 2. What must not be overridden
# --------------------------------------------------------------------------

def test_a_timezone_the_operator_chose_is_not_overwritten():
    """geoAuto is exactly the flag that tells a chosen value from a creation
    default. An operator running a US persona on a direct connection is doing
    something deliberate, and silently rewriting it would be a worse surprise
    than reporting the disagreement."""
    plan = geo.align_to_exit(
        _profile(geoAuto=False, timezone="America/New_York"), None, None, direct=True
    )
    assert plan["timezone"] == "America/New_York"
    assert plan["source"] == "profile"


def test_the_language_is_left_alone():
    """The host's locale is what a real browser here sends. Rewriting en-US to
    the zone country's majority language would invent a claim, not fix one."""
    plan = geo.align_to_exit(_profile(), None, None, direct=True)
    assert plan["language"] is None, "nothing should be imposed on the language"


def test_a_proxied_profile_is_untouched(monkeypatch):
    """F31 must not regress: with an exit country, that country still wins and
    the host is never consulted."""
    monkeypatch.setattr(
        geo, "host_timezone", lambda: pytest.fail("the host must not be asked")
    )
    plan = geo.align_to_exit(
        _profile(), None, {"country_code": "BR"}, direct=False
    )
    assert plan["timezone"] == "America/Sao_Paulo"
    assert plan["source"] == "exit_ip"


def test_direct_ignores_diagnostics_left_over_from_a_removed_proxy():
    """A profile that had a proxy and had it taken away keeps the record of
    the last route that was probed. That record describes a route no longer in
    use, so it must not answer for where this launch leaves from -- which is
    the exact shape of 'add a proxy when needed, remove it later'."""
    plan = geo.align_to_exit(
        _profile(), None, {"country_code": "BR"}, direct=True
    )
    assert plan["timezone"] == HOST_TZ
    assert plan["source"] == "host"


def test_nothing_is_claimed_when_the_host_zone_is_unresolvable(monkeypatch):
    """Declining to answer is the module's standing rule. UTC would be a guess
    wearing a plausible face."""
    monkeypatch.setattr(geo, "host_timezone", lambda: None)
    plan = geo.align_to_exit(_profile(), None, None, direct=True)
    assert plan["timezone"] is None
    assert plan["source"] == "profile"


# --------------------------------------------------------------------------
# 3. host_timezone itself
# --------------------------------------------------------------------------

def test_host_timezone_prefers_TZ(monkeypatch):
    """A process with TZ set is the zone Chromium itself would report."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert _REAL_HOST_TIMEZONE() == "Europe/Berlin"


def test_host_timezone_ignores_an_abbreviation(monkeypatch):
    """IST is not an IANA zone, and claiming it would be a worse answer than
    the wrong one it replaced."""
    monkeypatch.setenv("TZ", "IST-5:30")
    assert _REAL_HOST_TIMEZONE() != "IST-5:30"


def test_host_timezone_ignores_a_zone_that_does_not_exist(monkeypatch):
    monkeypatch.setenv("TZ", "Middle/Earth")
    assert _REAL_HOST_TIMEZONE() != "Middle/Earth"


def test_host_timezone_reads_this_machine():
    """Not mocked. Whatever it returns must be a real zone, or nothing."""
    tz = _REAL_HOST_TIMEZONE()
    if tz is None:
        pytest.skip("this host has no resolvable IANA zone")
    from zoneinfo import ZoneInfo

    ZoneInfo(tz)  # must not raise
    assert "/" in tz


# --------------------------------------------------------------------------
# 4. The score stops staying silent
# --------------------------------------------------------------------------

def _geo_check(profile):
    for check in ProfileConsistencyEngine().check(profile)["checks"]:
        if check["domain"] == "geo":
            return check
    raise AssertionError("no geo domain in the report")


def test_the_geo_domain_is_scored_for_a_no_proxy_profile():
    """It used to be excluded from the denominator, so a profile announcing
    New York from an Indian address scored exactly as well as a correct one."""
    check = _geo_check(_profile())
    assert check["status"] == PASS
    assert "insufficient data" not in check["message"]


def test_the_score_judges_what_the_launch_will_claim_not_what_is_stored():
    """The stored timezone is still the creation default here. Scoring that
    would report a conflict the launch itself resolves, and a false alarm
    teaches an operator to ignore the score."""
    check = _geo_check(_profile())
    assert check["actual"]["timezone"] == HOST_TZ
    assert check["status"] == PASS


def test_a_deliberate_foreign_persona_on_a_direct_route_is_reported():
    check = _geo_check(_profile(geoAuto=False, timezone="America/New_York"))
    assert check["status"] == WARNING
    assert "leaves from this machine" in check["message"]


def test_a_language_the_host_would_not_send_is_reported():
    check = _geo_check(_profile(language="pt-BR"))
    assert check["status"] == WARNING
    assert "pt-BR" in check["message"]


def test_en_US_from_an_indian_connection_is_not_a_conflict():
    """Enormously common and not a detection signal. Flagging it would be the
    noise that makes the whole score ignorable."""
    check = _geo_check(_profile(language="en-US"))
    assert check["status"] == PASS


def test_the_detection_signal_note_only_appears_for_a_timezone_conflict():
    """Appending it to a language-only conflict describes the wrong problem."""
    check = _geo_check(_profile(language="pt-BR"))
    assert "IP/timezone pair" not in check["message"]


def test_an_unresolvable_host_zone_falls_back_to_insufficient_data(monkeypatch):
    """Still honest: excluded from the denominator rather than scored on a
    comparison that could not be made."""
    monkeypatch.setattr(geo, "host_timezone", lambda: None)
    check = _geo_check(_profile())
    assert check["status"] == WARNING
    assert "insufficient data" in check["message"]
