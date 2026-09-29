"""Listing profiles must not get slower than linearly in the store.

The list endpoint used to collect every matching profile into Python and slice
the page out of it -- and the collector walked in chunks of 500 while the
function it called re-read and re-decorated the whole table on every chunk. So
serving 25 rows out of 10,000 profiles materialised the store twenty times.
Measured before the change::

     2,000 profiles   first page      246 ms
    10,000 profiles   first page    5,006 ms
    10,000 profiles   filter by tag 2,628 ms

Five times the data for twenty times the work, which is what doing the whole
job once per chunk of the scan looks like. Warm, at 10,000 profiles, after::

    no filter    4,561 ms -> 0.5 ms
    tag filter   1,568 ms -> 24.5 ms

These tests hold two things. The results must be *identical* to what the
Python predicates returned -- a filter that quietly means something different
once it runs in SQL is worse than a slow one -- and the work must not grow
with the size of the store when a page is asked for.
"""
from __future__ import annotations

import time

import pytest

from multiprofile.storage import db
from multiprofile.storage.profiles import filter_profiles, query_page


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    return tmp_path


def _bulk(n: int, **over):
    """Insert directly: this is about querying, not creating."""
    rows = []
    for i in range(n):
        row = {
            "id": f"profile_{i:06x}", "name": f"Profile {i}", "proxyType": "none",
            "seed": i, "tags": ["even" if i % 2 == 0 else "odd"],
            "notes": f"note {i}", "status": "idle", "createdAt": "2025-01-01",
            "clientId": f"client{i % 10}", "userAgent": "Mozilla/5.0 test",
        }
        row.update(over)
        rows.append(row)
    db.replace_all_profiles(rows)
    return rows


# --------------------------------------------------------------------------
# 1. The SQL predicates must mean exactly what the Python ones meant
# --------------------------------------------------------------------------

def _python_filter(rows, *, q=None, client_id=None, tag=None):
    """The predicate as it was written in Python, kept here as the oracle."""
    out = rows
    if q:
        ql = q.lower().strip()
        out = [p for p in out
               if ql in (p.get("name") or "").lower()
               or ql in (p.get("notes") or "").lower()
               or ql in (p.get("userAgent") or "").lower()
               or any(ql in str(t).lower() for t in (p.get("tags") or []))]
    if client_id:
        out = [p for p in out if p.get("clientId") == client_id]
    if tag:
        tl = tag.lower().strip()
        out = [p for p in out
               if any(tl == str(t).lower() for t in (p.get("tags") or []))]
    return out


@pytest.mark.parametrize("kwargs", [
    {},
    {"q": "Profile 1"},
    {"q": "note 42"},
    {"q": "mozilla"},
    {"q": "EVEN"},
    {"q": "nothing matches this"},
    {"client_id": "client3"},
    {"tag": "even"},
    {"tag": "ODD"},
    {"q": "Profile 2", "client_id": "client2"},
    {"q": "note", "tag": "odd"},
])
def test_the_sql_filter_returns_what_the_python_one_did(kwargs):
    rows = _bulk(200)
    expected = {p["id"] for p in _python_filter(rows, **kwargs)}
    got = db.query_profiles(**kwargs)
    assert {p["id"] for p in got["items"]} == expected, kwargs
    assert got["total"] == len(expected), kwargs


def test_a_search_term_with_sql_wildcards_is_matched_literally():
    """Unescaped, a profile named "100%" searched for as "100%" would match
    every row, and "_" would match any character -- the search silently
    returning the wrong rows rather than none."""
    db.replace_all_profiles([
        {"id": "profile_a", "name": "100% cotton", "tags": [], "notes": ""},
        {"id": "profile_b", "name": "plain", "tags": [], "notes": ""},
        {"id": "profile_c", "name": "a_b", "tags": [], "notes": ""},
    ])
    assert {p["id"] for p in db.query_profiles(q="100%")["items"]} == {"profile_a"}
    assert {p["id"] for p in db.query_profiles(q="a_b")["items"]} == {"profile_c"}
    assert db.query_profiles(q="%")["total"] == 1


def test_the_total_counts_every_match_not_the_page():
    _bulk(120)
    page = query_page(tag="even", limit=25)
    assert len(page["items"]) == 25
    assert page["total"] == 60


# --------------------------------------------------------------------------
# 2. Paging
# --------------------------------------------------------------------------

def test_paging_walks_every_row_exactly_once():
    _bulk(97)
    seen: list[str] = []
    after = None
    for _ in range(20):
        page = query_page(limit=25, after_id=after)
        seen.extend(p["id"] for p in page["items"])
        if not page["has_more"]:
            break
        after = page["items"][-1]["id"]
    assert len(seen) == 97
    assert len(set(seen)) == 97


def test_has_more_is_false_on_the_last_page():
    _bulk(25)
    page = query_page(limit=25)
    assert len(page["items"]) == 25
    assert page["has_more"] is False


def test_has_more_is_true_when_one_row_remains():
    _bulk(26)
    assert query_page(limit=25)["has_more"] is True


def test_a_page_is_ordered_by_id():
    _bulk(50)
    ids = [p["id"] for p in query_page(limit=50)["items"]]
    assert ids == sorted(ids)


# --------------------------------------------------------------------------
# 3. Status stays a Python filter, and says so
# --------------------------------------------------------------------------

def test_filtering_by_status_still_works():
    """Status is synthesised on read, so it cannot be a WHERE clause without
    the database disagreeing with the live process table."""
    _bulk(30)
    page = query_page(status="idle", limit=10)
    assert len(page["items"]) == 10
    assert page["total"] == 30
    assert all(p["status"] == "idle" for p in page["items"])


def test_a_status_filter_still_pages():
    _bulk(60)
    first = query_page(status="idle", limit=25)
    assert first["has_more"] is True
    second = query_page(status="idle", limit=25, after_id=first["items"][-1]["id"])
    assert not ({p["id"] for p in first["items"]} & {p["id"] for p in second["items"]})


# --------------------------------------------------------------------------
# 4. The shape of the cost
# --------------------------------------------------------------------------

def test_a_page_does_not_get_slower_as_the_store_grows():
    """The property that was broken: work per page grew with the store.

    Asserted as a ratio rather than an absolute time, because absolute
    timings on a shared CI machine are noise. Twenty times the rows must not
    cost anything like twenty times the work.
    """
    def timed(n: int) -> float:
        _bulk(n)
        query_page(limit=25)  # warm
        start = time.perf_counter()
        for _ in range(5):
            query_page(limit=25)
        return (time.perf_counter() - start) / 5

    small = timed(200)
    large = timed(4000)
    # 20x the rows. Linear would be 20x; the old code was worse than that.
    assert large < small * 8, f"{small*1000:.1f}ms -> {large*1000:.1f}ms for 20x rows"


def test_filter_profiles_reads_the_store_once():
    """It used to be called in a loop of 500-row chunks, and re-read and
    re-decorated the entire store on every one of them."""
    _bulk(600)
    calls = 0
    real = db.query_profiles

    def counting(**kw):
        nonlocal calls
        calls += 1
        return real(**kw)

    db.query_profiles = counting
    try:
        filter_profiles(limit=50, offset=0)
    finally:
        db.query_profiles = real
    assert calls == 1, f"read the store {calls} times to serve one page"


# --------------------------------------------------------------------------
# 5. Select-all across a filter
# --------------------------------------------------------------------------

def test_the_ids_endpoint_resolves_a_whole_filter(tmp_path, monkeypatch):
    """Selection is otherwise bounded by the page, so a bulk action on a
    filtered set of 400 means paging through it sixteen times."""
    from multiprofile.app import create_app

    _bulk(120)
    client = create_app().test_client()
    body = client.get("/api/profiles/ids?tag=even").get_json()
    assert body["total"] == 60
    assert len(body["ids"]) == 60


def test_the_ids_endpoint_matches_the_list_endpoint():
    from multiprofile.app import create_app

    _bulk(80)
    client = create_app().test_client()
    ids = client.get("/api/profiles/ids?q=Profile 1").get_json()
    listed = client.get("/api/profiles?q=Profile 1&limit=100").get_json()
    assert ids["total"] == listed["total"]
    assert set(ids["ids"]) == {p["id"] for p in listed["items"]}


def test_an_oversized_selection_is_refused_with_its_size():
    """Truncating instead is how a "delete all" quietly deletes some."""
    from multiprofile.api import server
    from multiprofile.app import create_app

    _bulk(50)
    client = create_app().test_client()
    original = server.SELECT_ALL_CAP
    server.SELECT_ALL_CAP = 10
    try:
        resp = client.get("/api/profiles/ids")
        assert resp.status_code == 413
        body = resp.get_json()
        assert body["error"] == "selection_too_large"
        assert body["total"] == 50
        assert body["cap"] == 10
    finally:
        server.SELECT_ALL_CAP = original
