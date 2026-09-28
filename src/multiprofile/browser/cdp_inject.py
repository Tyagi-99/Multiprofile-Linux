"""Identity injection over CDP — the replacement for the browser extension.

WHY THIS EXISTS
---------------
The per-profile MV3 extension was responsible for three things: running the
fingerprint script in the page, answering proxy authentication challenges,
and seeding cookies. It did none of them, because Google Chrome refuses the
flags that load it::

    WARNING:chrome/browser/extensions/extension_service.cc:441]
    --disable-extensions-except is not allowed in Google Chrome, ignoring.

Verified on Chrome 151: zero of our extensions appear in the profile's
``Preferences``, and ``window.__MP_PROFILE__`` is undefined on a real
``http://`` page. Profiles were browsing with the host's own fingerprint
while the console reported them healthy, and authenticated proxies could
never complete a challenge.

CDP can do all three on stock Chrome, with no extension and no flags:

    Page.addScriptToEvaluateOnNewDocument   fingerprint, before page script
    Fetch.enable{handleAuthRequests:true}   proxy auth, password off argv
    Network.setCookies                      cookie seeding

It also carries the fourth job the extension never had: the HTTP-layer
identity. ``Network.setUserAgentOverride`` is applied here, per attached
target, because this is the only session that lives as long as the browser
AND sees every tab. Anywhere else it is discarded (see F1 in
``docs/02_AUDIT_REPORT.md``) or covers only the first tab.

THE SESSION MUST STAY OPEN
--------------------------
``cdp_identity.PinnedCdpSession`` documents the rule this module obeys:
Chromium scopes overrides to the DevTools session that set them, and they
vanish when it disconnects. So this runs a long-lived connection on a daemon
thread for the whole life of the browser, not a connect-send-close helper.

NEW TABS ARE COVERED
--------------------
``Target.setAutoAttach`` with ``waitForDebuggerOnStart`` catches every new
page *before* it runs a line of script, so a tab opened by the operator --
or by ``window.open`` -- gets the same identity as the first one.

CREDENTIALS
-----------
The proxy password is held in memory for the lifetime of the browser and is
written to no log, no command line and no file. ``__repr__`` is overridden
so a traceback cannot print it either.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import identity_faults
from .cdp_identity import (
    IDENTITY_PROBE_PATH,
    UA_OVERRIDE_METHOD,
    low_entropy_client_hints,
)

log = logging.getLogger("multiprofile.browser.cdp_inject")

try:
    import websocket
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    websocket = None  # type: ignore

_CONNECT_TIMEOUT = 8.0
_RECV_TIMEOUT = 1.0


def _first_request_hints_enabled() -> bool:
    """Whether to arm browser-scope Fetch for the first-request hint fix.

    Read at attach time rather than captured at import, so a test or an
    operator can turn it off without rebuilding anything. Never raises: a
    missing config module must not stop a browser launching.
    """
    try:
        from ..config import get_settings

        return bool(get_settings().first_request_hints)
    except Exception:  # noqa: BLE001 - default to the safe (correct) behaviour
        return True


def build_identity_script(
    fingerprint: dict[str, Any],
    evasion_source: str,
    *,
    replacements: dict[str, str] | None = None,
) -> str:
    """Prefix the evasion source with this profile's config.

    This is now the ONLY way ``evasion.js`` reaches a page. The per-profile
    extension copy that used to do the same job is gone (Chromium honoured
    ``--load-extension`` while Chrome refused it, so the hooks were installed
    twice on one engine and once on the other), which makes this function the
    single source of truth for what the page sees — including the
    ``__PROFILE_*`` tokens the staging step used to substitute. Tokens left
    unreplaced default themselves inside the script, so a missing one is
    survivable, but the caller should pass them.
    """
    cfg = json.dumps(fingerprint, separators=(",", ":"))
    prelude = f"var __MP_INLINE_CFG__ = {cfg};\nwindow.__MP_PROFILE__ = __MP_INLINE_CFG__;\n"
    body = evasion_source
    for token, value in (replacements or {}).items():
        body = body.replace(token, value)
    if "__PROFILE_CONFIG_JSON__" in body:
        body = body.replace('"__PROFILE_CONFIG_JSON__"', "null")
        body = body.replace("'__PROFILE_CONFIG_JSON__'", "null")
    return prelude + body


@dataclass
class ProxyAuth:
    """Proxy credentials held in memory only."""

    username: str
    password: str

    def __repr__(self) -> str:  # never let a traceback print the password
        return f"ProxyAuth(username={self.username!r}, password=<redacted>)"


@dataclass
class InjectionStatus:
    """What actually happened, for the runtime registry and the console."""

    ok: bool = False
    script_installed: bool = False
    auth_armed: bool = False
    cookies_applied: int = 0
    ua_override_applied: bool = False
    timezone_applied: bool = False
    targets_seen: int = 0
    error: str | None = None
    detail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "scriptInstalled": self.script_installed,
            "authArmed": self.auth_armed,
            "cookiesApplied": self.cookies_applied,
            "uaOverrideApplied": self.ua_override_applied,
            "timezoneApplied": self.timezone_applied,
            "targetsSeen": self.targets_seen,
            "error": self.error,
            "detail": list(self.detail),
        }


def _browser_ws_url(port: int, timeout_s: float = _CONNECT_TIMEOUT) -> str:
    """Browser-level endpoint — the only one that can auto-attach targets."""
    deadline = time.monotonic() + timeout_s
    last = "not attempted"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.0
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            url = data.get("webSocketDebuggerUrl") or ""
            if url:
                return url
        except Exception as e:  # noqa: BLE001 - retry until the deadline
            last = str(e)
        time.sleep(0.15)
    raise RuntimeError(f"CDP browser endpoint not ready on 127.0.0.1:{port}: {last}")


class IdentityInjector:
    """Holds one browser-level CDP connection and applies identity to every page."""

    def __init__(
        self,
        port: int,
        *,
        script: str | None = None,
        proxy_auth: ProxyAuth | None = None,
        cookies: list[dict[str, Any]] | None = None,
        ua_override: dict[str, Any] | None = None,
        timezone_id: str | None = None,
        profile_id: str = "",
    ):
        self.port = int(port)
        self.timezone_id = (timezone_id or "").strip() or None
        self.script = script or ""
        self.proxy_auth = proxy_auth
        self.cookies = cookies or []
        self.ua_override = ua_override or None
        self.profile_id = profile_id
        # Derived from the override rather than passed in, so the headers a
        # paused request is given and the identity the page reports come from
        # one source and cannot drift apart.
        self.hint_headers = low_entropy_client_hints(self.ua_override)
        self.status = InjectionStatus()

        self._ws: Any = None
        self._id = 0
        self._pending: dict[int, str] = {}
        self._attached: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._cookies_done = False

    # -- wire ------------------------------------------------------------

    def _send(self, method: str, params: dict[str, Any] | None = None,
              session_id: str | None = None) -> int:
        """Fire a command. Responses are handled by the receive loop."""
        with self._lock:
            self._id += 1
            msg_id = self._id
            msg: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            self._pending[msg_id] = method
            self._ws.send(json.dumps(msg))
        return msg_id

    def _existing_page_targets(self) -> list[str]:
        """Page targets already open, read over the HTTP endpoint."""
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json/list", timeout=2.0
            ) as resp:
                items = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - auto-attach still covers new tabs
            log.debug("could not enumerate existing targets for %s: %s", self.profile_id, e)
            # Auto-attach covers targets opened from here on, so this is not
            # fatal -- but any page ALREADY open goes uninjected, and an
            # uninjected page is one browsing as the host (F11).
            identity_faults.record(
                "targets_unenumerable",
                f"already-open page targets could not be listed ({e}); the hooks were "
                "installed on new targets only, so any page already open is unmasked",
                where="browser.cdp_inject._existing_page_targets",
                profile_id=self.profile_id,
            )
            return []
        return [
            t["id"]
            for t in items
            if t.get("type") == "page" and t.get("id")
            and not str(t.get("url", "")).startswith("devtools://")
        ]

    # -- lifecycle -------------------------------------------------------

    def start(self, timeout_s: float = _CONNECT_TIMEOUT) -> InjectionStatus:
        """Connect, arm auto-attach, and run the event loop on a daemon thread."""
        if websocket is None:
            self.status.error = "websocket-client is not installed"
            return self.status
        try:
            url = _browser_ws_url(self.port, timeout_s=timeout_s)
            # suppress_origin: without an Origin header Chrome accepts us
            # under its DEFAULT policy, so the browser no longer needs
            # --remote-allow-origins=* and a web page stays locked out.
            self._ws = websocket.create_connection(url, timeout=timeout_s, suppress_origin=True)
            self._ws.settimeout(_RECV_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - a failed attach is reported, not raised
            self.status.error = f"cdp_connect_failed: {e}"
            log.warning("identity injection could not connect for %s: %s", self.profile_id, e)
            identity_faults.record(
                "inject_connect_failed",
                f"the identity injector could not attach over CDP ({e}); no hooks, no "
                "proxy auth and no cookies reached this browser",
                where="browser.cdp_inject.CdpInjector.start",
                profile_id=self.profile_id,
            )
            return self.status

        # waitForDebuggerOnStart is what makes this airtight: a new page is
        # frozen until we have installed the script on it.
        self._send("Target.setAutoAttach", {
            "autoAttach": True,
            "waitForDebuggerOnStart": True,
            "flatten": True,
        })

        if self.hint_headers and _first_request_hints_enabled():
            # Fetch on the BROWSER session, not per target, and this is the
            # whole point of it. Network.setUserAgentOverride is scoped to a
            # renderer, but a new tab's first document request is formed by
            # the browser process before that renderer exists -- so no
            # per-target interception can ever see it, however early it is
            # armed. Measured on Chromium 151, a tab opened by the operator:
            #
            #   intended : sec-ch-ua "Google Chrome";v="151", "Chromium";v="151", …
            #              sec-ch-ua-platform "Windows"
            #   actual   : sec-ch-ua "Chromium";v="151", "Not=A?Brand";v="99"
            #              sec-ch-ua-platform "Linux"
            #
            # The User-Agent was already right (it comes from the process
            # flag); the brand list was the host's own, GREASE spelling and
            # all, beside a Windows UA. That is the F1 contradiction surviving
            # on one request per tab, and it is the residual F1 left open.
            #
            # Document requests only: one pause per navigation, not one per
            # subresource, so the cost stays off the page's critical path.
            # Auth keeps its own unrestricted per-target Fetch in
            # _on_attached -- a 407 can arrive on any request, and that path
            # is proven, so it is left exactly as it was.
            self._send("Fetch.enable", {
                "patterns": [{
                    "urlPattern": "*",
                    "requestStage": "Request",
                    "resourceType": "Document",
                }],
            })
        self._thread = threading.Thread(
            target=self._loop, name=f"mp-inject-{self.profile_id or self.port}", daemon=True
        )
        self._thread.start()

        # setAutoAttach only catches targets created after it. The start-URL
        # page already exists by the time CDP is ready, so attach to what is
        # open now as well -- otherwise the very first tab, the one the
        # operator is looking at, is the only one left unmasked.
        for target_id in self._existing_page_targets():
            self._send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        # Give the first target a moment so callers get a truthful status.
        self._ready.wait(timeout=5.0)
        self.status.ok = self.status.script_installed or not self.script
        return self.status

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:  # noqa: BLE001 - best effort
            pass
        self._ws = None

    def __enter__(self) -> "IdentityInjector":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- event loop ------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._ws.recv()
            except Exception as e:  # noqa: BLE001
                if self._stop.is_set():
                    return
                text = str(e)
                if "timed out" in text.lower() or isinstance(e, TimeoutError):
                    continue  # idle tick, not a failure
                log.debug("injection socket closed for %s: %s", self.profile_id, e)
                return
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            try:
                self._handle(msg)
            except Exception as e:  # noqa: BLE001 - one bad event must not kill the loop
                log.debug("injection event handling failed for %s: %s", self.profile_id, e)

    def _handle(self, msg: dict[str, Any]) -> None:
        # A command that failed is the whole story when the page turns out
        # unmasked. Record it instead of dropping it on the floor.
        if "id" in msg and "error" in msg:
            err = msg.get("error") or {}
            label = self._pending.pop(msg["id"], f"id={msg['id']}")
            detail = f"{label}: {err.get('message')}"
            if detail not in self.status.detail:
                self.status.detail.append(detail)
            if label.startswith("Page.addScriptToEvaluateOnNewDocument"):
                self.status.script_installed = False
                self.status.ok = False
            if label == UA_OVERRIDE_METHOD:
                # An unmasked wire identity is not a detail to swallow.
                self.status.ua_override_applied = False
                self.status.ok = False
            log.warning("CDP injection command failed for %s — %s", self.profile_id, detail)
            return
        if "id" in msg:
            self._pending.pop(msg["id"], None)
            return

        method = msg.get("method")
        if method == "Target.attachedToTarget":
            self._on_attached(msg.get("params") or {})
        elif method == "Fetch.authRequired":
            self._on_auth_required(msg.get("params") or {}, msg.get("sessionId"))
        elif method == "Fetch.requestPaused":
            self._on_request_paused(msg.get("params") or {}, msg.get("sessionId"))

    def _on_request_paused(self, params: dict[str, Any], session_id: str | None) -> None:
        """Release a paused request, correcting its client hints on the way.

        Every paused request MUST be released or the page hangs forever, so
        this is written so that the release happens whatever else goes wrong:
        the rewrite is computed first, inside a guard, and a failure there
        falls back to continuing the request untouched. A hung tab would be a
        far worse bug than the leak this fixes.

        Only the headers the browser process got wrong are replaced. Chromium
        applies ``Network.setUserAgentOverride`` per renderer target, but a new
        tab's first document request is formed by the browser process before
        that lands, so it carries the host's own ``sec-ch-ua`` and
        ``sec-ch-ua-platform`` next to the spoofed User-Agent from the process
        flag -- the contradiction F1 was about, surviving on one request.
        """
        request_id = params.get("requestId")
        if not request_id:
            return

        override: dict[str, Any] = {"requestId": request_id}
        try:
            request = params.get("request") or {}
            # Never touch the launch-time identity probe. It measures what the
            # browser would really send, so correcting its headers here would
            # make the assertion tautological -- "verified" from headers this
            # handler had just written, even with the UA override dead. It is
            # also, during a launch, the ONLY document request there is, so
            # skipping it takes ~0.5s back off every launch.
            if IDENTITY_PROBE_PATH in str(request.get("url") or ""):
                self._send("Fetch.continueRequest", override, session_id=session_id)
                return

            if self.hint_headers:
                headers = dict(request.get("headers") or {})
                # Case-insensitively drop what we are about to set, so a
                # header the browser spelled differently cannot survive
                # alongside ours and be sent twice.
                lowered = {k.lower() for k in self.hint_headers}
                merged = {k: v for k, v in headers.items() if k.lower() not in lowered}
                merged.update(self.hint_headers)
                override["headers"] = [
                    {"name": k, "value": str(v)} for k, v in merged.items()
                ]
        except Exception as e:  # noqa: BLE001 - never hang a request over this
            identity_faults.record(
                "hint_rewrite_failed",
                f"could not correct client hints on a paused request ({e}); it was "
                "released unmodified and may carry the host's own hints",
                where="browser.cdp_inject.CdpInjector._on_request_paused",
                profile_id=self.profile_id,
            )
            override = {"requestId": request_id}

        self._send("Fetch.continueRequest", override, session_id=session_id)

    def _on_attached(self, params: dict[str, Any]) -> None:
        session_id = params.get("sessionId")
        target = params.get("targetInfo") or {}
        if not session_id or target.get("type") not in ("page", "iframe"):
            # Still release it, or a frozen worker blocks the browser.
            if session_id and params.get("waitingForDebugger"):
                self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)
            return

        # setAutoAttach and our explicit attachToTarget can both fire for the
        # same page. Installing twice would hook canvas and friends twice --
        # the exact bug loader.js used to cause.
        target_id = target.get("targetId") or ""
        with self._lock:
            if target_id and target_id in self._attached:
                return
            if target_id:
                self._attached.add(target_id)

        self.status.targets_seen += 1

        log.info("injection attached to %s target url=%s waiting=%s",
                 self.profile_id, str(target.get("url"))[:80], params.get("waitingForDebugger"))

        if self.ua_override is not None:
            # UA and Client Hints belong here, on the session that outlives
            # the launch, for the same reason the script does. Chromium drops
            # Network.setUserAgentOverride when the session that set it
            # disconnects, so a launch-time one-shot left the wire carrying
            # the host identity (F1). Setting it per attached target also
            # covers tabs the operator opens later, which a single pinned
            # page session cannot.
            self._send(UA_OVERRIDE_METHOD, self.ua_override, session_id=session_id)
            self.status.ua_override_applied = True

        if self.timezone_id:
            # The engine's own clock, not a patched one. evasion.js hooks
            # Intl.DateTimeFormat, resolvedOptions, getTimezoneOffset,
            # toString and toTimeString -- but not toLocaleString, and that
            # gap was measurable: a profile set to America/New_York reported
            # Eastern time from Date.toString and Asia/Kolkata time from
            # Date.toLocaleString, in the same document. Patching one method
            # at a time cannot win that game; setting the renderer's timezone
            # makes every surface agree, including the ones nobody has
            # thought of yet.
            self._send("Emulation.setTimezoneOverride",
                       {"timezoneId": self.timezone_id}, session_id=session_id)
            self.status.timezone_applied = True

        if self.script:
            # Page must be enabled on this session before the page machinery
            # will honour a new-document script.
            self._send("Page.enable", session_id=session_id)
            self._send("Runtime.enable", session_id=session_id)
            self._send("Page.addScriptToEvaluateOnNewDocument",
                       {"source": self.script, "runImmediately": True},
                       session_id=session_id)
            self.status.script_installed = True

        if self.proxy_auth is not None:
            # Unrestricted: a 407 can arrive on any request, not just the
            # document, so auth cannot be narrowed to a resource type.
            self._send("Fetch.enable", {"handleAuthRequests": True}, session_id=session_id)
            self.status.auth_armed = True

        if self.cookies and not self._cookies_done:
            self._send("Network.enable", session_id=session_id)
            self._send("Network.setCookies", {"cookies": self.cookies}, session_id=session_id)
            self.status.cookies_applied = len(self.cookies)
            self._cookies_done = True

        if params.get("waitingForDebugger"):
            # Auto-attached before its first script ran: unfreeze it and the
            # very first document already carries the identity.
            self._send("Runtime.runIfWaitingForDebugger", session_id=session_id)
        else:
            # A page that was already open when we attached (the start URL).
            # addScriptToEvaluateOnNewDocument only affects the NEXT document,
            # so reload once to put this one under the same identity rather
            # than leave the operator's first tab unmasked.
            if self.script:
                self._send("Page.reload", {}, session_id=session_id)
                self.status.detail.append("reloaded a pre-existing page to apply identity")
        self._ready.set()

    def _on_auth_required(self, params: dict[str, Any], session_id: str | None) -> None:
        challenge = params.get("authChallenge") or {}
        request_id = params.get("requestId")
        if challenge.get("source") != "Proxy" or self.proxy_auth is None:
            # Not ours: decline rather than offer proxy credentials to a site.
            self._send("Fetch.continueWithAuth",
                       {"requestId": request_id, "authChallengeResponse": {"response": "Default"}},
                       session_id=session_id)
            return
        self._send("Fetch.continueWithAuth", {
            "requestId": request_id,
            "authChallengeResponse": {
                "response": "ProvideCredentials",
                "username": self.proxy_auth.username,
                "password": self.proxy_auth.password,
            },
        }, session_id=session_id)
        log.info("proxy auth challenge answered for %s", self.profile_id)


_active: dict[str, IdentityInjector] = {}
_active_lock = threading.Lock()


def start_injection(
    profile_id: str,
    port: int,
    *,
    script: str | None = None,
    proxy_auth: ProxyAuth | None = None,
    cookies: list[dict[str, Any]] | None = None,
    ua_override: dict[str, Any] | None = None,
    timezone_id: str | None = None,
) -> InjectionStatus:
    """Start (and register) injection for a profile, replacing any previous one."""
    stop_injection(profile_id)
    injector = IdentityInjector(
        port,
        script=script,
        proxy_auth=proxy_auth,
        cookies=cookies,
        ua_override=ua_override,
        timezone_id=timezone_id,
        profile_id=profile_id,
    )
    status = injector.start()
    with _active_lock:
        _active[profile_id] = injector
    return status


def stop_injection(profile_id: str) -> bool:
    """Tear down a profile's injection session. Safe to call twice."""
    with _active_lock:
        injector = _active.pop(profile_id, None)
    if injector is None:
        return False
    injector.close()
    return True


def stop_all() -> None:
    with _active_lock:
        ids = list(_active)
    for pid in ids:
        stop_injection(pid)


def active_profiles() -> list[str]:
    with _active_lock:
        return list(_active)
