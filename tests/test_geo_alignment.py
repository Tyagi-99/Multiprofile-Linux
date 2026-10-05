"""Timezone, language and position have to name one place.

Before this, three surfaces were set by three different things or not at all:
the timezone came from a creation default, the language from the fingerprint,
and the position from nothing -- so ``getCurrentPosition`` resolved against
the host, returning the operator's real surroundings, identically for every
profile on the machine.

Worse, the creation default was a **fixed US identity**: `create_profile`
stamped `America/New_York` and `en-US` on every profile before anything knew
where its traffic would exit. A fleet on Brazilian, German and Indian proxies
all claimed the same city -- a contradiction inside each profile and a value
all of them shared.

The rule is that the exit IP is the authority, because everything else is a
claim and the address the packets came from is not. But a value the operator
*chose* is theirs, so only the creation default is replaced; a deliberate
mismatch is reported instead of being silently rewritten.
"""
from __future__ import annotations

import pytest

from multiprofile.geo import (
    COUNTRY_PROFILE,
    align_to_exit,
    country_for_timezone,
    geolocation_for,
)


def _auto(**over):
    """A profile as `create_profile` leaves it: defaults, not decisions."""
    base = {"id": "p1", "geoAuto": True, "timezone": "America/New_York",
            "language": "en-US"}
    base.update(over)
    return base


def _chosen(**over):
    base = {"id": "p2", "geoAuto": False, "timezone": "America/New_York",
            "language": "en-US"}
    base.update(over)
    return base


# --------------------------------------------------------------------------
# 1. The default is not a decision
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("cc", "tz", "lang"), [
    ("BR", "America/Sao_Paulo", "pt-BR"),
    ("DE", "Europe/Berlin", "de-DE"),
    ("JP", "Asia/Tokyo", "ja-JP"),
    ("IN", "Asia/Kolkata", "en-IN"),
])
def test_an_unconfigured_profile_follows_its_exit(cc, tz, lang):
    """The measurement in the module docstring, as an assertion."""
    plan = align_to_exit(_auto(), {"seed": 7}, {"countryCode": cc})
    assert plan["timezone"] == tz, plan
    assert plan["language"] == lang, plan
    assert plan["source"] == "exit_ip"


def test_a_fleet_no_longer_shares_one_city():
    """Every profile used to claim New York whatever proxy it was on."""
    zones = {
        align_to_exit(_auto(), {"seed": 7}, {"countryCode": cc})["timezone"]
        for cc in ("BR", "DE", "JP", "US")
    }
    assert len(zones) == 4, zones


def test_a_chosen_timezone_is_not_overwritten():
    """An operator may be running a US persona through a German exit on
    purpose. Silently rewriting their configuration at launch would be a
    worse surprise than telling them the two disagree."""
    plan = align_to_exit(_chosen(), {"seed": 7}, {"countryCode": "DE"})
    assert plan["timezone"] == "America/New_York"
    assert plan["source"] == "profile"


def test_a_deliberate_mismatch_is_reported():
    plan = align_to_exit(_chosen(), {"seed": 7}, {"countryCode": "DE"})
    assert plan["conflicts"], plan
    assert any("exit IP" in c for c in plan["conflicts"])


def test_a_matching_choice_raises_nothing():
    plan = align_to_exit(_chosen(), {"seed": 7}, {"countryCode": "US"})
    assert plan["conflicts"] == []


# --------------------------------------------------------------------------
# 2. Position
# --------------------------------------------------------------------------

def test_the_position_follows_the_timezone_the_page_sees():
    """Not the exit IP. When the two differ, putting the position with the
    exit would show the page a third value rather than resolving anything;
    keeping it with the timezone at least makes the surfaces the page reads
    agree with each other."""
    plan = align_to_exit(_chosen(), {"seed": 7}, {"countryCode": "DE"})
    assert plan["timezone"] == "America/New_York"
    assert plan["geolocation"]["city"] == "New York", plan["geolocation"]


def test_two_profiles_do_not_share_a_position():
    """A fixed city centre would be a constant shared by every profile in the
    country -- the same linking mistake the audio fingerprint made."""
    a = geolocation_for("US", "profile-a")
    b = geolocation_for("US", "profile-b")
    assert (a["latitude"], a["longitude"]) != (b["latitude"], b["longitude"])


def test_a_position_is_stable_for_a_profile():
    assert geolocation_for("US", "p") == geolocation_for("US", "p")


def test_a_position_stays_near_its_city():
    """Jitter must not land the profile in another country or the sea."""
    for cc, (_tz, _lang, lat, lon, _city) in COUNTRY_PROFILE.items():
        got = geolocation_for(cc, "seed")
        assert abs(got["latitude"] - lat) < 0.1, cc
        assert abs(got["longitude"] - lon) < 0.1, cc


def test_the_accuracy_does_not_claim_gps():
    """A desktop answering with a metre-accurate position is claiming a
    capability the machine does not have."""
    assert geolocation_for("US", "p")["accuracy"] >= 500


def test_an_unknown_country_claims_nothing():
    """An invented position that lands in the sea is worse than none."""
    assert geolocation_for("ZZ", "p") is None


# --------------------------------------------------------------------------
# 3. Language lists
# --------------------------------------------------------------------------

def test_the_language_list_is_shaped_like_a_browser_s():
    plan = align_to_exit(_auto(), {"seed": 7}, {"countryCode": "BR"})
    assert plan["languages"] == ["pt-BR", "pt", "en"]


def test_an_english_locale_does_not_repeat_english():
    plan = align_to_exit(_auto(), {"seed": 7}, {"countryCode": "GB"})
    assert plan["languages"] == ["en-GB", "en"]


# --------------------------------------------------------------------------
# 4. Degrading without data
# --------------------------------------------------------------------------

def test_no_diagnostics_leaves_the_profile_alone():
    plan = align_to_exit(_chosen(), {"seed": 7}, None)
    assert plan["timezone"] == "America/New_York"
    assert plan["conflicts"] == []


def test_an_unknown_exit_country_does_not_blank_the_profile():
    """A country we have no data for must not leave the launch with nothing
    to claim."""
    plan = align_to_exit(_chosen(), {"seed": 7}, {"countryCode": "ZZ"})
    assert plan["timezone"] == "America/New_York"
    assert plan["geolocation"] is not None


def test_diagnostics_may_be_an_object_or_a_dict():
    class Diag:
        country_code = "DE"

    from_obj = align_to_exit(_auto(), {"seed": 7}, Diag())
    from_dict = align_to_exit(_auto(), {"seed": 7}, {"country_code": "DE"})
    assert from_obj["timezone"] == from_dict["timezone"] == "Europe/Berlin"


def test_the_timezone_table_agrees_with_the_consistency_engine():
    """Both map zones to countries; if they drifted, a launch could align to
    a country the consistency check then flags as a conflict."""
    for cc, (tz, *_rest) in COUNTRY_PROFILE.items():
        mapped = country_for_timezone(tz)
        assert mapped in (cc, None), (tz, cc, mapped)
