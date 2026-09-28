"""F11: a swallowed failure on the identity path must still be reportable.

``pyproject.toml`` deselects ``BLE001`` and ``S110`` on purpose, and the reason
is sound -- a launch path must not crash a browser because an optional step
failed. The cost is that this product's value *is* surfacing failures, and F1
was a failure that was swallowed and reported as a success. The exception
policy and the silent-failure bug are the same problem seen twice.

The fix is not a blanket cleanup: cosmetic handlers keep their ``pass``.
Handlers on the identity path -- the ones deciding whether a profile browses as
the identity it claims -- record a structured fault that reaches the launch
notes and ``/api/health``. The shape follows ``engine.py``'s "unavailable"
record, which already did this for one case.

These tests pin down the property, not the current inventory of call sites: a
fault must be *reachable* from the two places an operator looks.
"""
from __future__ import annotations

import pytest

from multiprofile.browser import identity_faults as F

HEADERS = {"Host": "localhost", "Origin": "http://localhost", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def clean_faults():
    F.reset()
    yield
    F.reset()


@pytest.fixture()
def xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("MULTIPROFILE_API_TOKEN", raising=False)
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    monkeypatch.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")


@pytest.fixture()
def client(xdg):
    from multiprofile.api.server import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------
# 1. The record itself
# --------------------------------------------------------------------------

def test_a_fault_carries_a_code_a_reason_and_a_place():
    F.record("some_code", "the human reason", where="mod.fn", profile_id="p1")
    (fault,) = F.faults_for("p1")
    assert fault.code == "some_code"
    assert fault.detail == "the human reason"
    assert fault.where == "mod.fn"
    assert fault.at > 0


def test_recording_never_raises_whatever_it_is_handed():
    """It is called from inside ``except`` blocks.

    A recorder that can throw would turn a swallowed failure into a crash,
    which is worse than the bug it reports and would get the policy reverted.
    """
    class Hostile:
        def __str__(self) -> str:
            raise RuntimeError("no string for you")

    F.record(Hostile(), Hostile(), where="mod.fn", profile_id="p1")  # must not raise
    F.record("ok", "fine", where="mod.fn", profile_id=None)
    assert any(f.code == "ok" for f in F.recent())


def test_a_long_detail_is_truncated_not_dropped():
    F.record("c", "x" * 5000, where="mod.fn", profile_id="p1")
    (fault,) = F.faults_for("p1")
    assert 0 < len(fault.detail) <= 500


def test_the_registry_is_bounded():
    """A launch loop must not grow this without bound."""
    for i in range(F._MAX_FAULTS + 50):
        F.record("c", f"n={i}", where="mod.fn", profile_id="p1")
    assert len(F.faults_for("p1")) <= F._MAX_FAULTS
    assert F.health_summary()["truncated"] is True


def test_faults_are_scoped_per_profile():
    F.record("a", "one", where="mod.fn", profile_id="p1")
    F.record("b", "two", where="mod.fn", profile_id="p2")
    assert [f.code for f in F.faults_for("p1")] == ["a"]
    assert [f.code for f in F.faults_for("p2")] == ["b"]


def test_clearing_one_profile_leaves_the_others():
    F.record("a", "one", where="mod.fn", profile_id="p1")
    F.record("b", "two", where="mod.fn", profile_id="p2")
    F.clear_for("p1")
    assert F.faults_for("p1") == []
    assert [f.code for f in F.faults_for("p2")] == ["b"]


# --------------------------------------------------------------------------
# 2. It reaches /api/health
# --------------------------------------------------------------------------

def test_health_always_carries_the_identity_block(client):
    """``faults: []`` is a statement, not an absence.

    A block that appears only on trouble cannot be used to confirm that
    nothing went wrong.
    """
    body = client.get("/api/health", headers=HEADERS).get_json()
    assert "identity" in body
    assert body["identity"]["faults"] == []
    assert body["identity"]["faultCount"] == 0


def test_a_recorded_fault_shows_up_in_health(client):
    F.record("ua_override_failed", "the override did not apply", where="mod.fn", profile_id="p1")
    identity = client.get("/api/health", headers=HEADERS).get_json()["identity"]
    assert identity["faultCount"] == 1
    assert identity["byCode"] == {"ua_override_failed": 1}
    assert identity["faults"][0]["code"] == "ua_override_failed"
    assert identity["faults"][0]["profileId"] == "p1"


def test_health_groups_by_code(client):
    for _ in range(3):
        F.record("preset_unknown", "d", where="mod.fn", profile_id="p1")
    F.record("ua_override_failed", "d", where="mod.fn", profile_id="p2")
    identity = client.get("/api/health", headers=HEADERS).get_json()["identity"]
    assert identity["byCode"] == {"preset_unknown": 3, "ua_override_failed": 1}


# --------------------------------------------------------------------------
# 3. Real call sites: the failure that used to be invisible
# --------------------------------------------------------------------------

def test_a_preset_that_does_not_load_is_recorded(xdg):
    """The F8 story in code form: a device selection that never applied.

    ``device_preset_for_profile`` returned None for an unknown id and the
    profile was built without the device it asked for, with nothing said.
    """
    from multiprofile.fingerprint._core import device_preset_for_profile

    assert device_preset_for_profile({"id": "p1", "deviceId": "no-such-device"}) is None
    codes = [f.code for f in F.faults_for("p1")]
    assert "preset_unknown" in codes


def test_a_profile_that_asked_for_no_preset_is_not_a_fault(xdg):
    """Only an unmet request is a fault. Otherwise every profile would be one."""
    from multiprofile.fingerprint._core import device_preset_for_profile

    assert device_preset_for_profile({"id": "p2"}) is None
    assert F.faults_for("p2") == []


def test_a_broken_registry_is_recorded_not_hidden(xdg, monkeypatch):
    import sys

    from multiprofile.fingerprint import _core

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("registry exploded")

    # The module object from sys.modules, not the package attribute: the
    # devices package exports a singleton *named* `registry`, which shadows the
    # submodule of the same name, so both the dotted string and
    # `import ... as reg` hand back the instance instead of the module.
    monkeypatch.setattr(sys.modules["multiprofile.devices.registry"], "DeviceRegistry", Boom)
    assert _core.device_preset_for_profile({"id": "p3", "deviceId": "iphone-16"}) is None
    codes = [f.code for f in F.faults_for("p3")]
    assert "preset_unreadable" in codes, codes


def test_an_unserialisable_preset_is_recorded(xdg, monkeypatch):
    import sys

    from multiprofile.fingerprint import _core

    class Preset:
        def to_dict(self):
            raise RuntimeError("cannot serialise")

    class Registry:
        def get(self, _id):
            return Preset()

    monkeypatch.setattr(sys.modules["multiprofile.devices.registry"], "DeviceRegistry", Registry)
    assert _core.device_preset_for_profile({"id": "p4", "deviceId": "iphone-16"}) is None
    assert "preset_unserializable" in [f.code for f in F.faults_for("p4")]


# --------------------------------------------------------------------------
# 4. It reaches the launch notes
# --------------------------------------------------------------------------

def test_a_fault_reads_as_a_launch_note():
    F.record("ua_override_failed", "the override did not apply", where="mod.fn", profile_id="p1")
    (fault,) = F.faults_for("p1")
    note = fault.note()
    assert "IDENTITY FAULT" in note
    assert "ua_override_failed" in note
    assert "the override did not apply" in note


def test_a_fault_from_deeper_in_the_stack_reaches_the_notes(xdg, monkeypatch):
    """The launcher notes its own branches; this is for the rest.

    A fault raised while building the fingerprint has no other route to the
    notes of the profile it concerns.
    """
    import multiprofile.browser.launcher as L
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "noted", "proxyType": "none", "startUrl": "about:blank"})
    pid = profile["id"]

    # Raise the fault from something the launcher calls mid-launch, after it has
    # cleared the previous launch's faults. build_fingerprint is NOT such a
    # seam: the launcher uses the fingerprint already stored on the profile and
    # only builds one when that is missing.
    real = L.chrome_gpu_args

    def gpu_args_with_fault(mode):
        F.record(
            "preset_unknown", "planted for the test",
            where="fingerprint._core.device_preset_for_profile", profile_id=pid,
        )
        return real(mode)

    monkeypatch.setattr(L, "chrome_gpu_args", gpu_args_with_fault)
    res = L.launch_profile(pid, url="about:blank", direct=True)
    try:
        notes = " ".join(res.get("notes") or [])
        assert "IDENTITY FAULT" in notes, res.get("notes")
        assert "planted for the test" in notes
    finally:
        L.stop_profile(pid)


def test_a_launch_does_not_report_the_previous_launchs_faults(xdg):
    """Stale faults would make a healthy launch look broken."""
    import multiprofile.browser.launcher as L
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "fresh", "proxyType": "none", "startUrl": "about:blank"})
    pid = profile["id"]
    F.record("stale", "from a previous launch", where="somewhere.else", profile_id=pid)

    res = L.launch_profile(pid, url="about:blank", direct=True)
    try:
        assert "stale" not in " ".join(res.get("notes") or [])
        assert "stale" not in [f.code for f in F.faults_for(pid)]
    finally:
        L.stop_profile(pid)


def test_the_launcher_does_not_double_report_its_own_faults(xdg):
    """Its branches already write a note at the call site.

    Appending every fault again would print each launcher failure twice, which
    is how a useful note turns into noise an operator stops reading.
    """
    import multiprofile.browser.launcher as L
    from multiprofile.storage.profiles import create_profile

    profile = create_profile({"name": "nodupe", "proxyType": "none", "startUrl": "about:blank"})
    pid = profile["id"]

    notes: list[str] = []
    L._fault(notes, "ua_override_failed", "planted at launcher scope", profile_id=pid)

    # _fault writes the note itself, and stamps the exact scope the sweep skips.
    assert len(notes) == 1 and "ua_override_failed" in notes[0]
    (fault,) = F.faults_for(pid)
    assert fault.where == L._LAUNCHER_SCOPE
    swept = [f.note() for f in F.faults_for(pid) if f.where != L._LAUNCHER_SCOPE]
    assert swept == [], "the launcher's own fault would have been printed twice"


def test_a_launcher_fault_raised_outside_the_helper_is_still_swept():
    """The rule is "already noted", not "happened in the launcher".

    _resolve_wire_identity records faults at launcher scope but has no notes
    list to write to, so a prefix match on "browser.launcher" would have hidden
    them from the notes entirely -- which is the bug F11 is about.
    """
    import multiprofile.browser.launcher as L

    F.record(
        "preset_hints_unreadable", "hints could not be built",
        where="browser.launcher._resolve_wire_identity", profile_id="p9",
    )
    swept = [f for f in F.faults_for("p9") if f.where != L._LAUNCHER_SCOPE]
    assert len(swept) == 1, "a launcher fault nothing noted must still be swept"
