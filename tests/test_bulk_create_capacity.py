"""Bulk creation, and the limit on how large a fleet may get.

Creating profiles from a template was capped at 50 by
``max(1, min(int(payload.get("count") or 1), 50))``. Asking for 150 returned
HTTP 200 with ``ok: true`` and fifty profiles; nothing in the response
mentioned the other hundred. The number 50 was unrelated to anything -- not
the concurrency limit, not the storage limit, not a measured cost -- and
filling a fleet from a template is the one flow this endpoint exists for, so
silently doing a third of it was the worst available answer.

Measured before the fix, against an empty store:

    asked for 150 -> status=200 ok=True count=50
    count="abc"   -> status=200 count=1
    count=-5      -> status=200 count=1

Three things are asserted here.

**Nothing is silent.** A count that will not fit is refused with 409 and the
numbers needed to act on it; a count that is not a positive integer is a 400.
Neither is quietly reinterpreted.

**A batch is all or nothing.** The old loop called ``create_profile`` once per
profile, so a failure at row 80 of 150 left 79 stored and no way to tell which.
The batch is one transaction now, so a refusal stores nothing.

**The cap is a real cap.** It is enforced in the store rather than in the
endpoint, because a limit that a loop of single creates can walk past is not a
limit. The number itself is configuration (``MAX_PROFILES``, default 150 --
the size this product is built and measured for), not a constant to be found
by reading the source.
"""
from __future__ import annotations

import pytest

from multiprofile.config import max_profiles
from multiprofile.storage import db


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


@pytest.fixture
def client():
    from multiprofile.api.server import create_app

    app = create_app()
    return app.test_client()


@pytest.fixture
def template(client):
    from multiprofile.storage.templates import load_templates

    client.post("/api/templates", json={"name": "Fleet", "payload": {"os": "windows"}})
    return load_templates()[0]["id"]


# --------------------------------------------------------------------------
# 1. The truncation itself
# --------------------------------------------------------------------------

def test_a_batch_larger_than_fifty_is_not_quietly_cut_to_fifty(client, template, monkeypatch):
    """The original bug: 150 requested, 50 created, HTTP 200, no mention."""
    monkeypatch.setenv("MAX_PROFILES", "400")
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 120})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["count"] == 120
    assert len(body["profiles"]) == 120


def test_a_batch_that_will_not_fit_is_refused_with_the_numbers(client, template):
    """409 and enough detail to act: how many are stored, how many fit."""
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 500})
    assert r.status_code == 409
    err = r.get_json()["error"]
    assert err["code"] == "fleet_full"
    assert err["capacity"] == max_profiles()
    assert err["requested"] == 500
    assert err["free"] == err["capacity"] - err["stored"]
    # The message has to name the numbers; "fleet is full" alone does not tell
    # an operator whether to delete two profiles or fifty.
    assert str(err["capacity"]) in err["message"]
    assert str(err["free"]) in err["message"]


def test_a_refused_batch_stores_nothing(client, template):
    from multiprofile.storage.profiles import fleet_capacity

    before = fleet_capacity()["stored"]  # also primes the lazily seeded store
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 500})
    assert r.status_code == 409
    assert db.count_profiles() == before


# --------------------------------------------------------------------------
# 2. A count that is not a count
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["abc", -5, 0, 1.5, True, [3]])
def test_a_count_that_is_not_a_positive_integer_is_rejected(client, template, bad):
    """These all silently became 1 before."""
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": bad})
    assert r.status_code == 400, f"{bad!r} -> {r.status_code} {r.get_json()}"
    assert r.get_json()["error"]["field"] == "count"


def test_an_absurd_count_is_rejected_as_input_not_attempted(client, template):
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 10**9})
    assert r.status_code == 400
    assert r.get_json()["error"]["field"] == "count"


def test_no_count_still_means_one(client, template):
    r = client.post("/api/profiles/from-template", json={"templateId": template})
    assert r.status_code == 200
    assert r.get_json()["count"] == 1


# --------------------------------------------------------------------------
# 3. What a batch produces
# --------------------------------------------------------------------------

def test_every_profile_in_a_batch_is_its_own_identity(client, template):
    """A fleet of clones is not a fleet: ids, names and seeds must differ."""
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 40})
    profiles = r.get_json()["profiles"]
    assert len({p["id"] for p in profiles}) == 40
    assert len({p["name"] for p in profiles}) == 40
    assert len({p["seed"] for p in profiles}) == 40


def test_a_batch_reports_the_fleet_it_left_behind(client, template):
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 10})
    fleet = r.get_json()["fleet"]
    assert fleet["stored"] == db.count_profiles()
    assert fleet["free"] == fleet["capacity"] - fleet["stored"]


def test_the_batch_is_one_transaction_not_a_loop_of_writes():
    """The distinction that makes all-or-nothing possible.

    Asserted on the store rather than through the API, because it is a
    property of the write: a batch that raises must leave the table untouched.
    """
    from multiprofile.storage.profiles import create_profiles, fleet_capacity

    before = fleet_capacity()["stored"]  # also primes the lazily seeded store
    with pytest.raises(db.FleetFull):
        create_profiles([{"name": f"p{i}"} for i in range(max_profiles() + 5)])
    assert db.count_profiles() == before


# --------------------------------------------------------------------------
# 4. The cap is enforced where it cannot be walked past
# --------------------------------------------------------------------------

def test_single_creates_cannot_walk_past_the_cap(client, monkeypatch):
    """A cap only the bulk endpoint honours is not a cap."""
    monkeypatch.setenv("MAX_PROFILES", "5")
    stored = db.count_profiles()
    codes = []
    for i in range(10):
        codes.append(client.post("/api/profiles", json={"name": f"one-{i}"}).status_code)
    assert db.count_profiles() == 5, codes
    assert 409 in codes
    assert stored <= 5


def test_the_cap_is_configuration_not_a_constant(client, template, monkeypatch):
    monkeypatch.setenv("MAX_PROFILES", "12")
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 30})
    assert r.status_code == 409
    assert r.get_json()["error"]["capacity"] == 12
    r = client.post("/api/profiles/from-template", json={"templateId": template, "count": 5})
    assert r.status_code == 200


def test_capacity_can_be_asked_before_a_batch_is_submitted(client, template):
    """So the console can say "room for 12 more" while a number is chosen."""
    fleet = client.get("/api/profiles/capacity").get_json()["fleet"]
    assert fleet["capacity"] == max_profiles()
    free = fleet["free"]
    client.post("/api/profiles/from-template", json={"templateId": template, "count": 7})
    after = client.get("/api/profiles/capacity").get_json()["fleet"]
    assert after["free"] == free - 7


# --------------------------------------------------------------------------
# 5. Import shares the same ceiling
# --------------------------------------------------------------------------

def test_an_import_larger_than_the_free_space_reports_the_rows_it_could_not_take(monkeypatch):
    """Not a 500, and not one rejected transaction per row past the limit."""
    monkeypatch.setenv("MAX_PROFILES", "10")
    from multiprofile.profile_import import import_profiles

    lines = "\n".join(f"10.0.0.{i}:8080" for i in range(1, 31))
    report = import_profiles(lines)
    assert db.count_profiles() == 10
    full = [f for f in report["failed"] if f["error"] == "fleet_full"]
    assert len(full) == 30 - len(report["created"])
    # By line, so the operator knows which part of their paste to keep.
    assert all(isinstance(f["line"], int) for f in full)
    assert report["fleet"]["free"] == 0
