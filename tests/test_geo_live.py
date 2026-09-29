"""What a page actually sees once timezone, language and position agree.

Measured before the fix, with the profile routed through a Brazilian exit::

    navigator.language  en-US        Intl locale      en-US
    timezone            America/New_York
    (1234.56).toLocaleString()       1,234.56
    getCurrentPosition()             the host's real surroundings
    Accept-Language on the wire      en-US,en;q=0.9

Every profile said that, from every proxy, because `create_profile` stamped
`America/New_York` and `en-US` on each one before anything knew where its
traffic would leave from.

An intermediate version aligned only the CDP overrides and not the
fingerprint, which was worse in an instructive way: the page then answered
``pt-BR`` to Intl and ``en-US`` to ``navigator.language`` **in the same
document**. One place has to be decided once, before anything reads it.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.network


def _chrome_available() -> bool:
    try:
        from multiprofile.browser.launcher import find_chrome

        find_chrome()
        return True
    except Exception:
        return False


pytest.importorskip("websocket")
if not _chrome_available():
    pytest.skip("Chrome is not installed", allow_module_level=True)

_PROBE = r"""
(async () => {
  const o = {};
  o.language = navigator.language;
  o.languages = navigator.languages.join(',');
  o.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  o.locale = Intl.DateTimeFormat().resolvedOptions().locale;
  o.number = (1234.56).toLocaleString();
  o.date = new Date(Date.UTC(2024, 0, 15)).toLocaleDateString();
  o.position = await new Promise(r => {
    try {
      navigator.geolocation.getCurrentPosition(
        p => r([p.coords.latitude, p.coords.longitude, p.coords.accuracy]),
        e => r('ERR:' + e.code), {timeout: 6000});
    } catch (e) { r('THREW:' + e.name); }
  });
  return JSON.stringify(o);
})()
"""


class _Page:
    def __init__(self) -> None:
        seen: list = []

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 - handler API
                seen.append(dict(self.headers))
                body = b"<!doctype html><title>g</title>ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.seen = seen
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/g"

    def close(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()


def _probe(country: str, tmp_path) -> dict:
    import websocket
    from _pytest.monkeypatch import MonkeyPatch

    import multiprofile.browser.launcher as L
    from multiprofile.browser.runtime_state import get_runtime
    from multiprofile.storage.profiles import create_profile, update_profile

    mp = MonkeyPatch()
    mp.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    mp.setenv("XDG_CACHE_HOME", str(tmp_path / "c"))
    mp.setenv("XDG_CONFIG_HOME", str(tmp_path / "g"))
    mp.setenv("MULTIPROFILE_NO_BACKGROUND", "1")
    mp.setenv("MULTIPROFILE_PROXY_CHECK_ENABLED", "0")

    page = _Page()
    pid = create_profile({"name": f"geo{country}", "proxyType": "none",
                          "startUrl": page.url})["id"]
    # What a real proxy check would have written.
    update_profile(pid, {"lastNetworkDiagnostics": {"countryCode": country}})
    try:
        assert L.launch_profile(pid, url=page.url, direct=True).get("ok") is True
        port = get_runtime(pid).cdp_port
        time.sleep(2.5)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json", headers={"Host": f"localhost:{port}"}
        )
        target = next(
            t for t in json.loads(urllib.request.urlopen(req, timeout=5).read())
            if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
        )
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=30, suppress_origin=True
        )
        # Geolocation is behind a permission in a real browser too; granting it
        # is the only way to observe whether the override took.
        ws.send(json.dumps({
            "id": 9, "method": "Browser.grantPermissions",
            "params": {"origin": page.url.rsplit("/", 1)[0],
                       "permissions": ["geolocation"]},
        }))
        deadline = time.time() + 10
        while time.time() < deadline:
            if json.loads(ws.recv()).get("id") == 9:
                break
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": _PROBE, "awaitPromise": True, "returnByValue": True},
        }))
        value = None
        deadline = time.time() + 40
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                value = ((msg.get("result") or {}).get("result") or {}).get("value")
                break
        ws.close()
        assert value, "the probe returned nothing"
        out = json.loads(value)
        out["accept_language"] = next(
            (h.get("Accept-Language") for h in reversed(page.seen)
             if h.get("Accept-Language")), None)
        return out
    finally:
        L.stop_profile(pid)
        page.close()
        mp.undo()


@pytest.fixture(scope="module")
def brazil(tmp_path_factory):
    return _probe("BR", tmp_path_factory.mktemp("br"))


@pytest.fixture(scope="module")
def germany(tmp_path_factory):
    return _probe("DE", tmp_path_factory.mktemp("de"))


def test_the_timezone_follows_the_exit(brazil, germany):
    assert brazil["timezone"] == "America/Sao_Paulo", brazil
    assert germany["timezone"] == "Europe/Berlin", germany


def test_navigator_language_follows_the_exit(brazil, germany):
    assert brazil["language"] == "pt-BR", brazil
    assert germany["language"] == "de-DE", germany


def test_intl_agrees_with_navigator(brazil, germany):
    """The intermediate version answered pt-BR to Intl and en-US to
    navigator.language in the same document."""
    for probe in (brazil, germany):
        assert probe["locale"] == probe["language"], probe


def test_formatting_follows_the_locale(brazil, germany):
    """Number and date formatting are Intl surfaces no script patches, which
    is why the locale has to be set on the engine rather than hooked."""
    assert brazil["number"] == "1.234,56", brazil["number"]
    assert germany["number"] == "1.234,56", germany["number"]
    assert brazil["date"] != germany["date"], (brazil["date"], germany["date"])


def test_accept_language_on_the_wire_follows_the_exit(brazil, germany):
    """--lang is what makes the very first request carry it, before any CDP
    session exists."""
    assert brazil["accept_language"].startswith("pt-BR"), brazil["accept_language"]
    assert germany["accept_language"].startswith("de-DE"), germany["accept_language"]


def test_the_position_is_in_the_claimed_country(brazil, germany):
    """Unset, getCurrentPosition resolved against the host -- the operator's
    real surroundings, identically for every profile on the machine."""
    lat, lon, _acc = brazil["position"]
    assert -26 < lat < -21 and -49 < lon < -44, brazil["position"]
    lat, lon, _acc = germany["position"]
    assert 50 < lat < 55 and 11 < lon < 16, germany["position"]


def test_the_position_does_not_claim_gps_accuracy(brazil):
    assert brazil["position"][2] >= 500, brazil["position"]


def test_two_exits_do_not_share_one_identity(brazil, germany):
    """The failure this whole item exists to fix: every profile claiming New
    York whatever proxy it was on."""
    assert brazil["timezone"] != germany["timezone"]
    assert brazil["language"] != germany["language"]
    assert brazil["position"][:2] != germany["position"][:2]
