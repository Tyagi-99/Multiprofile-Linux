"""F20: every job is visible, and there is only one queue to be visible in.

Written when the finding was half done -- ``GET /api/jobs`` listed both
queues while ``POST`` still wrote to the in-process one -- and kept, with the
two tests that pinned the interim state rewritten to pin the finished one.
The rest are unchanged, because what they assert never depended on which
queue was underneath: a job that exists must be listed, ids must not collide,
and the listing must survive one source failing.

The migration itself is in :mod:`multiprofile.jobs.batches`. What these tests
still guard is the property the finding was about: **a job that exists must be
visible to a client.** A durable job with no batch -- parking, a health check,
anything crash recovery re-queued -- is listed alongside the batches, and a
batch's members are not listed separately, or every bulk launch would appear
N+1 times.
"""
from __future__ import annotations

import pytest

HEADERS = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from multiprofile.api.server import create_app

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def profile(client):
    from multiprofile.storage.profiles import create_profile

    return create_profile({"name": "j", "proxyType": "none"})


def _items(client):
    body = client.get("/api/jobs", headers=HEADERS).get_json()
    return body["items"]


def test_a_durable_job_is_listed(client, profile):
    """Before this, the only client of the durable queue was a worker."""
    from multiprofile.jobs import enqueue

    enqueue("health_check", profile["id"], {})
    rows = _items(client)
    assert any(r.get("source") == "durable" for r in rows), rows


def test_batches_and_background_jobs_appear_together(client, profile):
    """The finding, restated for one queue: a job that exists is listed,
    whether the operator started it or the product did."""
    from multiprofile.jobs import enqueue

    enqueue("health_check", profile["id"], {})
    r = client.post(
        "/api/jobs", json={"type": "stop_profile", "profileIds": [profile["id"]]},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.get_json()
    rows = _items(client)
    batches = [x for x in rows if not str(x["id"]).startswith("durable:")]
    background = [x for x in rows if str(x["id"]).startswith("durable:")]
    assert batches, "the operator's batch is missing"
    assert background, "the background health_check is missing"
    # Both carry the same source now, because there is one store.
    assert {x.get("source") for x in rows} == {"durable"}


def test_a_durable_job_is_normalised_into_the_console_shape(client, profile):
    """The console reads `type`/`status`/`createdAt`; the table has none of them."""
    from multiprofile.jobs import enqueue

    # Select the row this test made: the control DB caches its path, so rows
    # from earlier tests in the same process are still listed.
    job_id = enqueue("consistency_check", profile["id"], {})
    row = next(r for r in _items(client) if r.get("id") == f"durable:{job_id}")
    assert row["source"] == "durable"
    assert row["type"] == "consistency_check"
    assert row["status"] == "queued"
    assert isinstance(row["createdAt"], (int, float))
    assert row["profileIds"] == [profile["id"]]


def test_durable_ids_cannot_collide_with_legacy_ids(client, profile):
    """The jobs table numbers from 1; a bare integer id would be ambiguous."""
    from multiprofile.jobs import enqueue

    enqueue("health_check", profile["id"], {})
    row = next(r for r in _items(client) if r.get("source") == "durable")
    assert str(row["id"]).startswith("durable:")

    ids = [str(r["id"]) for r in _items(client)]
    assert len(ids) == len(set(ids))


def test_the_listing_is_sortable_across_both_queues(client, profile):
    """Pagination keys on (createdAt, id); an unparsed ISO date would sort to 0."""
    from multiprofile.jobs import enqueue

    enqueue("health_check", profile["id"], {})
    client.post("/api/jobs", json={"type": "stop_profile", "profileIds": [profile["id"]]},
                headers=HEADERS)
    rows = _items(client)
    assert all(isinstance(r.get("createdAt"), (int, float)) for r in rows), rows
    assert all(float(r["createdAt"]) > 0 for r in rows), rows


def test_posting_creates_a_durable_batch(client, profile):
    """The finding's larger half: the writer moved. A job created through the
    API is now a row in the control database, not a dict in this process."""
    from multiprofile.storage import db

    r = client.post(
        "/api/jobs", json={"type": "bulk_stop", "profileIds": [profile["id"]]},
        headers=HEADERS,
    )
    assert r.status_code == 201
    created = r.get_json()["job"]
    assert db.get_job_batch(created["id"]) is not None, "the batch must be persisted"
    assert len(db.list_batch_members(created["id"])) == 1


def test_a_batch_is_listed_once_not_once_per_member(client, profile):
    """Members live in the same table as background jobs. Listing both without
    excluding members would show every bulk launch N+1 times."""
    client.post("/api/jobs",
                json={"type": "bulk_stop", "profileIds": [profile["id"]] * 1},
                headers=HEADERS)
    rows = _items(client)
    assert len([r for r in rows if not str(r["id"]).startswith("durable:")]) == 1


def test_background_jobs_failing_does_not_hide_the_batches(client, profile, monkeypatch):
    """The two reads are of the same table but not the same query. One
    raising must not blank the operator's jobs page."""
    import multiprofile.jobs as jobs_pkg

    client.post("/api/jobs", json={"type": "stop_profile", "profileIds": [profile["id"]]},
                headers=HEADERS)

    def boom(*a, **k):
        raise RuntimeError("control DB unavailable")

    monkeypatch.setattr(jobs_pkg, "list_durable_jobs", boom)
    rows = _items(client)
    assert rows, "the batch must still be listed"
    assert all(not str(r["id"]).startswith("durable:") for r in rows)
