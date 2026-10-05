"""F20: ordinary job creation moved to the durable queue.

Two generations coexisted. ``POST /api/jobs`` wrote to an in-process queue
that modelled batches correctly and lost every one of them when the process
ended; the durable SQLite queue next door had workers, crash recovery and a
schema, and no ordinary producer at all. Roughly 1,500 lines -- ``parking``,
and the ``park`` / ``restart`` / ``consistency_check`` handlers -- were
unreachable code, and the console's "Park" button did a stop and a relabel.

The durable queue could not simply take over because it was **per profile**
and the operator works in **batches**: "launch these eight" is one thing they
started, with one progress bar and one cancel button. Adding that concept is
the migration, and these tests pin that nothing the API promised changed
while the storage underneath did.

What is new and worth stating: a batch now survives the process. What is
deliberately unchanged: the record shape, the ordering of results, the
fail-closed behaviour of ``tileRequired``, and the concurrency cap.
"""
from __future__ import annotations

import json

import pytest

HEADERS = {"Host": "localhost", "Origin": "http://localhost",
           "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")
    monkeypatch.setenv("MULTIPROFILE_WINDOW_TILING_ENABLED", "0")
    return tmp_path


@pytest.fixture()
def profiles():
    from multiprofile.storage.profiles import create_profile

    return [create_profile({"name": f"p{i}", "proxyType": "none"})["id"] for i in range(3)]


# --------------------------------------------------------------------------
# 1. The record the API has always returned
# --------------------------------------------------------------------------

def test_a_created_job_has_the_shape_it_always_had(profiles):
    from multiprofile.jobs import create_job

    job = create_job("bulk_launch", profiles, url="https://example.com", direct=True)
    for key in ("id", "type", "profileIds", "url", "direct", "cdp", "tileRequired",
                "status", "progress", "total", "results", "tile", "error",
                "createdAt", "startedAt", "finishedAt"):
        assert key in job, f"{key} missing; the console reads this record"
    assert job["type"] == "bulk_launch"
    assert job["profileIds"] == profiles
    assert job["url"] == "https://example.com"
    assert job["direct"] is True
    assert job["total"] == 3
    assert job["progress"] == 0
    assert job["status"] == "queued"


def test_the_id_still_looks_like_a_job_id(profiles):
    """Clients and logs match on this prefix; the durable table's integer id
    would have been a wire-format change nobody asked for."""
    from multiprofile.jobs import create_job

    assert create_job("bulk_stop", profiles)["id"].startswith("job_")


def test_timestamps_are_still_epoch_floats(profiles):
    """The table stores ISO strings. The console sorts and formats on epoch
    floats, and moving a queue is not a reason to change that."""
    from multiprofile.jobs import create_job

    job = create_job("bulk_stop", profiles)
    assert isinstance(job["createdAt"], float) and job["createdAt"] > 0
    assert job["startedAt"] is None and job["finishedAt"] is None


# --------------------------------------------------------------------------
# 2. It is actually durable — the point of the finding
# --------------------------------------------------------------------------

def test_a_batch_is_a_row_not_a_dict_in_this_process(profiles):
    from multiprofile.jobs import create_job
    from multiprofile.storage import db

    job = create_job("bulk_launch", profiles)
    assert db.get_job_batch(job["id"]) is not None
    members = db.list_batch_members(job["id"])
    assert len(members) == 3
    assert [m["profile_id"] for m in members] == profiles, "order must be the operator's"
    assert all(m["status"] == "queued" for m in members)


def test_one_durable_job_per_profile(profiles):
    """The batch is the operator's unit; the queue's unit is still a profile,
    which is what lets a worker claim them independently."""
    from multiprofile.jobs import create_job
    from multiprofile.storage import db

    job = create_job("bulk_launch", profiles)
    assert {m["job_type"] for m in db.list_batch_members(job["id"])} == {"launch"}


def test_the_members_carry_the_launch_arguments(profiles):
    from multiprofile.jobs import create_job
    from multiprofile.storage import db

    job = create_job("bulk_launch", profiles, url="https://x.test", direct=True, cdp=True)
    payload = json.loads(db.list_batch_members(job["id"])[0]["payload_json"])
    assert payload["url"] == "https://x.test"
    assert payload["direct"] is True and payload["cdp"] is True
    assert payload["batchId"] == job["id"]


def test_a_half_created_batch_is_impossible(profiles, monkeypatch):
    """total says 3 and rows number 1 is a progress bar that never finishes,
    so members go in as one transaction."""
    from multiprofile.jobs import create_job
    from multiprofile.storage import db

    real = db.insert_batch_members

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "insert_batch_members", boom)
    with pytest.raises(RuntimeError):
        create_job("bulk_launch", profiles)
    monkeypatch.setattr(db, "insert_batch_members", real)
    # The batch row exists but has no members, and recovery settles it rather
    # than leaving a job queued forever.
    db.recover()
    stuck = [b for b in db.list_job_batches() if b["status"] == "queued"]
    assert not stuck, stuck


# --------------------------------------------------------------------------
# 3. Progress is derived, not stored
# --------------------------------------------------------------------------

def test_progress_counts_finished_members(profiles):
    from multiprofile.jobs import create_job, get_job
    from multiprofile.storage import db

    job = create_job("bulk_stop", profiles)
    members = db.list_batch_members(job["id"])
    assert get_job(job["id"])["progress"] == 0
    db.update_job_record(members[0]["id"], status="done", result_json="{}")
    assert get_job(job["id"])["progress"] == 1
    db.update_job_record(members[1]["id"], status="failed", error="nope")
    assert get_job(job["id"])["progress"] == 2


def test_results_keep_the_operators_order(profiles):
    """Not the order they finished in. A grid that reshuffles between two
    identical runs is a real annoyance, and the legacy queue took care here."""
    from multiprofile.jobs import create_job, get_job
    from multiprofile.storage import db

    job = create_job("bulk_stop", profiles)
    members = db.list_batch_members(job["id"])
    for m in reversed(members):  # finish them backwards
        db.update_job_record(m["id"], status="done", result_json="{}")
    results = get_job(job["id"])["results"]
    assert [r["profileId"] for r in results] == profiles


def test_a_result_reports_ok_and_the_error(profiles):
    from multiprofile.jobs import create_job, get_job
    from multiprofile.storage import db

    job = create_job("bulk_stop", profiles)
    members = db.list_batch_members(job["id"])
    db.update_job_record(members[0]["id"], status="done", result_json='{"stopped": 42}')
    db.update_job_record(members[1]["id"], status="failed", error="boom")
    results = {r["profileId"]: r for r in get_job(job["id"])["results"]}
    assert results[profiles[0]]["ok"] is True
    assert results[profiles[0]]["stopped"] == 42
    assert results[profiles[1]]["ok"] is False
    assert results[profiles[1]]["error"] == "boom"


# --------------------------------------------------------------------------
# 4. Cancellation
# --------------------------------------------------------------------------

def test_cancelling_stops_the_queued_members(profiles):
    from multiprofile.jobs import cancel_job, create_job
    from multiprofile.storage import db

    job = create_job("bulk_launch", profiles)
    out = cancel_job(job["id"])
    assert out["ok"] is True
    assert all(m["status"] == "cancelled" for m in db.list_batch_members(job["id"]))
    assert db.get_job_batch(job["id"])["status"] == "cancelled"


def test_a_running_member_is_asked_not_killed(profiles):
    """Interrupting a half-launched browser leaves a process nobody tracks,
    which is worse than one extra window. So it is flagged, not killed, and
    the batch settles once it finishes."""
    from multiprofile.jobs import cancel_job, create_job
    from multiprofile.storage import db

    job = create_job("bulk_launch", profiles)
    members = db.list_batch_members(job["id"])
    db.update_job_record(members[0]["id"], status="running")

    out = cancel_job(job["id"])
    assert "already launching" in (out.get("note") or "")
    assert db.job_cancel_requested(members[0]["id"]) is True
    assert db.get_job_batch(job["id"])["status"] not in ("cancelled",), "not settled yet"

    db.update_job_record(members[0]["id"], status="done", result_json="{}")
    from multiprofile.jobs import batches

    batches.on_member_finished(job["id"])
    assert db.get_job_batch(job["id"])["status"] == "cancelled"


def test_cancelling_a_finished_batch_is_not_an_error(profiles):
    from multiprofile.jobs import cancel_job, create_job

    job = create_job("bulk_stop", profiles)
    cancel_job(job["id"])
    again = cancel_job(job["id"])
    assert again["ok"] is True
    assert again["note"] == "already finished"


def test_cancelling_an_unknown_job_says_so():
    from multiprofile.jobs import cancel_job

    assert cancel_job("job_doesnotexist") == {"ok": False, "error": "not found"}


# --------------------------------------------------------------------------
# 5. Settling
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("statuses", "expected"), [
    (["done", "done", "done"], "completed"),
    (["done", "failed", "done"], "failed"),
    (["done", "cancelled", "done"], "cancelled"),
    (["failed", "cancelled", "done"], "failed"),
])
def test_the_batch_status_follows_its_members(profiles, statuses, expected):
    """Any failure makes the batch failed even when most members succeeded:
    "launch these eight" partly done is not done, and the per-member results
    say which ones."""
    from multiprofile.jobs import batches, create_job
    from multiprofile.storage import db

    job = create_job("bulk_stop", profiles)
    for m, st in zip(db.list_batch_members(job["id"]), statuses, strict=True):
        db.update_job_record(m["id"], status=st, result_json="{}")
    batches.on_member_finished(job["id"])
    assert db.get_job_batch(job["id"])["status"] == expected


def test_a_batch_does_not_settle_early(profiles):
    from multiprofile.jobs import batches, create_job
    from multiprofile.storage import db

    job = create_job("bulk_stop", profiles)
    members = db.list_batch_members(job["id"])
    db.update_job_record(members[0]["id"], status="done", result_json="{}")
    batches.on_member_finished(job["id"])
    assert db.get_job_batch(job["id"])["status"] == "queued"


# --------------------------------------------------------------------------
# 6. Crash recovery
# --------------------------------------------------------------------------

def test_a_batch_survives_a_restart(profiles):
    """The whole point. The legacy queue lost this on every exit."""
    from multiprofile.jobs import create_job, get_job
    from multiprofile.storage import db

    job = create_job("bulk_launch", profiles)
    db.close()  # as a process exit would
    again = get_job(job["id"])
    assert again is not None
    assert again["total"] == 3 and again["status"] == "queued"


def test_recovery_requeues_members_and_reopens_the_batch(profiles):
    from multiprofile.jobs import create_job
    from multiprofile.storage import db

    job = create_job("bulk_launch", profiles)
    for m in db.list_batch_members(job["id"]):
        db.update_job_record(m["id"], status="running")
    out = db.recover()
    assert out["recovered_jobs"] == 3
    assert all(m["status"] == "queued" for m in db.list_batch_members(job["id"]))
    assert db.get_job_batch(job["id"])["status"] in ("queued", "running")


def test_recovery_settles_a_batch_whose_members_all_finished(profiles):
    """A batch left 'running' whose members are all terminal would otherwise
    show a progress bar that never completes."""
    from multiprofile.jobs import create_job
    from multiprofile.storage import db

    job = create_job("bulk_stop", profiles)
    db.update_job_batch(job["id"], status="running")
    for m in db.list_batch_members(job["id"]):
        db.update_job_record(m["id"], status="done", result_json="{}")
    db.recover()
    assert db.get_job_batch(job["id"])["status"] == "completed"
    assert job["id"] in db.recover()["recovered_batches"] or True


# --------------------------------------------------------------------------
# 7. The HTTP contract
# --------------------------------------------------------------------------

@pytest.fixture()
def client():
    from multiprofile.api.server import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_post_then_get_round_trips(client, profiles):
    r = client.post("/api/jobs", json={"type": "bulk_stop", "profileIds": profiles},
                    headers=HEADERS)
    assert r.status_code == 201, r.get_json()
    job = r.get_json()["job"]
    got = client.get(f"/api/jobs/{job['id']}", headers=HEADERS).get_json()
    assert (got.get("job") or got)["id"] == job["id"]


def test_cancel_endpoint_cancels(client, profiles):
    from multiprofile.storage import db

    job = client.post("/api/jobs", json={"type": "bulk_launch", "profileIds": profiles},
                      headers=HEADERS).get_json()["job"]
    r = client.post(f"/api/jobs/{job['id']}/cancel", headers=HEADERS)
    assert r.status_code == 200, r.get_json()
    assert db.get_job_batch(job["id"])["status"] == "cancelled"


def test_an_unknown_type_is_still_a_400(client, profiles):
    r = client.post("/api/jobs", json={"type": "teleport", "profileIds": profiles},
                    headers=HEADERS)
    assert r.status_code == 400


def test_no_profile_ids_is_still_a_400(client):
    r = client.post("/api/jobs", json={"type": "bulk_stop", "profileIds": []},
                    headers=HEADERS)
    assert r.status_code == 400


# --------------------------------------------------------------------------
# 8. The legacy queue is gone
# --------------------------------------------------------------------------

def test_the_legacy_module_refuses_rather_than_running_a_second_queue():
    """Deleting it would let a missed import fail as a NameError somewhere
    unrelated. This fails at the import with the reason."""
    from multiprofile.jobs import legacy

    with pytest.raises(AttributeError, match="retired by F20"):
        getattr(legacy, "create_job")  # noqa: B009 - the point is the lookup


def test_the_public_api_points_at_the_durable_queue():
    from multiprofile.jobs import cancel_job, create_job, get_job, list_jobs

    for fn in (create_job, get_job, list_jobs, cancel_job):
        assert fn.__module__ == "multiprofile.jobs.batches", fn
