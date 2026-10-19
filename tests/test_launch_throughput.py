"""Starting a fleet must not cost one launch after another.

This product is designed for 150 stored profiles with 20-40 running. Bulk
launch was a plain ``for`` loop, so starting N profiles cost N launches end to
end -- measured at ~1.6 s each, starting 40 took about a minute while the
operator watched a progress bar. Almost all of that is spent *waiting*: for
Chrome to boot, for its debugging port, for the identity probe to answer. So
it overlaps nearly perfectly. Measured with 8 profiles::

    sequential   ~13 s
    parallel      2.9 s   (0.36 s per profile)

Two things had to be true first, and both are asserted here: ports must not be
handed to two launches at once, and the per-launch settle must not be a fixed
sleep.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture(autouse=True)
def xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")


# --------------------------------------------------------------------------
# 1. Port allocation under concurrency
# --------------------------------------------------------------------------

def test_two_launches_are_never_handed_the_same_port():
    """The kernel picks the number, we close the socket, and Chrome binds it
    a moment later. Sequentially that window is small enough not to matter;
    in parallel two launches were handed the same number, and the second
    browser then failed to bind its debugging port -- surfacing as a launch
    that came up with no CDP at all."""
    from multiprofile.browser.cdp_window import allocate_localhost_port

    with ThreadPoolExecutor(max_workers=16) as pool:
        ports = list(pool.map(lambda _: allocate_localhost_port(), range(200)))
    assert len(ports) == len(set(ports)), f"{len(ports) - len(set(ports))} collisions"


def test_a_released_port_can_be_handed_out_again():
    """Otherwise a long-running process leaks the range."""
    from multiprofile.browser import cdp_window as C

    port = C.allocate_localhost_port()
    assert port in C._handed_out
    C.release_localhost_port(port)
    assert port not in C._handed_out


def test_stopping_a_profile_returns_its_port(monkeypatch):
    """The release has to be wired into teardown, not merely available.

    It was not: nothing in src/ called it, so ports accumulated for the life
    of the process and the only thing bounding them was a size backstop.
    """
    from multiprofile.browser import cdp_window as C
    from multiprofile.browser import launcher as L
    from multiprofile.browser import runtime_state as RS

    port = C.allocate_localhost_port()
    assert port in C._handed_out

    class _RT:
        cdp_port = port

    monkeypatch.setattr(RS, "get_runtime", lambda pid: _RT())
    L._release_cdp_port("profile_x")
    assert port not in C._handed_out


def test_releasing_a_port_for_an_unknown_profile_is_harmless(monkeypatch):
    from multiprofile.browser import launcher as L
    from multiprofile.browser import runtime_state as RS

    monkeypatch.setattr(RS, "get_runtime", lambda pid: None)
    L._release_cdp_port("profile_nope")


def test_the_tracked_set_is_bounded_even_without_releases():
    """Backstop for a teardown that never runs -- a crash, a kill -9."""
    from multiprofile.browser import cdp_window as C

    for _ in range(C._MAX_TRACKED + 50):
        C.allocate_localhost_port()
    assert len(C._handed_out) <= C._MAX_TRACKED + 1


def test_releasing_nothing_is_harmless():
    from multiprofile.browser.cdp_window import release_localhost_port

    release_localhost_port(None)
    release_localhost_port(0)


# --------------------------------------------------------------------------
# 2. The settle after spawn
# --------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, exit_after: float | None = None, code: int = 1):
        self._deadline = None if exit_after is None else time.monotonic() + exit_after
        self._code = code

    def poll(self):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return self._code
        return None


def test_a_browser_that_dies_is_noticed_at_once(monkeypatch):
    """It used to cost a flat 1.2 s to notice, on every launch."""
    from multiprofile.browser import launcher as L

    monkeypatch.setattr(L, "_cdp_port_open", lambda port: False)
    start = time.monotonic()
    rc = L._settle_after_spawn(_FakeProc(exit_after=0.0, code=9), 1234)
    elapsed = time.monotonic() - start
    assert rc == 9
    assert elapsed < 0.3, elapsed


def test_a_browser_that_binds_its_port_is_not_waited_on(monkeypatch):
    from multiprofile.browser import launcher as L

    monkeypatch.setattr(L, "_cdp_port_open", lambda port: True)
    start = time.monotonic()
    rc = L._settle_after_spawn(_FakeProc(), 1234)
    elapsed = time.monotonic() - start
    assert rc is None
    assert elapsed < 0.3, elapsed


def test_a_slow_but_healthy_start_is_still_bounded(monkeypatch):
    """The timeout is unchanged, so a slow start is treated as it was."""
    from multiprofile.browser import launcher as L

    monkeypatch.setattr(L, "_cdp_port_open", lambda port: False)
    start = time.monotonic()
    rc = L._settle_after_spawn(_FakeProc(), 1234, timeout_s=0.4)
    elapsed = time.monotonic() - start
    assert rc is None
    assert 0.35 < elapsed < 1.0, elapsed


def test_a_browser_that_dies_late_is_still_caught(monkeypatch):
    """The whole point of the wait: a process that exits part-way through."""
    from multiprofile.browser import launcher as L

    monkeypatch.setattr(L, "_cdp_port_open", lambda port: False)
    rc = L._settle_after_spawn(_FakeProc(exit_after=0.1, code=3), 1234, timeout_s=1.0)
    assert rc == 3


# --------------------------------------------------------------------------
# 3. Bulk launch runs in parallel and keeps its contract
# --------------------------------------------------------------------------

def _run_bulk(monkeypatch, n, *, launch_delay=0.2, fail=(), ids=None, spans=None):
    """Drive a bulk batch with a stubbed launch, so this measures the loop.

    Rewritten for F20: the batch now runs on the durable queue's workers
    rather than a thread pool inside the job record. Exactly-once, operator
    ordering, the concurrency ceiling and partial failure are unchanged.
    Overlap is judged on ``spans`` when the caller passes a list: each entry
    is the start and end of one stubbed launch, so the sequential cost is
    measured in the same run as the overlap.
    """
    from multiprofile.browser import manager as browser_manager
    from multiprofile.jobs import batches, create_job, get_job, start_workers, stop_workers
    from multiprofile.storage.profiles import create_profile

    monkeypatch.setenv("MULTIPROFILE_WINDOW_TILING_ENABLED", "0")
    if ids is None:
        ids = [create_profile({"name": f"b{i}", "proxyType": "none"})["id"]
               for i in range(n)]

    concurrent = 0
    peak = 0
    lock = threading.Lock()

    def fake_launch(pid, **kw):
        nonlocal concurrent, peak
        with lock:
            concurrent += 1
            peak = max(peak, concurrent)
        started = time.monotonic()
        try:
            time.sleep(launch_delay)
            if pid in fail:
                return {"ok": False, "error": "nope"}
            return {"ok": True}
        finally:
            if spans is not None:
                spans.append((started, time.monotonic()))
            with lock:
                concurrent -= 1

    monkeypatch.setattr(browser_manager, "acquire_slot", lambda pid, timeout=None: True)
    monkeypatch.setattr(browser_manager, "release_slot", lambda pid: None)
    monkeypatch.setattr(browser_manager, "mark_running", lambda pid: None)
    # The state transitions write to the control DB and publish events; none
    # of that is what this file measures.
    monkeypatch.setattr(batches, "_apply_tiling", lambda *a, **k: None)

    start = time.monotonic()
    job = create_job("bulk_launch", ids)
    worker = start_workers(launch_fn=fake_launch)
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = get_job(job["id"])
            if current["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.01)
        else:  # pragma: no cover - only on a hang
            raise AssertionError(f"batch never finished: {get_job(job['id'])}")
    finally:
        stop_workers()
        assert not worker.running
    return get_job(job["id"]), ids, time.monotonic() - start, peak


def test_bulk_launch_overlaps_instead_of_queueing(monkeypatch):
    """Overlap is a ratio against the durations measured in this run.

    A fixed ceiling does not survive a shared runner. Eight sleeps of 0.25s
    were required to finish in under 1.2s; one CI run took 4.79s and the
    same commit passed when that job was re-run. The sequential cost is the
    sum of the eight launch durations recorded here, so a stall stretches
    the budget and the measurement together. ``peak > 1`` still requires
    that two launches were in flight at once. 0.6 is the old 1.2s / 2.0s
    line. A cap of 1 lands near 1.0 and fails it.
    """
    monkeypatch.setenv("MAX_CONCURRENT_PROFILES", "8")
    spans: list[tuple[float, float]] = []
    _job, _ids, _elapsed, peak = _run_bulk(
        monkeypatch, 8, launch_delay=0.25, spans=spans,
    )
    assert peak > 1, "launches never overlapped"
    assert len(spans) == 8
    sequential = sum(end - start for start, end in spans)
    overlapped = max(end for _start, end in spans) - min(start for start, _end in spans)
    assert sequential > 0
    assert overlapped < sequential * 0.6, (
        f"overlapped {overlapped:.2f}s vs sequential {sequential:.2f}s"
    )


def test_every_profile_is_launched_exactly_once(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_PROFILES", "8")
    job, ids, _elapsed, _peak = _run_bulk(monkeypatch, 8, launch_delay=0.02)
    got = [r["profileId"] for r in job["results"]]
    assert sorted(got) == sorted(ids)
    assert len(got) == len(set(got))


def test_results_stay_in_the_order_asked_for(monkeypatch):
    """Not the order they happened to finish in: the response has to read
    the same as it did when this was a sequential loop."""
    monkeypatch.setenv("MAX_CONCURRENT_PROFILES", "8")
    job, ids, _elapsed, _peak = _run_bulk(monkeypatch, 8, launch_delay=0.02)
    assert [r["profileId"] for r in job["results"]] == ids


def test_the_pool_is_no_wider_than_the_concurrency_cap(monkeypatch):
    """acquire_slot already decides how many browsers may exist, so a wider
    pool would only queue inside the semaphore."""
    monkeypatch.setenv("MAX_CONCURRENT_PROFILES", "3")
    _job, _ids, _elapsed, peak = _run_bulk(monkeypatch, 9, launch_delay=0.1)
    assert peak <= 3, peak


def test_a_failure_does_not_stop_the_others(monkeypatch):
    """One browser refusing to start must not cost the rest of the batch."""
    monkeypatch.setenv("MAX_CONCURRENT_PROFILES", "8")
    from multiprofile.storage.profiles import create_profile

    # Pre-create so the ids are known before the run, and fail two of them.
    ids = [create_profile({"name": f"pre{i}", "proxyType": "none"})["id"]
           for i in range(6)]
    doomed = {ids[1], ids[4]}

    job, run_ids, _elapsed, _peak = _run_bulk(
        monkeypatch, 6, launch_delay=0.02, fail=doomed, ids=ids
    )
    assert len(job["results"]) == 6
    ok = {r["profileId"] for r in job["results"] if r.get("ok")}
    bad = {r["profileId"] for r in job["results"] if not r.get("ok")}
    assert bad == doomed, (bad, doomed)
    assert ok == set(ids) - doomed


def test_progress_counts_completions(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_PROFILES", "4")
    job, ids, _e, _p = _run_bulk(monkeypatch, 4, launch_delay=0.02)
    assert job["progress"] == 4


# --------------------------------------------------------------------------
# 4. The locale override is a browser-wide setting
# --------------------------------------------------------------------------

def test_the_locale_override_is_sent_once_per_browser():
    """Unlike the timezone override, this one is per browser context and
    refuses to be set twice -- "Another locale override is already in
    effect". Sending it per attached target meant every target after the
    first got an error back. It only became visible when bulk launch started
    attaching several targets at once.
    """
    from multiprofile.browser.cdp_inject import IdentityInjector

    inj = IdentityInjector(1234, locale="de-DE", worker_script="")
    sent: list[str] = []
    inj._send = lambda method, params=None, session_id=None: sent.append(method)
    inj._ws = object()

    for n in range(3):
        inj._on_attached({
            "sessionId": f"s{n}",
            "targetInfo": {"type": "page", "targetId": f"t{n}", "url": "about:blank"},
            "waitingForDebugger": False,
        })
    assert sent.count("Emulation.setLocaleOverride") == 1, sent
