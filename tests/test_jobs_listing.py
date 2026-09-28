"""F20 (part one): a durable job must not be invisible.

Two job generations coexist. ``POST /api/jobs`` writes to the legacy
in-process queue; the durable SQLite queue starts its workers at boot and
recovers on crash, but nothing in the API enqueues into it. ``GET /api/jobs``
listed only the legacy one, so a durable job that *did* exist -- parking can
create one, and crash recovery re-queues into it -- could not be seen by any
client at all.

Listing both is the smaller half of the finding and is what these tests pin.
Moving the writer is the larger half: bulk API types map to N durable jobs,
cancel semantics differ, and the console's jobs page changes shape. That is
filed, not done here, so ``POST`` deliberately still goes to legacy.

The two shapes were never reconciled either -- legacy uses ``type`` and an
epoch ``createdAt`` with a string id, the jobs table uses ``job_type``, ISO
``created_at`` and an integer id -- so the durable rows are normalised into the
shape the console already reads, with a prefixed id so the two can never
collide.
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


def test_both_queues_appear_together(client, profile):
    from multiprofile.jobs import enqueue

    enqueue("health_check", profile["id"], {})
    r = client.post(
        "/api/jobs", json={"type": "stop_profile", "profileIds": [profile["id"]]},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.get_json()
    assert {row.get("source") for row in _items(client)} == {"legacy", "durable"}


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


def test_posting_still_targets_the_legacy_queue(client, profile):
    """Deliberate: moving the writer is the larger half of F20 and is filed."""
    r = client.post(
        "/api/jobs", json={"type": "bulk_stop", "profileIds": [profile["id"]]},
        headers=HEADERS,
    )
    assert r.status_code == 201
    created = r.get_json()["job"]
    assert not str(created["id"]).startswith("durable:")


def test_one_queue_failing_does_not_hide_the_other(client, profile, monkeypatch):
    """A missing control DB must not blank the jobs page."""
    import multiprofile.jobs as jobs_pkg

    client.post("/api/jobs", json={"type": "stop_profile", "profileIds": [profile["id"]]},
                headers=HEADERS)

    def boom(*a, **k):
        raise RuntimeError("control DB unavailable")

    monkeypatch.setattr(jobs_pkg, "list_durable_jobs", boom)
    rows = _items(client)
    assert any(r.get("source") == "legacy" for r in rows), rows
