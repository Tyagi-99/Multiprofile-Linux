"""A small client for the Multiprofile Linux local API.

Four operations plus the one that matters for automation:

    from multiprofile_client import Client

    mp = Client()                        # http://127.0.0.1:8765, no token
    profile = mp.create("research-1")
    session = mp.launch(profile["id"])
    print(session.cdp_url)               # hand this to Playwright/Puppeteer
    mp.stop(profile["id"])

**Why `cdp_url` is the point.** `launch()` returns a DevTools endpoint, and
that is how an automation framework attaches to a profile that the product
launched -- with its identity, its route and its font view already installed.
Launching Chromium yourself through Playwright gets you a browser with none
of that, which is the mistake this SDK exists to make hard:

    # Playwright
    browser = playwright.chromium.connect_over_cdp(session.cdp_url)

    # Puppeteer
    browser = await puppeteer.connect({browserURL: session.cdpUrl})

**Auth.** Loopback needs no token. A non-loopback caller needs one on every
route except `GET /api/health`; pass it as ``token=`` and it is sent as a
bearer. There is no TLS, so a token on a LAN travels in cleartext -- tunnel
it. See ``docs/API.md``.

No dependencies: the standard library only, so this can be vendored into a
scraper without adding a package.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

__all__ = ["Client", "Session", "MultiprofileError", "ApiError",
           "LaunchBlocked", "ScopeError"]

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


class MultiprofileError(Exception):
    """Base for everything this client raises."""


class ApiError(MultiprofileError):
    """The API answered, and the answer was an error."""

    def __init__(self, status: int, body: Any, method: str, path: str) -> None:
        self.status = status
        self.body = body
        detail = body
        if isinstance(body, dict):
            error = body.get("error")
            detail = error if error is not None else body
        super().__init__(f"{method} {path} -> {status}: {detail}")


class ScopeError(ApiError):
    """A valid token without the scope the route needs (403)."""


class LaunchBlocked(MultiprofileError):
    """The launch was refused, with the product's reason.

    This is a distinct type because it is the *expected* outcome of a
    fail-closed route decision, not a bug: a dead proxy on a profile set to
    ``BLOCK``, a pool with no members, or an authenticated SOCKS5 proxy that
    Chromium cannot use. A caller that wants to treat "refused" differently
    from "the server is down" needs them to be different exceptions.
    """

    def __init__(self, reason: str, body: Any = None) -> None:
        self.reason = reason
        self.body = body
        super().__init__(reason)


@dataclass
class Session:
    """A launched profile, and how to attach to it."""

    profile_id: str
    pid: int | None
    cdp_host: str
    cdp_port: int | None
    raw: dict[str, Any]

    @property
    def cdp_url(self) -> str | None:
        """The DevTools HTTP endpoint, for ``connect_over_cdp``."""
        if not self.cdp_port:
            return None
        return f"http://{self.cdp_host}:{self.cdp_port}"

    @property
    def notes(self) -> list[str]:
        """What the launcher recorded -- including identity faults.

        Worth reading rather than ignoring: an experimental identity claim,
        an engine outside the supported range and a font claim that cannot be
        backed all arrive here.
        """
        return list(self.raw.get("notes") or [])


class Client:
    """A thin, synchronous client. One connection per call, no state."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *,
                 token: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ---- plumbing ------------------------------------------------------
    def _request(self, method: str, path: str,
                 payload: dict[str, Any] | None = None,
                 params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
            # Writes require this exact content type; see docs/API.md.
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode("utf-8", "replace")
            if e.code == 403:
                raise ScopeError(e.code, body, method, path) from None
            # 409 on a launch is a refusal, not a fault: the product worked
            # and declined. Raised as LaunchBlocked so a caller can tell it
            # from the server being broken.
            if e.code == 409 and path.endswith("/launch"):
                reason = ""
                if isinstance(body, dict):
                    reason = str(body.get("error") or body.get("errors") or "")
                raise LaunchBlocked(reason or "the launch was refused", body) from None
            raise ApiError(e.code, body, method, path) from None
        except urllib.error.URLError as e:
            raise MultiprofileError(
                f"{method} {path}: could not reach {self.base_url} ({e.reason}). "
                "Is `multiprofile --server-only` running?") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", "replace")

    # ---- the five operations -------------------------------------------
    def health(self) -> dict[str, Any]:
        """Everything the product knows about itself. Needs no token."""
        return self._request("GET", "/api/health")

    def doctor(self) -> dict[str, Any]:
        """The same diagnosis as ``multiprofile --doctor``.

        Worth calling once at the start of a run: it reports a blocked route
        or an unsupported engine *before* a launch fails.
        """
        return self._request("GET", "/api/doctor")

    def list(self, *, limit: int | None = None,
             cursor: str | None = None) -> list[dict[str, Any]]:
        """Profiles, newest first. Paginated; pass ``cursor`` to continue."""
        body = self._request("GET", "/api/profiles",
                             params={"limit": limit, "cursor": cursor})
        if isinstance(body, dict):
            return list(body.get("items") or body.get("profiles") or [])
        return list(body or [])

    def get(self, profile_id: str) -> dict[str, Any]:
        """One profile, with its resolved route and identity verdict.

        The detail endpoint carries ``effectiveTimezone`` and ``identity``,
        which the list does not -- resolving a route per row is expensive.
        """
        body = self._request("GET", f"/api/profiles/{urllib.parse.quote(profile_id)}")
        return body.get("profile", body) if isinstance(body, dict) else body

    def create(self, name: str, **fields: Any) -> dict[str, Any]:
        """Create a profile.

        ``fields`` are passed through, so anything the API accepts works:
        ``proxyType``/``proxyHost``/``proxyPort``/``proxyUser``/``proxyPass``,
        ``userAgentId``, ``deviceId``, ``timezone``, ``startUrl``, ``tags``.

        Unknown fields are **rejected** rather than stored, so a typo is a
        400 and not a setting that silently does nothing.
        """
        body = self._request("POST", "/api/profiles", {"name": name, **fields})
        return body.get("profile", body) if isinstance(body, dict) else body

    def launch(self, profile_id: str, *, url: str | None = None,
               direct: bool = False) -> Session:
        """Launch a profile and return how to attach to it.

        Raises :class:`LaunchBlocked` when the product refused -- a dead
        proxy on a fail-closed profile, an empty pool, or a route this engine
        cannot use. That is a different thing from the server being down, and
        the caller usually wants to handle it differently.
        """
        params: dict[str, Any] = {}
        if url:
            params["url"] = url
        if direct:
            params["direct"] = "1"
        body = self._request(
            "POST", f"/api/profiles/{urllib.parse.quote(profile_id)}/launch",
            {}, params=params or None)
        if not isinstance(body, dict) or body.get("ok") is not True:
            reason = ""
            if isinstance(body, dict):
                reason = str(body.get("error") or body.get("errors") or body)
            raise LaunchBlocked(reason or "the launch was refused", body)
        return Session(
            profile_id=profile_id,
            pid=body.get("pid"),
            cdp_host=str(body.get("cdpHost") or "127.0.0.1"),
            cdp_port=body.get("cdpPort") or body.get("debugPort"),
            raw=body,
        )

    def stop(self, profile_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/api/profiles/{urllib.parse.quote(profile_id)}/stop", {})

    def delete(self, profile_id: str, *, permanent: bool = False,
               purge_storage: bool = True) -> dict[str, Any]:
        """Trash a profile, or remove it for good.

        The default is the trash, which is reversible. ``permanent=True``
        returns a report whose ``purgeStorage`` is the **outcome**, not the
        request -- a purge that could not remove the Chrome directory says so
        and names what it left (F51).
        """
        return self._request(
            "DELETE", f"/api/profiles/{urllib.parse.quote(profile_id)}",
            params={"permanent": "1" if permanent else "0",
                    "purgeStorage": "1" if purge_storage else "0"})

    # ---- convenience ---------------------------------------------------
    def wait_for_cdp(self, session: Session, *, timeout: float = 20.0) -> str:
        """Block until the DevTools endpoint answers, then return its URL.

        ``launch()`` returns once the process is up, which is not quite the
        same moment DevTools starts accepting connections. An automation
        framework that connects too early gets a confusing refusal, so this
        exists to be called instead of ``time.sleep``.
        """
        url = session.cdp_url
        if not url:
            raise MultiprofileError(
                f"{session.profile_id}: the launch recorded no CDP port, so "
                "nothing can attach to it")
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(
                    f"{url}/json/version",
                    headers={"Host": f"localhost:{session.cdp_port}"})
                with urllib.request.urlopen(request, timeout=2):
                    return url
            except Exception as e:  # noqa: BLE001 - retried until the deadline
                last = str(e)
            time.sleep(0.2)
        raise MultiprofileError(
            f"{session.profile_id}: DevTools did not answer on {url} "
            f"within {timeout}s ({last})")
