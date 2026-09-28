"""CDP-level browser identity configuration.

Sets navigator.userAgent, Sec-CH-UA-* HTTP headers, and navigator.platform
consistently via Chrome DevTools Protocol so that JS-level and HTTP-level
identity match.

Why this matters:
    --user-agent only sets the initial UA string.
    Sec-CH-UA-Platform, Sec-CH-UA-Arch, Sec-CH-UA-Bitness are sent by Chrome
    itself at the network layer.  If the JS UA says 'Windows' but the HTTP
    headers say 'Linux', advanced bot-detection identifies the mismatch.

    Network.setUserAgentOverride with userAgentMetadata aligns both surfaces.

    It only aligns them for as long as the DevTools session that sent it
    stays open. Everything in this module is therefore built around a
    caller-held session: :class:`PinnedCdpSession`, and an
    :func:`apply_ua_override` that refuses to run without one.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from typing import Any

from ..engine import detect_engine_version

log = logging.getLogger("multiprofile.browser.cdp_identity")

_CDP_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Low-level CDP helpers
# ---------------------------------------------------------------------------

def _cdp_get(port: int, path: str) -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    # NOTE: Chromium builds webSocketDebuggerUrl from the request's Host
    # header. Sending "Host: localhost" WITHOUT the port makes Chrome return
    # "ws://localhost/devtools/..." (port 80) which can never connect. The
    # port must be included or every WebSocket send silently fails.
    req = urllib.request.Request(url, headers={"Host": f"localhost:{port}"})
    with urllib.request.urlopen(req, timeout=_CDP_TIMEOUT) as r:
        return json.loads(r.read())


class PinnedCdpSession:
    """One CDP WebSocket pinned to a page target, kept OPEN by the caller.

    Chromium scopes ``Network.setUserAgentOverride`` and
    ``Emulation.setTouchEmulationEnabled`` to the DevTools session that set
    them: the moment that session disconnects, the overrides vanish and
    page readback reverts to host values (verified empirically against
    real Chromium: override ACKed on session A, visible on session A, gone
    after A closes). Only ``Emulation.setDeviceMetricsOverride`` survives a
    disconnect.

    Consequence: one-shot "connect, send, close" helpers can NEVER make
    UA/touch emulation persist. Any caller that needs those overrides to
    stay applied must hold a PinnedCdpSession open for as long as the
    emulation should apply and close it explicitly when done.

    This module deliberately ships NO one-shot sender. It used to, and
    :func:`apply_ua_override` used it: the override was ACKed, reported as
    applied, and discarded microseconds later when the socket closed, so
    every profile without a device preset browsed with the host's own
    User-Agent and Client Hints while its JavaScript claimed otherwise.
    The helper is gone rather than merely documented, because a footgun
    that only a comment guards gets picked up again.
    """

    def __init__(self, ws: Any, target_url: str):
        self._ws = ws
        self.target_url = target_url
        self._next_id = 0
        self._closed = False

    @property
    def is_open(self) -> bool:
        """True while this session can still carry a command.

        Session-scoped overrides live and die with the session, so callers
        that promise persistence must be able to ask.
        """
        return not self._closed

    def send(
        self,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Send one CDP command on the held session; return the response.

        Matches ``apply_cdp_plan``'s ``send(method, params) -> dict`` sender
        contract. ``session_id`` addresses a flat child session (a target
        attached through this one). Returns None when the session is closed
        or the command gets no response within the timeout.
        """
        if self._closed:
            return None
        try:
            self._next_id += 1
            msg_id = self._next_id
            msg: dict[str, Any] = {"id": msg_id, "method": method, "params": params}
            if session_id:
                msg["sessionId"] = session_id
            self._ws.send(json.dumps(msg))
            deadline = time.monotonic() + _CDP_TIMEOUT
            while time.monotonic() < deadline:
                raw = self._ws.recv()
                if not raw:
                    break
                obj = json.loads(raw)
                if obj.get("id") == msg_id:
                    return obj
        except Exception as e:  # noqa: BLE001 - a dead session is a None response
            log.debug("PinnedCdpSession send failed: %s", e)
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001 - best effort
            pass

    def __enter__(self) -> "PinnedCdpSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def open_pinned_cdp_session(port: int) -> PinnedCdpSession | None:
    """Open a CDP session pinned to the best page target; stays open.

    The caller OWNS the session lifetime: keep it open while UA/touch
    emulation must apply (see :class:`PinnedCdpSession`), close it when
    done. Returns None when CDP is unreachable, has no page target, or
    the websocket-client package is missing.
    """
    try:
        targets = _cdp_get(port, "/json")
    except Exception as e:
        log.debug("CDP /json failed on port %s: %s", port, e)
        return None
    picked_url: str | None = None
    picked_page_url = ""
    fallback_url: str | None = None
    fallback_page_url = ""
    for t in targets:
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        ws_url = t.get("webSocketDebuggerUrl")
        if not ws_url:
            continue
        if url.startswith("chrome://") or url.startswith("chrome-extension://"):
            if fallback_url is None:
                fallback_url, fallback_page_url = ws_url, url
            continue
        picked_url, picked_page_url = ws_url, url
        break
    ws_url = picked_url or fallback_url
    if not ws_url:
        return None
    try:
        import websocket  # type: ignore
    except ImportError:
        log.debug("websocket-client not installed; install python3-websocket")
        return None
    try:
        ws = websocket.WebSocket()
        # No Origin header, so Chrome's default DevTools origin check can
        # stay on and --remote-allow-origins=* is no longer needed.
        ws.connect(ws_url, timeout=_CDP_TIMEOUT, suppress_origin=True)
    except Exception as e:  # noqa: BLE001
        log.debug("CDP pinned session connect failed on port %s: %s", port, e)
        return None
    return PinnedCdpSession(ws, picked_page_url or fallback_page_url)


def open_pinned_browser_session(port: int) -> PinnedCdpSession | None:
    """Open a session on the BROWSER endpoint; stays open, caller-owned.

    The browser endpoint is the only one that can create and close targets,
    which is what the launch-time identity probe needs (see
    :mod:`multiprofile.browser.identity_assert`). Returns None when CDP is
    unreachable or websocket-client is missing.
    """
    try:
        version = _cdp_get(port, "/json/version")
    except Exception as e:  # noqa: BLE001 - an unreachable port is a None session
        log.debug("CDP /json/version failed on port %s: %s", port, e)
        return None
    ws_url = (version or {}).get("webSocketDebuggerUrl") or ""
    if not ws_url:
        return None
    try:
        import websocket  # type: ignore
    except ImportError:
        log.debug("websocket-client not installed; install python3-websocket")
        return None
    try:
        ws = websocket.WebSocket()
        # No Origin header, so Chrome's default DevTools origin check can
        # stay on and --remote-allow-origins=* is no longer needed.
        ws.connect(ws_url, timeout=_CDP_TIMEOUT, suppress_origin=True)
    except Exception as e:  # noqa: BLE001
        log.debug("CDP browser session connect failed on port %s: %s", port, e)
        return None
    return PinnedCdpSession(ws, "browser")


# ---------------------------------------------------------------------------
# Brand list builder
# ---------------------------------------------------------------------------

def _build_brands(chrome_version: str) -> list[dict[str, str]]:
    """Return Sec-CH-UA brands list for a given Chrome major version.

    The real list varies; this produces a realistic set.
    """
    major = chrome_version.split(".")[0] if "." in chrome_version else chrome_version
    try:
        major_int = int(major)
    except ValueError:
        major_int = 131
    # Typical pattern: "Google Chrome", "Chromium", and a dummy brand
    return [
        {"brand": "Google Chrome", "version": str(major_int)},
        {"brand": "Chromium", "version": str(major_int)},
        {"brand": "Not-A.Brand", "version": "99"},
    ]


def _build_full_version_list(chrome_version: str) -> list[dict[str, str]]:
    major = chrome_version.split(".")[0] if "." in chrome_version else chrome_version
    try:
        major_int = int(major)
    except ValueError:
        major_int = 131
    full = chrome_version if "." in chrome_version else f"{major_int}.0.0.0"
    return [
        {"brand": "Google Chrome", "version": full},
        {"brand": "Chromium", "version": full},
        {"brand": "Not-A.Brand", "version": "99.0.0.0"},
    ]


# ---------------------------------------------------------------------------
# UA metadata extractor from fingerprint
# ---------------------------------------------------------------------------

def _extract_ua_metadata(fingerprint: dict[str, Any]) -> dict[str, Any]:
    """Build CDP userAgentMetadata from the fingerprint dict."""
    platform = fingerprint.get("platform") or "Win32"
    ch_platform = fingerprint.get("ch_platform") or (
        "Windows" if "Win" in platform else
        "macOS" if "Mac" in platform else
        "Linux"
    )
    architecture = fingerprint.get("architecture") or "x86"
    bitness = fingerprint.get("bitness") or "64"
    platform_version = fingerprint.get("platformVersion") or "15.0.0"
    is_mobile = fingerprint.get("touch", 0) > 0

    # Extract the Chromium version from the UA string. The fallback is the
    # installed engine, not a literal: this branch is only reached when the
    # fingerprint carries no brand lists, and inventing Chrome 131 there was
    # the same stale claim F5 is about. CriOS/Edg count — the iPhone pool's
    # UA has no "Chrome/" token at all.
    ua_str = fingerprint.get("userAgent") or ""
    engine = detect_engine_version()
    chrome_version = engine.full or "131.0.0.0"
    m = re.search(r"\b(?:Chrome|Chromium|CriOS|Edg|EdgA|EdgiOS|HeadlessChrome)/(\d+\.\d+\.\d+\.\d+)", ua_str)
    if m:
        chrome_version = m.group(1)

    # Use pre-built brands from fingerprint if available, else generate
    brands = fingerprint.get("ch_brands") or _build_brands(chrome_version)
    full_version_list = fingerprint.get("ch_full_version_list") or _build_full_version_list(chrome_version)

    return {
        "brands": brands,
        "fullVersionList": full_version_list,
        "platform": ch_platform,
        "platformVersion": platform_version,
        "architecture": architecture,
        "model": fingerprint.get("model") or "",
        "mobile": is_mobile,
        "bitness": bitness,
        "wow64": False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

UA_OVERRIDE_METHOD = "Network.setUserAgentOverride"


def build_ua_override_params(
    fingerprint: dict[str, Any],
    ua_string: str = "",
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the ``Network.setUserAgentOverride`` params for one identity.

    Pure: no I/O, no session. This is the single description of what the
    profile is supposed to look like on the wire, so the thing that applies
    it and the thing that verifies it (``identity_assert``) cannot drift
    apart. Returns None when there is no UA string to claim at all — the
    caller must then treat the profile as unmasked rather than launch it
    and hope.

    ``metadata`` overrides the client-hint block derived from the
    fingerprint; the device-preset path passes the preset's own hints so
    the headers and the preset agree.
    """
    ua = ua_string or fingerprint.get("userAgent") or ""
    if not ua:
        return None
    # acceptLanguage is a language LIST, not a finished header: Chromium adds
    # the q-weights itself. Passing "en-US,en;q=0.9" produced
    # "en-US,en;q=0.9;q=0.9" on the wire — a malformed header no real browser
    # sends, and therefore a tell. Measured once the override started
    # applying at all; invisible while it was being discarded (F1).
    languages = fingerprint.get("languages") or []
    if not isinstance(languages, list) or not languages:
        languages = [fingerprint.get("language") or "en-US"]
    return {
        "userAgent": ua,
        "acceptLanguage": ",".join(str(x) for x in languages if x),
        "platform": fingerprint.get("platform") or "Win32",
        "userAgentMetadata": metadata or _extract_ua_metadata(fingerprint),
    }


def _brand_list_header(brands: Any) -> str:
    """Format a client-hints brand list the way Chrome puts it on the wire."""
    parts = []
    for entry in brands or []:
        if not isinstance(entry, dict):
            continue
        brand = str(entry.get("brand") or "").strip()
        version = str(entry.get("version") or "").strip()
        if brand and version:
            parts.append(f'"{brand}";v="{version}"')
    return ", ".join(parts)


def low_entropy_client_hints(ua_params: dict[str, Any] | None) -> dict[str, str]:
    """The headers Chrome sends unasked, built from the intended identity.

    These are the three low-entropy hints every request carries --
    ``sec-ch-ua``, ``sec-ch-ua-mobile``, ``sec-ch-ua-platform`` -- plus the
    User-Agent. They exist as a *header* builder because
    ``Network.setUserAgentOverride`` is scoped to a renderer target, and a new
    tab's first document request is issued by the **browser process** before
    that override lands. Measured on Chromium 151, a new tab's first request::

        intended : sec-ch-ua "Google Chrome";v="151", "Chromium";v="151", …
                   sec-ch-ua-platform "Windows"
        actual   : sec-ch-ua "Chromium";v="151", "Not=A?Brand";v="99"
                   sec-ch-ua-platform "Linux"

    The User-Agent was right (it comes from the process flag) while the brand
    list was the host's own -- no "Google Chrome", and Chromium's GREASE
    spelling -- next to a Windows UA. That pairing is a contradiction no real
    browser produces, which is the F1 class of defect all over again.

    Derived from the same ``ua_params`` the override uses, so the header the
    request carries and the identity the page reports cannot drift apart.
    """
    if not ua_params:
        return {}
    meta = ua_params.get("userAgentMetadata") or {}
    headers: dict[str, str] = {}

    ua = str(ua_params.get("userAgent") or "").strip()
    if ua:
        headers["User-Agent"] = ua

    brands = _brand_list_header(meta.get("brands"))
    if brands:
        headers["sec-ch-ua"] = brands

    headers["sec-ch-ua-mobile"] = "?1" if meta.get("mobile") else "?0"

    platform = str(meta.get("platform") or "").strip()
    if platform:
        headers["sec-ch-ua-platform"] = f'"{platform}"'

    return headers


def apply_ua_override(
    session: PinnedCdpSession | None,
    fingerprint: dict[str, Any],
    ua_string: str = "",
    *,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Apply UA + client-hint metadata on an OPEN, CALLER-HELD CDP session.

    ``session`` must stay open for as long as the override should apply.
    Chromium scopes ``Network.setUserAgentOverride`` to the DevTools
    session that set it (see :class:`PinnedCdpSession`), so an override
    sent on a session that is about to close is not an override at all.

    This function therefore **returns False whenever it does not hold an
    open session**, instead of reporting the success the ACK would suggest.
    That is the whole point: the previous version sent the command down a
    one-shot socket, got its ACK, returned True, and the launcher logged
    "CDP UA/client-hints override applied" while the browser went on
    sending the host's own User-Agent and ``sec-ch-ua-platform``.
    """
    if session is None or not session.is_open:
        log.warning(
            "refusing UA override without an open CDP session: a session-scoped "
            "override would be discarded and the profile would browse as the host"
        )
        return False

    params = build_ua_override_params(fingerprint, ua_string, metadata=metadata)
    if params is None:
        log.warning("refusing UA override with no User-Agent to claim")
        return False

    result = session.send(UA_OVERRIDE_METHOD, params)
    if result is None:
        log.warning("CDP UA override got no response on a held session")
        return False
    if result.get("error"):
        log.warning("CDP UA override error: %s", result["error"])
        return False
    meta = params["userAgentMetadata"]
    log.info(
        "CDP UA override applied on a held session: platform=%s arch=%s bitness=%s",
        meta.get("platform"),
        meta.get("architecture"),
        meta.get("bitness"),
    )
    return True
