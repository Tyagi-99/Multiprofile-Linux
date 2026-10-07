"""The Python SDK, against a real server rather than a mock.

Phase 6 asks for "documented Python and Node SDKs for create/launch/stop/list
and automation attachment". A mocked SDK test proves the SDK agrees with the
author's idea of the API, which is the thing most likely to be wrong -- so
these run it against `create_app()`'s real routes through a loopback HTTP
server.

The attachment path is the one that matters: `launch()` must hand back
something Playwright or Puppeteer can connect to, because a caller who
launches Chromium themselves gets a browser with none of the product's
identity, route or font view.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

SDK = Path(__file__).resolve().parents[1] / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def server(tmp_path, monkeypatch):
    """The real app, on a real loopback port."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("MULTIPROFILE_NO_BACKGROUND", "1")

    from multiprofile.api.server import create_app

    app = create_app()
    httpd = make_server("127.0.0.1", 0, app, handler_class=_QuietHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def client(server):
    from multiprofile_client import Client

    return Client(server)


def test_health_needs_no_token(client):
    body = client.health()
    assert body["ok"] is True
    assert body["version"]


def test_doctor_is_reachable(client):
    """So a run can check itself before launching anything."""
    report = client.doctor()
    assert "ok" in report and "findings" in report


def test_create_then_get_then_list(client):
    created = client.create("sdk-one", startUrl="about:blank")
    assert created["id"].startswith("profile_")

    fetched = client.get(created["id"])
    assert fetched["id"] == created["id"]
    # The detail endpoint carries what the list does not.
    assert "identity" in fetched, fetched.keys()

    listed = client.list()
    assert any(p["id"] == created["id"] for p in listed)


def test_create_passes_fields_through(client):
    created = client.create(
        "sdk-proxied", proxyType="http", proxyHost="127.0.0.1", proxyPort=8080)
    assert created["proxyType"] == "http"
    assert created["proxyHost"] == "127.0.0.1"


def test_an_unknown_field_is_an_error_not_a_silent_setting(client):
    """F8. A typo should be a 400, not a stored value that does nothing."""
    from multiprofile_client import ApiError

    with pytest.raises(ApiError) as exc:
        client.create("sdk-typo", proxyTpe="http")
    assert exc.value.status == 400


def test_delete_defaults_to_the_trash(client):
    created = client.create("sdk-trash")
    body = client.delete(created["id"])
    assert body["ok"] is True
    assert body.get("permanent") is False


def test_a_permanent_delete_reports_the_outcome(client):
    """F51: `purgeStorage` is what happened, not what was asked."""
    created = client.create("sdk-permanent")
    body = client.delete(created["id"], permanent=True)
    assert body["ok"] is True
    assert body["permanent"] is True
    assert "purgeStorage" in body and "purgeRequested" in body


def test_an_unreachable_server_says_so_usefully(tmp_path):
    """The most common first failure deserves a message that names the fix."""
    from multiprofile_client import Client, MultiprofileError

    client = Client("http://127.0.0.1:1", timeout=2.0)
    with pytest.raises(MultiprofileError) as exc:
        client.health()
    assert "server-only" in str(exc.value), str(exc.value)


def test_a_refused_launch_is_its_own_exception(client):
    """A fail-closed refusal is the expected outcome of a route decision,
    not a bug, and a caller handles it differently from a dead server.

    Authenticated SOCKS5 (F56) is the cleanest refusal to provoke: Chromium
    cannot use it, so the launch is blocked with that reason.
    """
    from multiprofile_client import LaunchBlocked

    created = client.create(
        "sdk-blocked", proxyType="socks5", proxyHost="127.0.0.1",
        proxyPort=1080, proxyUser="u", proxyPass="p")
    with pytest.raises(LaunchBlocked) as exc:
        client.launch(created["id"])
    assert "SOCKS5" in exc.value.reason, exc.value.reason


def test_wait_for_cdp_refuses_a_session_with_no_port(client):
    """Rather than waiting 20 seconds to say nothing was listening."""
    from multiprofile_client import MultiprofileError, Session

    session = Session("profile_x", None, "127.0.0.1", None, {})
    assert session.cdp_url is None
    with pytest.raises(MultiprofileError) as exc:
        client.wait_for_cdp(session, timeout=1.0)
    assert "no CDP port" in str(exc.value)


# --------------------------------------------------------------------------
# Automation attachment, live
# --------------------------------------------------------------------------

def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


@pytest.mark.network
@pytest.mark.skipif(not _chrome_available(), reason="Chrome is not installed")
def test_launch_hands_back_an_endpoint_an_automation_tool_can_attach_to(client):
    """The point of the SDK.

    Not just "a port was returned" -- the endpoint is opened and asked for
    its version, which is exactly what `connect_over_cdp` does first.
    """
    import json
    import urllib.request

    created = client.create("sdk-attach", startUrl="about:blank")
    session = client.launch(created["id"], direct=True)
    try:
        assert session.cdp_url, session.raw
        url = client.wait_for_cdp(session, timeout=25.0)
        request = urllib.request.Request(
            f"{url}/json/version",
            headers={"Host": f"localhost:{session.cdp_port}"})
        with urllib.request.urlopen(request, timeout=5) as response:
            version = json.loads(response.read())
        assert "Chrom" in version.get("Browser", ""), version
        # And the launcher's own record came through, which is where an
        # experimental identity claim or an engine warning would appear.
        assert session.notes, session.raw.keys()
        assert any("Engine:" in note for note in session.notes), session.notes
    finally:
        client.stop(created["id"])


# --------------------------------------------------------------------------
# Status codes: a refusal is not a server error
# --------------------------------------------------------------------------
#
# Every failed launch answered 500, including the deliberate, documented
# refusals. That matters in two concrete ways: monitoring sees server errors
# during normal operation, and a client's retry logic retries something that
# can never succeed. The route one line above already used 409 for
# `consistency_blocked`, so the pattern was established and the launch path
# simply did not follow it.


def test_a_refused_launch_answers_409_not_500(server):
    """The HTTP-level assertion, independent of the SDK's mapping."""
    import json
    import urllib.error
    import urllib.request

    from multiprofile_client import Client

    created = Client(server).create(
        "code-blocked", proxyType="socks5", proxyHost="127.0.0.1",
        proxyPort=1080, proxyUser="u", proxyPass="p")
    request = urllib.request.Request(
        f"{server}/api/profiles/{created['id']}/launch",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=20)
        raise AssertionError("the launch succeeded; it should have been refused")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        assert e.code == 409, (e.code, body)
        assert body["errors"] == ["route_unsupported"], body


def test_a_missing_profile_answers_404(server):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{server}/api/profiles/profile_nope/launch",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=10)
        raise AssertionError("expected a 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code


def test_the_refusal_set_covers_every_decision_the_launcher_can_make():
    """A new refusal code added to the launcher without being classified
    here would silently go back to answering 500.

    The list is derived from the launcher's own source rather than restated,
    so adding a code there fails this test until it is classified.
    """
    import re
    from pathlib import Path

    from multiprofile.api.server import _LAUNCH_REFUSALS

    source = (Path(__file__).resolve().parents[1]
              / "src/multiprofile/browser/launcher.py").read_text()
    emitted = set(re.findall(r'"errors": \["([a-z_]+)"\]', source))
    # Genuine faults, which SHOULD be 500, and absence, which is 404.
    faults = {
        "dependency_missing", "spawn_failed", "chrome_exit_immediate",
        "permission", "profile_not_found",
    }
    # Codes other operations in the same module emit. The grep above reads the
    # whole file, so they turn up here without being launch outcomes at all.
    #
    # `stop_timeout` is the stop path. The four below are
    # `open_url_in_profile`, which the extension manager uses to reach the
    # Chrome Web Store flow and chrome://extensions in a chosen profile. Those
    # routes classify their own status explicitly -- 409 when the profile is
    # not running or the debugger cannot be reached -- so they never fall
    # through to the launch classifier that this test guards.
    not_launch_outcomes = {
        "stop_timeout",
        "profile_not_running", "cdp_unreachable", "cdp_error", "cdp_timeout",
    }
    unclassified = emitted - _LAUNCH_REFUSALS - faults - not_launch_outcomes
    assert not unclassified, (
        "the launcher can emit error codes that are neither classified as a "
        f"refusal nor as a fault, so they answer 500 by default: {unclassified}"
    )
