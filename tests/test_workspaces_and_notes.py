"""Workspaces (clients with operating defaults) and the per-profile note log."""
from __future__ import annotations

import pytest

from multiprofile.storage import clients, notes, pools
from multiprofile.storage.profiles import create_profile, get_profile


@pytest.fixture()
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


# ---------------------------------------------------------------------------
# workspaces
# ---------------------------------------------------------------------------


def test_a_workspace_carries_a_default_pool_and_tags(xdg):
    pool = pools.create_pool({"label": "US", "members": []})
    ws = clients.create_client({
        "name": "Acme", "defaultPoolId": pool["id"], "tags": ["shop", "ads"],
    })

    assert ws["defaultPoolId"] == pool["id"]
    assert ws["tags"] == ["shop", "ads"]
    assert clients.workspace_default_pool(ws["id"]) == pool["id"]


def test_workspace_fields_are_optional(xdg):
    ws = clients.create_client({"name": "Plain"})
    assert ws["defaultPoolId"] is None
    assert ws["tags"] == []
    assert clients.workspace_default_pool(ws["id"]) is None


def test_tags_are_cleaned(xdg):
    ws = clients.create_client({"name": "W", "tags": ["  keep ", "", "   ", "also"]})
    assert ws["tags"] == ["keep", "also"]


def test_a_pre_existing_client_reads_as_a_workspace(xdg):
    """clients.json written before this change must still load."""
    clients.save_clients([{"id": "client_old", "name": "Legacy", "color": "#fff"}])

    [ws] = clients.load_workspaces()

    assert ws["name"] == "Legacy"
    assert ws["defaultPoolId"] is None
    assert ws["tags"] == []


def test_updating_a_workspace_keeps_unmentioned_fields(xdg):
    pool = pools.create_pool({"label": "US", "members": []})
    ws = clients.create_client({"name": "Acme", "defaultPoolId": pool["id"], "tags": ["a"]})

    updated = clients.update_client(ws["id"], {"name": "Acme Ltd"})

    assert updated["name"] == "Acme Ltd"
    assert updated["defaultPoolId"] == pool["id"]
    assert updated["tags"] == ["a"]


def test_a_workspace_pool_can_be_cleared(xdg):
    pool = pools.create_pool({"label": "US", "members": []})
    ws = clients.create_client({"name": "Acme", "defaultPoolId": pool["id"]})

    updated = clients.update_client(ws["id"], {"defaultPoolId": None})

    assert updated["defaultPoolId"] is None


def test_deleting_a_workspace_detaches_profiles_without_deleting_them(xdg):
    ws = clients.create_client({"name": "Acme"})
    p = create_profile({"name": "In workspace", "clientId": ws["id"]})

    assert clients.delete_client(ws["id"]) is True

    survivor = get_profile(p["id"])
    assert survivor is not None
    assert survivor["clientId"] is None


def test_unknown_workspace_has_no_default_pool(xdg):
    assert clients.workspace_default_pool("client_nope") is None


# ---------------------------------------------------------------------------
# note log
# ---------------------------------------------------------------------------


def test_notes_are_a_log_newest_first(xdg):
    p = create_profile({"name": "Noted"})
    notes.add_note(p["id"], "first")
    notes.add_note(p["id"], "second")
    notes.add_note(p["id"], "third")

    log = notes.list_notes(p["id"])

    assert [e["text"] for e in log] == ["third", "second", "first"]
    assert all(e["at"] and e["id"] for e in log)


def test_each_entry_is_timestamped_and_typed(xdg):
    p = create_profile({"name": "Noted"})
    entry = notes.add_note(p["id"], "proxy swapped", kind="network")

    assert entry["kind"] == "network"
    assert entry["at"].endswith("Z")
    assert entry["id"].startswith("note_")


def test_the_flat_summary_still_works_alongside_the_log(xdg):
    """Nothing that reads `notes` has to change."""
    p = create_profile({"name": "Noted", "notes": "watch for captchas"})
    notes.add_note(p["id"], "an entry")

    profile = get_profile(p["id"])

    assert profile["notes"] == "watch for captchas"
    assert notes.note_count(profile) == 1


def test_setting_the_summary_leaves_the_log_alone(xdg):
    p = create_profile({"name": "Noted"})
    notes.add_note(p["id"], "kept")

    notes.set_summary(p["id"], "new summary")

    assert get_profile(p["id"])["notes"] == "new summary"
    assert [e["text"] for e in notes.list_notes(p["id"])] == ["kept"]


def test_notes_survive_a_profile_update(xdg):
    from multiprofile.storage.profiles import update_profile

    p = create_profile({"name": "Noted"})
    notes.add_note(p["id"], "survive me")
    update_profile(p["id"], {"name": "Renamed"})

    assert [e["text"] for e in notes.list_notes(p["id"])] == ["survive me"]


def test_an_entry_can_be_deleted(xdg):
    p = create_profile({"name": "Noted"})
    a = notes.add_note(p["id"], "keep")
    b = notes.add_note(p["id"], "remove")

    assert notes.delete_note(p["id"], b["id"]) is True
    assert [e["id"] for e in notes.list_notes(p["id"])] == [a["id"]]
    assert notes.delete_note(p["id"], b["id"]) is False


def test_empty_notes_are_refused(xdg):
    p = create_profile({"name": "Noted"})
    for bad in ("", "   ", "\n"):
        with pytest.raises(ValueError):
            notes.add_note(p["id"], bad)


def test_an_overlong_note_is_refused(xdg):
    p = create_profile({"name": "Noted"})
    with pytest.raises(ValueError):
        notes.add_note(p["id"], "x" * (notes.MAX_ENTRY_LEN + 1))


def test_the_log_is_bounded(xdg):
    """A runaway writer must not grow profiles.json without limit."""
    p = create_profile({"name": "Noted"})
    for i in range(notes.MAX_ENTRIES + 25):
        notes.add_note(p["id"], f"entry {i}")

    log = notes.list_notes(p["id"])

    assert len(log) == notes.MAX_ENTRIES
    assert log[0]["text"] == f"entry {notes.MAX_ENTRIES + 24}"  # newest kept


def test_notes_on_a_missing_profile(xdg):
    assert notes.add_note("nope", "text") is None
    assert notes.list_notes("nope") == []
    assert notes.delete_note("nope", "note_x") is False


def test_a_profile_with_no_notes_reads_as_empty(xdg):
    p = create_profile({"name": "Quiet"})
    assert notes.list_notes(p["id"]) == []
    assert notes.note_count(get_profile(p["id"])) == 0
