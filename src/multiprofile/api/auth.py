"""Bearer-token enforcement for non-localhost API access.

HONEST AUTH ASSESSMENT (v1.6.0 -> v1.7.0)
=========================================

What existed before (v1.6.0, server.py ``_check_api_token``):
- If ``MULTIPROFILE_API_TOKEN`` was set, *every* request under ``/api/``
  (except ``GET /api/health``) needed ``Authorization: Bearer <token>`` —
  including requests from localhost. If the env var was unset, there was no
  auth at all: any process on the network that could reach the port had full
  control (launch browsers, delete profiles, read cookies).
- Weaknesses: token read from env only; comparison used ``==`` (not
  constant-time); the failure body leaked no secret but the policy applied
  no localhost distinction, so either *everyone* needed the token or
  *nobody* did.

What changed in v1.7.0:
- Requests from localhost (``127.0.0.1`` / ``::1``) keep working with no
  token. This is a deliberate tradeoff: the server binds to loopback by
  default and the local UI must work out of the box; anyone who can run
  code on this machine already owns it. It is documented here, not hidden.
- Requests from any other address REQUIRE a bearer token. The token comes
  from ``MULTIPROFILE_API_TOKEN`` or the config file
  ``$XDG_CONFIG_HOME/multiprofile/api_token`` (first non-empty line);
  it is never hardcoded and never logged. Comparison uses
  ``hmac.compare_digest`` (constant time). A single generic 401
  ``{"error": {"code": "unauthorized"}}`` is returned whether the token
  is missing, malformed, or wrong — the response does not distinguish.
- Fail-closed: if a remote request arrives while no token is configured,
  it is rejected (401). The server log gets one line at startup so the
  operator knows remote access is effectively disabled until a token is set.

What remains weak (no security theater):
- This is a single static bearer token, not per-user credentials: it is a
  shared secret, and rotation is manual (restart with a new value).
- No TLS: the token travels in cleartext on the local network; use an SSH
  tunnel or a reverse proxy with TLS for anything beyond a trusted LAN.
- No rate limiting or brute-force lockout on the token endpoint.
- ``X-Forwarded-For`` / ``X-Real-IP`` are deliberately NOT trusted for the
  localhost check — behind a proxy, everything is remote. Deployments that
  terminate TLS in a reverse proxy must do their own allow-listing.
- The static file UI (``/``) and non-``/api/`` paths are not gated beyond
  this module; they serve no secrets but do fingerprint the install.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from flask import g, jsonify, request

from .. import api_tokens

log = logging.getLogger("multiprofile.api.auth")

_LOOPBACKS = frozenset({"127.0.0.1", "::1"})


def _token_file_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "multiprofile" / "api_token"


def get_configured_token() -> str:
    """Resolve the bearer token: env first, then the config file.

    Never logs or returns the token anywhere except to the caller.
    """
    env = (os.environ.get("MULTIPROFILE_API_TOKEN") or "").strip()
    if env:
        return env
    try:
        path = _token_file_path()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    except OSError as e:
        log.warning("could not read api token file: %s", e)
    return ""


def is_localhost(remote_addr: str | None) -> bool:
    """True only for genuine loopback addresses. Proxies are not trusted."""
    if not remote_addr:
        return False
    addr = remote_addr.strip()
    if addr in _LOOPBACKS:
        return True
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def _unauthorized():
    # Identical body whether the token is missing or wrong: no oracle.
    return jsonify({"error": {"code": "unauthorized"}}), 401


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_JSON_TYPES = ("application/json", "application/*+json")


def _forbidden(code: str, message: str):
    return jsonify({"ok": False, "error": code, "message": message}), 403


def _host_is_loopback(host_header: str | None) -> bool:
    """True when the Host header names this machine.

    DNS rebinding is the attack this closes: the browser really is on
    127.0.0.1, so ``remote_addr`` looks perfectly local, but the page was
    served from ``attacker.example`` which now resolves to loopback. The
    only thing that still tells them apart is the Host the request carries.
    """
    host = (host_header or "").strip()
    if not host:
        return False
    if host.startswith("["):  # [::1]:8765
        host = host[1 : host.index("]")] if "]" in host else host[1:]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.lower()
    if host in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin_is_same(origin: str | None, host_header: str | None) -> bool:
    """Compare an Origin to the Host we were reached on."""
    if not origin:
        return True  # header absent: not a browser page (curl, an SDK, a test)
    if origin == "null":
        return False  # sandboxed iframe / file:// -- never ours
    try:
        netloc = urlparse(origin).netloc.lower()
    except ValueError:
        return False
    return bool(netloc) and netloc == (host_header or "").lower()


def register_request_guard(app) -> None:
    """Close the browser-driven attack surface on a localhost API.

    ``register_auth`` deliberately lets anything on this machine through:
    the local UI must work with no token, and code already running here owns
    the machine anyway. That reasoning does not extend to a *web page* the
    operator happens to open, which is also "on this machine" as far as the
    socket is concerned.

    Three checks, each closing a specific hole that was verified open:

    1. Host must be loopback. Without it, DNS rebinding turns any site into
       a same-origin client of this API and can read cookies and proxy
       credentials, not merely write.
    2. Origin, when present, must match. A cross-origin page cannot forge it.
    3. A body must be declared JSON. Every write route parses with
       ``get_json(force=True)``, so a ``text/plain`` POST -- a CORS *simple
       request*, no preflight, sent by any page without asking -- was landing
       real side effects. Bodyless writes such as /launch are covered by 1
       and 2.

    Absent Origin and Sec-Fetch-Site are allowed on purpose: non-browser
    clients (curl, the SDKs, the test suite) never send them, and a browser
    always does on a cross-origin request.
    """

    @app.before_request
    def _request_guard():
        path = request.path or ""
        if not path.startswith("/api/"):
            return None

        host_header = request.headers.get("Host")
        if is_localhost(request.remote_addr) and not _host_is_loopback(host_header):
            log.warning("rejected non-loopback Host %r from a local connection", host_header)
            return _forbidden(
                "bad_host",
                "Request Host is not this machine. Reach the API on 127.0.0.1.",
            )

        if request.method in _SAFE_METHODS:
            return None

        origin = request.headers.get("Origin")
        if not _origin_is_same(origin, host_header):
            log.warning("rejected cross-origin %s %s from %r", request.method, path, origin)
            return _forbidden(
                "cross_origin",
                "Cross-origin requests are not accepted by this API.",
            )

        site = (request.headers.get("Sec-Fetch-Site") or "").lower()
        if site in {"cross-site", "same-site"}:
            log.warning("rejected %s %s with Sec-Fetch-Site=%s", request.method, path, site)
            return _forbidden("cross_origin", "Cross-site requests are not accepted.")

        if request.content_length:
            ctype = (request.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not any(ctype == t or ctype.endswith("+json") for t in _JSON_TYPES):
                return _forbidden(
                    "bad_content_type",
                    "Request bodies must be sent as application/json.",
                )
        return None


def _forbidden_scope(needed: str, held: list[str]):
    """403, not 401: the credential is good, the permission is not.

    The distinction matters to whoever is debugging an integration. A 401
    sends them to check their token; a 401 for a scope problem sends them to
    replace a token that was never wrong.
    """
    return (
        jsonify({
            "error": {
                "code": "insufficient_scope",
                "message": f"this token may not perform this request; it needs {needed!r}",
                "required": needed,
                "granted": held,
            }
        }),
        403,
    )


def register_auth(app) -> None:
    """Install the before_request auth gate on the Flask app."""
    token = get_configured_token()
    if token:
        log.info("API token configured; remote (non-localhost) /api/* requests require it")
    else:
        log.info(
            "No legacy API token file; remote /api/* requests need a token from "
            "POST /api/tokens"
        )

    @app.before_request
    def _auth_gate():
        path = request.path or ""
        if not path.startswith("/api/"):
            return None
        if is_localhost(request.remote_addr):
            return None

        auth = (request.headers.get("Authorization") or "").strip()
        if not auth.startswith("Bearer "):
            return _unauthorized()
        presented = auth[len("Bearer ") :].strip()
        if not presented:
            return _unauthorized()

        # The single file/env token predates the store and may already be
        # wired into the operator's scripts, so it keeps working and carries
        # every scope. It is checked first and compared in constant time.
        configured = get_configured_token()
        if configured and hmac.compare_digest(presented, configured):
            g.api_token = {
                "id": api_tokens.LEGACY_TOKEN_ID,
                "name": "legacy file token",
                "scopes": list(api_tokens.SCOPES),
            }
            return None

        record = api_tokens.verify(presented)
        if record is None:
            # Identical answer for malformed, unknown, revoked and wrong: the
            # endpoint must not tell an attacker which part they got right.
            return _unauthorized()

        needed = api_tokens.required_scope(request.method, path)
        if not api_tokens.allows(record, needed):
            log.warning(
                "token %s lacks scope %s for %s %s", record["id"], needed,
                request.method, path,
            )
            return _forbidden_scope(needed, record["scopes"])

        g.api_token = record
        return None
