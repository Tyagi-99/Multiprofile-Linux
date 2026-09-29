"""One profile, one request.

There was no ``GET /api/profiles/<id>``. The console's detail page fetched
``/api/profiles?limit=500`` and searched the page for the id it wanted -- but
the list clamps ``limit``, so past the clamp the console could not open a
profile at all. Measured with 150 profiles, against the 100 cap in force at
the time::

    asked for 500, got 100 of total 150
    profile #10  findable: True
    profile #149 findable: False

Nothing reported the truncation, which is why it survived: the request
succeeded, the page rendered, and the answer was "not found" for a profile
sitting in the store. It would have bitten this product at its stated design
size of 150 profiles.
"""
from __future__ import annotations

import pytest

from multiprofile.api.server import PROFILE_LIST_MAX_LIMIT
from multiprofile.app import create_app
from multiprofile.fingerprint import build_fingerprint
from multiprofile.storage import db


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")


@pytest.fixture()
def client():
    return create_app().test_client()


def _bulk(n: int):
    rows = []
    for i in range(n):
        row = {"id": f"profile_{i:06x}", "name": f"Profile {i}", "proxyType": "none",
               "seed": i, "tags": [], "notes": "", "status": "idle",
               "createdAt": "2025-01-01"}
        row["fingerprint"] = build_fingerprint(row)
        rows.append(row)
    db.replace_all_profiles(rows)
    return rows


def test_a_profile_beyond_one_page_can_still_be_opened(client):
    """The measurement in the module docstring, as an assertion."""
    _bulk(PROFILE_LIST_MAX_LIMIT + 50)
    last = f"profile_{PROFILE_LIST_MAX_LIMIT + 49:06x}"
    resp = client.get(f"/api/profiles/{last}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["id"] == last


def test_a_missing_profile_is_a_404_not_an_empty_success(client):
    _bulk(3)
    resp = client.get("/api/profiles/profile_nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_the_detail_keeps_the_fingerprint(client):
    """Unlike a list row. The thing asking for one profile is the thing that
    wants its detail."""
    _bulk(2)
    body = client.get("/api/profiles/profile_000000").get_json()
    assert "fingerprint" in body
    assert body["fingerprint"]


def test_the_detail_carries_the_runtime_projection(client):
    _bulk(2)
    body = client.get("/api/profiles/profile_000000").get_json()
    for key in ("status", "isRunning", "launchState"):
        assert key in body, key


def test_the_ids_route_is_not_shadowed_by_the_detail_route(client):
    """/api/profiles/ids must not be read as a profile called "ids"."""
    _bulk(3)
    resp = client.get("/api/profiles/ids")
    assert resp.status_code == 200
    assert "ids" in resp.get_json()


# --------------------------------------------------------------------------
# The list projection
# --------------------------------------------------------------------------

def test_a_list_row_does_not_carry_its_fingerprint(client):
    """2,114 bytes of a 2,795-byte row, and the table reads none of it.

    The inspector fetches /api/profiles/<id>/fingerprint when an operator
    opens one, so sending it with every row was pure weight.
    """
    _bulk(3)
    rows = client.get("/api/profiles?limit=10").get_json()["items"]
    assert rows
    for row in rows:
        assert "fingerprint" not in row


def test_a_list_row_still_carries_what_the_table_shows(client):
    _bulk(3)
    row = client.get("/api/profiles?limit=10").get_json()["items"][0]
    for key in ("id", "name", "status", "proxyType", "tags", "createdAt"):
        assert key in row, key


def test_the_list_row_got_materially_smaller(client):
    """Guards the reason the page size could be raised."""
    import json

    _bulk(3)
    row = client.get("/api/profiles?limit=10").get_json()["items"][0]
    assert len(json.dumps(row)) < 1200, len(json.dumps(row))


def test_the_whole_fleet_fits_in_one_page_at_the_design_size(client):
    """This product is designed for 150 profiles. At that size the operator
    should never meet a pagination control."""
    _bulk(150)
    body = client.get("/api/profiles?limit=200").get_json()
    assert len(body["items"]) == 150
    assert body["total"] == 150
    assert body["next_cursor"] is None
