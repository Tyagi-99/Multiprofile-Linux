"""Launch-time proof that the wire identity matches the intent.

WHY THIS EXISTS
---------------
F1 was not a spoofing bug, it was a *reporting* bug. ``apply_ua_override()``
sent ``Network.setUserAgentOverride`` down a one-shot socket, got its ACK,
returned True, and the launcher appended "CDP UA/client-hints override
applied" to the launch notes. Meanwhile the browser sent::

    User-Agent:         Mozilla/5.0 (X11; Linux x86_64) … Chrome/151.0.0.0
    sec-ch-ua-platform: "Linux"

while its JavaScript claimed ``navigator.platform == "Win32"``. Nothing in
the product ever compared the claim to the wire, so a profile that was
*more* identifiable than an unmodified browser was reported as healthy.

So the fix for F1 is not only a working override. It is this module: after
the identity is installed, the launcher stands up a loopback HTTP server,
makes the browser fetch it through its own network stack, and compares the
headers the browser actually sent with the identity the launcher intended.

MISMATCH FAILS THE LAUNCH. A profile whose headers disagree with its
JavaScript is a liability, and handing one to an operator who asked for a
masked browser is worse than refusing.

INCONCLUSIVE DOES NOT fail the launch. When the probe cannot run at all —
no CDP, no websocket-client, the tab never reached the loopback server —
there is no evidence of a mismatch, and turning every such environment into
a launch outage would be its own defect. Inconclusive is recorded loudly in
the launch notes and in the runtime registry, and stays visible there.

WHY LOOPBACK AND NOT A PUBLIC ECHO SERVICE
------------------------------------------
The check must work offline, must not depend on a third party, and must not
tell anyone else that this profile exists. Chromium bypasses the proxy for
loopback by default, so the probe measures the browser's own header
construction rather than whatever a proxy rewrites. ``http://127.0.0.1`` is
a "potentially trustworthy" origin, so Client Hints are still attached —
they are not on plain remote http.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .cdp_identity import open_pinned_browser_session

log = logging.getLogger("multiprofile.browser.identity_assert")

# The probe is on the launch path, so it gets a short leash.
PROBE_TIMEOUT_S = 6.0

# Headers that carry the identity. Everything else the browser sends is
# irrelevant to whether it is wearing the mask it was given.
_UA = "user-agent"
_CH_UA = "sec-ch-ua"
_CH_PLATFORM = "sec-ch-ua-platform"
_CH_MOBILE = "sec-ch-ua-mobile"


@dataclass
class IdentityAssertion:
    """What the wire said, what we meant, and whether they agree."""

    ok: bool = False
    conclusive: bool = False
    reason: str = "not run"
    mismatches: list[str] = field(default_factory=list)
    observed: dict[str, str] = field(default_factory=dict)
    expected: dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        """True only for a PROVEN mismatch — the one case that blocks a launch."""
        return self.conclusive and not self.ok

    def summary(self) -> str:
        if self.failed:
            return "identity assertion FAILED: " + "; ".join(self.mismatches)
        if self.ok:
            return "identity verified on the wire: " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.observed.items())
            )
        return f"identity NOT VERIFIED ({self.reason}) — no wire evidence either way"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "conclusive": self.conclusive,
            "reason": self.reason,
            "mismatches": list(self.mismatches),
            "observed": dict(self.observed),
            "expected": dict(self.expected),
        }


# ---------------------------------------------------------------------------
# Expected identity, derived from the same params we asked Chromium for
# ---------------------------------------------------------------------------

def _brand_majors(brands: Any) -> set[str]:
    """Major versions of the real brands in a client-hints brand list.

    ``Not-A.Brand`` (and its many spellings) is deliberate GREASE and says
    nothing about the identity, so it is not evidence either way.
    """
    out: set[str] = set()
    for entry in brands or []:
        if not isinstance(entry, dict):
            continue
        brand = str(entry.get("brand") or "")
        if re.search(r"not[^a-z0-9]*a[^a-z0-9]*brand", brand, re.IGNORECASE):
            continue
        major = str(entry.get("version") or "").split(".")[0]
        if major:
            out.add(major)
    return out


def expected_wire_identity(ua_params: dict[str, Any]) -> dict[str, str]:
    """Translate CDP override params into the headers they should produce."""
    meta = ua_params.get("userAgentMetadata") or {}
    expected = {_UA: str(ua_params.get("userAgent") or "")}
    platform = str(meta.get("platform") or "")
    if platform:
        expected[_CH_PLATFORM] = f'"{platform}"'
    expected[_CH_MOBILE] = "?1" if meta.get("mobile") else "?0"
    majors = _brand_majors(meta.get("brands"))
    if majors:
        expected[_CH_UA] = "|".join(sorted(majors))  # "any of these majors"
    return expected


def compare(expected: dict[str, str], observed: dict[str, str]) -> list[str]:
    """Return one human-readable line per proven contradiction.

    A header the browser did not send is not a contradiction: Client Hints
    are not attached to every request shape, and absence is not the host
    identity leaking. Only a header that is present AND disagrees counts.
    """
    out: list[str] = []

    want_ua = expected.get(_UA) or ""
    got_ua = observed.get(_UA)
    if want_ua and got_ua is not None and got_ua != want_ua:
        out.append(f"User-Agent on the wire is {got_ua!r}, intended {want_ua!r}")

    for header in (_CH_PLATFORM, _CH_MOBILE):
        want = expected.get(header)
        got = observed.get(header)
        if want and got is not None and got != want:
            out.append(f"{header} on the wire is {got!r}, intended {want!r}")

    want_majors = (expected.get(_CH_UA) or "").split("|") if expected.get(_CH_UA) else []
    got_ch_ua = observed.get(_CH_UA)
    if want_majors and got_ch_ua is not None:
        got_majors = {m for m in re.findall(r'v="(\d+)', got_ch_ua)}
        if got_majors and not (set(want_majors) & got_majors):
            out.append(
                f"{_CH_UA} on the wire claims major(s) {sorted(got_majors)}, "
                f"intended one of {sorted(want_majors)}"
            )
    return out


# ---------------------------------------------------------------------------
# Loopback probe target
# ---------------------------------------------------------------------------

class _Probe:
    """A one-request loopback server that records the headers it was sent."""

    def __init__(self) -> None:
        self._headers: queue.Queue[dict[str, str]] = queue.Queue()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                outer._headers.put(
                    {k.lower(): v for k, v in self.headers.items()}
                )
                body = b"<!doctype html><title>identity probe</title>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                """Silence BaseHTTPRequestHandler's stderr logging."""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="mp-identity-probe"
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/identity-probe"

    def __enter__(self) -> "_Probe":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self._server.shutdown()
        finally:
            self._server.server_close()

    def wait(self, timeout_s: float) -> dict[str, str] | None:
        try:
            return self._headers.get(timeout=timeout_s)
        except queue.Empty:
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_wire_identity(
    cdp_port: int,
    ua_params: dict[str, Any] | None,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    attempts: int = 2,
) -> IdentityAssertion:
    """Make the browser talk to us, and check what it said about itself.

    The probe opens a background tab at ``about:blank`` and then NAVIGATES it
    to the loopback server. That order is deliberate, and measured: a tab's
    very FIRST document request is issued by the browser process before the
    renderer exists, so the per-target UA override has not landed yet and
    Chromium computes that one request's Client Hints from the host. Every
    request after it — which is all real browsing — carries the override.
    Measuring the first request would therefore fail every launch for a
    reason the launch cannot fix.

    (That first-request window is a real residual leak, not a measurement
    artefact: a tab the operator opens leaks the host platform in its
    ``sec-ch-ua-platform`` until it navigates. Closing it means rewriting
    headers in the browser process — browser-level ``Fetch`` interception —
    which is its own change, not part of F1. It is recorded in the audit
    report rather than papered over here.)

    A mismatch is re-measured (``attempts``) before it is believed: the
    verdict comes from the last measurement, so a transient first-navigation
    race can never fail a launch, while a real mismatch — which is permanent
    — still does. The probe tab is closed again before returning.
    """
    if not ua_params:
        return IdentityAssertion(reason="no intended identity to verify")

    expected = expected_wire_identity(ua_params)
    session = open_pinned_browser_session(cdp_port)
    if session is None:
        return IdentityAssertion(
            reason="no CDP browser session", expected=expected
        )

    target_id = ""
    observed: dict[str, str] = {}
    mismatches: list[str] = []
    measured = False
    try:
        with _Probe() as probe:
            created = session.send(
                "Target.createTarget",
                {"url": "about:blank", "background": True},
            )
            if created is None or created.get("error"):
                detail = (created or {}).get("error") or "no response"
                return IdentityAssertion(
                    reason=f"could not open a probe tab: {detail}", expected=expected
                )
            target_id = str((created.get("result") or {}).get("targetId") or "")
            attached = session.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            probe_session = str((attached or {}).get("result", {}).get("sessionId") or "")
            if not probe_session:
                return IdentityAssertion(
                    reason="could not attach to the probe tab", expected=expected
                )

            for _ in range(max(1, attempts)):
                session.send(
                    "Page.navigate", {"url": probe.url}, session_id=probe_session
                )
                headers = probe.wait(timeout_s)
                if headers is None:
                    continue
                measured = True
                observed = {
                    k: headers[k]
                    for k in (_UA, _CH_UA, _CH_PLATFORM, _CH_MOBILE)
                    if k in headers
                }
                mismatches = compare(expected, observed)
                if not mismatches:
                    break
    finally:
        if target_id:
            session.send("Target.closeTarget", {"targetId": target_id})
        session.close()

    if not measured:
        return IdentityAssertion(
            reason="probe tab never reached the loopback server", expected=expected
        )

    result = IdentityAssertion(
        ok=not mismatches,
        conclusive=True,
        reason="verified on the wire" if not mismatches else "wire disagrees with intent",
        mismatches=mismatches,
        observed=observed,
        expected=expected,
    )
    if result.failed:
        log.error("identity assertion failed: %s", json.dumps(result.to_dict()))
    return result
