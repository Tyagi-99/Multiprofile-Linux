"""Unit guards for the first-request client-hint rewrite.

The live evidence is in ``test_first_request_hints_live.py``. These pin the two
properties that do not need a browser and that would be expensive to learn the
hard way.

1. **The identity probe is never rewritten.** It is the one document request a
   launch makes, and it exists to observe what the browser *would* send. When
   the rewrite was first added it caught the probe too, which made the
   launch-time assertion tautological: it reported "identity verified" from
   headers the injector had just written, and would have kept reporting it with
   the UA override completely dead. F1 was a reporting bug before it was a
   spoofing bug, and that is exactly how it would come back.

2. **Every paused request is released.** A rewrite that throws must still
   continue the request; a hung tab is a worse bug than the leak.
"""
from __future__ import annotations

from multiprofile.browser.cdp_identity import (
    IDENTITY_PROBE_PATH,
    low_entropy_client_hints,
)
from multiprofile.browser.cdp_inject import IdentityInjector

UA_PARAMS = {
    "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0.0.0 Safari/537.36",
    "userAgentMetadata": {
        "brands": [
            {"brand": "Google Chrome", "version": "151"},
            {"brand": "Not-A.Brand", "version": "99"},
        ],
        "platform": "Windows",
        "mobile": False,
    },
}


class _Recorder(IdentityInjector):
    """An injector that records commands instead of sending them."""

    def __init__(self, **kw):
        super().__init__(port=0, ua_override=UA_PARAMS, **kw)
        self.sent: list[tuple[str, dict]] = []

    def _send(self, method, params=None, session_id=None):  # type: ignore[override]
        self.sent.append((method, params or {}))
        return 0


def _pause(url: str) -> dict:
    return {"requestId": "req-1", "request": {"url": url, "headers": {"Accept": "*/*"}}}


# --------------------------------------------------------------------------
# 1. The probe is observed, not corrected
# --------------------------------------------------------------------------

def test_the_identity_probe_is_released_untouched():
    inj = _Recorder()
    inj._on_request_paused(_pause(f"http://127.0.0.1:9/{IDENTITY_PROBE_PATH.lstrip('/')}"), None)

    (method, params), = inj.sent
    assert method == "Fetch.continueRequest"
    assert "headers" not in params, "the probe's headers were rewritten"


def test_an_ordinary_request_is_corrected():
    """The counterpart -- otherwise the exemption could swallow everything."""
    inj = _Recorder()
    inj._on_request_paused(_pause("https://example.com/page"), None)

    (method, params), = inj.sent
    assert method == "Fetch.continueRequest"
    names = {h["name"].lower(): h["value"] for h in params["headers"]}
    assert names["sec-ch-ua-platform"] == '"Windows"'
    assert "Google Chrome" in names["sec-ch-ua"]


def test_the_rewrite_replaces_rather_than_duplicates():
    """A header the browser spelled differently must not survive alongside ours."""
    inj = _Recorder()
    params = _pause("https://example.com/page")
    params["request"]["headers"]["Sec-CH-UA-Platform"] = '"Linux"'
    inj._on_request_paused(params, None)

    (_m, sent), = inj.sent
    platforms = [h for h in sent["headers"] if h["name"].lower() == "sec-ch-ua-platform"]
    assert len(platforms) == 1, platforms
    assert platforms[0]["value"] == '"Windows"'


def test_other_headers_are_preserved():
    inj = _Recorder()
    params = _pause("https://example.com/page")
    params["request"]["headers"]["X-Something"] = "keep me"
    inj._on_request_paused(params, None)

    (_m, sent), = inj.sent
    names = {h["name"]: h["value"] for h in sent["headers"]}
    assert names["X-Something"] == "keep me"


# --------------------------------------------------------------------------
# 2. The request is always released
# --------------------------------------------------------------------------

def test_a_failing_rewrite_still_continues_the_request():
    """A hung tab would be far worse than the leak this fixes."""
    inj = _Recorder()

    class Hostile:
        """Truthy, and explodes the moment the rewrite reads it."""

        def __bool__(self) -> bool:
            return True

        def __iter__(self):
            raise RuntimeError("boom")

    inj.hint_headers = Hostile()  # type: ignore[assignment]
    inj._on_request_paused(_pause("https://example.com/page"), None)

    (method, params), = inj.sent
    assert method == "Fetch.continueRequest"
    assert params == {"requestId": "req-1"}, "the request was not released cleanly"


def test_a_request_with_no_id_is_ignored_quietly():
    inj = _Recorder()
    inj._on_request_paused({"request": {"url": "https://example.com/"}}, None)
    assert inj.sent == []


# --------------------------------------------------------------------------
# 3. The header builder
# --------------------------------------------------------------------------

def test_the_headers_come_from_the_same_params_as_the_override():
    """Two sources would be two identities."""
    headers = low_entropy_client_hints(UA_PARAMS)
    assert headers["sec-ch-ua-platform"] == '"Windows"'
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert headers["User-Agent"] == UA_PARAMS["userAgent"]


def test_no_override_means_no_headers():
    assert low_entropy_client_hints(None) == {}
    assert low_entropy_client_hints({}) == {}


def test_a_mobile_identity_says_so():
    params = {**UA_PARAMS, "userAgentMetadata": {**UA_PARAMS["userAgentMetadata"], "mobile": True}}
    assert low_entropy_client_hints(params)["sec-ch-ua-mobile"] == "?1"
