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

from flask import jsonify, request

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


def register_auth(app) -> None:
    """Install the before_request auth gate on the Flask app."""
    token = get_configured_token()
    if token:
        log.info("API token configured; remote (non-localhost) /api/* requests require it")
    else:
        log.warning(
            "No API token configured: remote (non-localhost) /api/* requests will be rejected"
        )

    @app.before_request
    def _auth_gate():
        path = request.path or ""
        if not path.startswith("/api/"):
            return None
        if is_localhost(request.remote_addr):
            return None
        configured = get_configured_token()
        if not configured:
            return _unauthorized()
        auth = (request.headers.get("Authorization") or "").strip()
        if not auth.startswith("Bearer "):
            return _unauthorized()
        presented = auth[len("Bearer ") :].strip()
        if not presented or not hmac.compare_digest(presented, configured):
            return _unauthorized()
        return None
