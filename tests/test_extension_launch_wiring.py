"""The launch-side wiring: what the browser is told, and what we believe back.

Three things live here and each has a way of being quietly wrong.

``_assigned_extension_payloads`` decides whether a profile is launched without
``--disable-extensions`` at all, so a bug here changes a profile's anti-detect
posture rather than just failing to load something.

``_observe_extension_ids`` is the verification primitive: it reads extension
IDs out of the browser's own target list. If it returned an empty list on a
transient error and that were treated as "nothing loaded", every verification
would fail; if it returned a stale or wrongly-parsed ID, every verification
would pass. Both are tested.

``open_url_in_profile`` is the only route to the Chrome Web Store flow and to
``chrome://extensions``. It must refuse rather than launch a browser behind
the operator's back.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

MANIFEST = {"manifest_version": 3, "name": "Wiring", "version": "1.0.0"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    from multiprofile.storage import db

    db.close()
    yield
    db.close()


def _ext(tmp_path):
    from multiprofile.extensions import manager

    z = tmp_path / "w.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST))
    return manager.import_zip(z)


def _profile(name="w"):
    from multiprofile.storage.profiles import create_profile

    return create_profile({"name": name, "proxyType": "none"})["id"]


# ---------------------------------------------------------------------------
# what the browser is told
# ---------------------------------------------------------------------------

def test_a_profile_with_nothing_assigned_contributes_no_paths():
    """And therefore keeps --disable-extensions, which is the whole point."""
    from multiprofile.browser.launcher import _assigned_extension_payloads

    assert _assigned_extension_payloads(_profile()) == []


def test_an_assigned_profile_contributes_its_managed_payload(tmp_path):
    from multiprofile.browser.launcher import _assigned_extension_payloads
    from multiprofile.extensions import manager
    from multiprofile.extensions.capability import BrowserCapability

    supported = BrowserCapability(
        binary="/x", brand="chromium", version="151.0.0.0", major=151,
        unpacked=True, crx=False, reason="test")
    ext = _ext(tmp_path)
    pid = _profile()
    manager.assign(ext["id"], [pid], capability=supported)
    got = _assigned_extension_payloads(pid)
    assert [u for u, _ in got] == [ext["id"]]
    assert got[0][1] == ext["payloadPath"]


def test_the_helper_never_raises_into_the_launch_path(monkeypatch):
    """A broken extension manager must not stop a profile launching. A launch
    that fails because of this feature would be worse than an extension that
    does not load."""
    import multiprofile.extensions.manager as M
    from multiprofile.browser.launcher import _assigned_extension_payloads

    def boom(_pid):
        raise RuntimeError("control database is on fire")

    monkeypatch.setattr(M, "payload_paths_for_profile", boom)
    assert _assigned_extension_payloads("anything") == []


# ---------------------------------------------------------------------------
# what the browser tells us back
# ---------------------------------------------------------------------------

def _fake_targets(monkeypatch, payloads):
    """Serve a scripted sequence of /json responses to the observer."""
    import multiprofile.browser.launcher as L

    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def read(self):
            return json.dumps(self._body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(_req, timeout=None):
        i = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        got = payloads[i]
        if isinstance(got, Exception):
            raise got
        return FakeResponse(got)

    monkeypatch.setattr(L, "_observe_extension_ids",
                        L._observe_extension_ids)   # keep the real function
    import urllib.request as U

    monkeypatch.setattr(U, "urlopen", fake_urlopen)
    return calls


def test_extension_ids_are_read_out_of_the_browsers_own_target_list(monkeypatch):
    from multiprofile.browser.launcher import _observe_extension_ids

    _fake_targets(monkeypatch, [[
        {"type": "page", "url": "http://127.0.0.1:1/x"},
        {"type": "service_worker",
         "url": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/sw.js"},
        {"type": "background_page",
         "url": "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/_generated.html"},
    ]])
    assert _observe_extension_ids(9222, timeout_s=2.0) == [
        "a" * 32, "b" * 32,
    ]


def test_a_non_extension_target_contributes_nothing(monkeypatch):
    from multiprofile.browser.launcher import _observe_extension_ids

    _fake_targets(monkeypatch, [[
        {"type": "page", "url": "https://example.test/"},
        {"type": "browser", "url": ""},
    ]])
    assert _observe_extension_ids(9222, timeout_s=1.0) == []


def test_the_observer_polls_rather_than_reading_once(monkeypatch):
    """An MV3 service worker starts asynchronously after the browser does, so
    a single read would miss it and every verification would fail."""
    from multiprofile.browser.launcher import _observe_extension_ids

    calls = _fake_targets(monkeypatch, [
        [{"type": "page", "url": "http://127.0.0.1:1/x"}],       # not yet
        [{"type": "page", "url": "http://127.0.0.1:1/x"}],       # still not
        [{"type": "service_worker",
          "url": "chrome-extension://cccccccccccccccccccccccccccccccc/sw.js"}],
    ])
    assert _observe_extension_ids(9222, timeout_s=5.0) == ["c" * 32]
    assert calls["n"] >= 3, "it stopped after the first read"


def test_a_transient_error_does_not_become_nothing_loaded(monkeypatch):
    """Returning [] on an error would be read as "the browser loaded nothing",
    which would mark a working install failed."""
    from multiprofile.browser.launcher import _observe_extension_ids

    _fake_targets(monkeypatch, [
        OSError("connection refused"),
        [{"type": "service_worker",
          "url": "chrome-extension://dddddddddddddddddddddddddddddddd/sw.js"}],
    ])
    assert _observe_extension_ids(9222, timeout_s=5.0) == ["d" * 32]


def test_the_observer_gives_up_rather_than_hanging(monkeypatch):
    from multiprofile.browser.launcher import _observe_extension_ids

    _fake_targets(monkeypatch, [OSError("refused")])
    assert _observe_extension_ids(9222, timeout_s=0.8) == []


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------

def test_verification_records_what_the_browser_reported(tmp_path, monkeypatch):
    from multiprofile.browser.launcher import _verify_assigned_extensions
    from multiprofile.extensions import manager, store
    from multiprofile.extensions.capability import BrowserCapability

    supported = BrowserCapability(
        binary="/x", brand="chromium", version="151.0.0.0", major=151,
        unpacked=True, crx=False, reason="test")
    ext = _ext(tmp_path)
    pid = _profile()
    manager.assign(ext["id"], [pid], capability=supported)
    _fake_targets(monkeypatch, [[
        {"type": "service_worker",
         "url": f"chrome-extension://{ext['extensionId']}/sw.js"},
    ]])
    report = _verify_assigned_extensions(pid, 9222, [(ext["id"], ext["payloadPath"])])
    assert report["assigned"] == 1
    assert report["verified"] == 1
    assert ext["extensionId"] in report["observedIds"]
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["state"] == store.STATE_INSTALLED_VERIFIED


def test_verification_failure_is_recorded_not_swallowed(tmp_path, monkeypatch):
    from multiprofile.browser.launcher import _verify_assigned_extensions
    from multiprofile.extensions import manager, store
    from multiprofile.extensions.capability import BrowserCapability

    supported = BrowserCapability(
        binary="/x", brand="chromium", version="151.0.0.0", major=151,
        unpacked=True, crx=False, reason="test")
    ext = _ext(tmp_path)
    pid = _profile()
    manager.assign(ext["id"], [pid], capability=supported)
    _fake_targets(monkeypatch, [[{"type": "page", "url": "http://127.0.0.1:1/x"}]])
    report = _verify_assigned_extensions(pid, 9222, [(ext["id"], ext["payloadPath"])])
    assert report["verified"] == 0
    row = store.get_assignment(ext["id"], pid)
    assert row is not None
    assert row["state"] == store.STATE_FAILED
    assert "did not report" in (row["lastError"] or "")


def test_a_broken_verifier_does_not_fail_the_launch(tmp_path, monkeypatch):
    """Reporting must not be able to turn a working launch into a failed one."""
    import multiprofile.extensions.manager as M
    from multiprofile.browser.launcher import _verify_assigned_extensions

    ext = _ext(tmp_path)
    monkeypatch.setattr(M, "mark_applied",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    report = _verify_assigned_extensions(_profile(), 9222,
                                         [(ext["id"], ext["payloadPath"])])
    assert report["assigned"] == 1
    assert "error" in report


# ---------------------------------------------------------------------------
# opening a page in a profile
# ---------------------------------------------------------------------------

def test_opening_a_url_refuses_when_the_profile_is_not_running():
    """A Web Store install needs the operator in front of the window, so this
    reports instead of starting a browser behind their back."""
    from multiprofile.browser.launcher import open_url_in_profile

    out = open_url_in_profile(_profile(), "https://chromewebstore.google.com/x")
    assert out["ok"] is False
    assert out["errors"] == ["profile_not_running"]
    assert "Launch it first" in out["error"]


def test_opening_a_url_reports_an_unreachable_debugger(monkeypatch):
    import multiprofile.browser.launcher as L

    pid = _profile()
    monkeypatch.setattr(L, "_cdp_port_open", lambda port: True)

    class FakeRuntime:
        cdp_port = 9333

    monkeypatch.setattr("multiprofile.browser.runtime_state.get_runtime",
                        lambda _pid: FakeRuntime())
    import urllib.request as U

    monkeypatch.setattr(U, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    out = L.open_url_in_profile(pid, "chrome://extensions")
    assert out["ok"] is False
    assert out["errors"] == ["cdp_unreachable"]


def test_every_failure_mode_names_an_error_code(monkeypatch):
    """The API maps these to a status, and an unnamed failure answers 500."""
    from multiprofile.browser.launcher import open_url_in_profile

    out = open_url_in_profile("profile_missing", "chrome://extensions")
    assert out["ok"] is False
    assert out.get("errors"), out
    assert out.get("error"), out


# ---------------------------------------------------------------------------
# the payload path the browser is given
# ---------------------------------------------------------------------------

def test_the_payload_path_is_absolute_and_inside_managed_storage(tmp_path):
    from multiprofile.storage.xdg import extensions_dir

    ext = _ext(tmp_path)
    payload = Path(ext["payloadPath"])
    assert payload.is_absolute()
    assert str(payload).startswith(str(extensions_dir().resolve())) or \
        str(payload).startswith(str(extensions_dir()))
