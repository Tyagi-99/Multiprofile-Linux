"""Strict input validation at the API boundary.

Every mutating endpoint must validate its payload here, *before* the data
reaches the launcher, the job queue, or storage. Nothing invalid may pass
this layer.

On violation a :class:`ValidationError` is raised; the Flask error handler
registered by :func:`register_error_handlers` turns it into::

    400 {"error": {"code": "invalid_input", "message": "...", "field": "..."}}

Security notes:
- Secret-bearing fields (proxy password, passphrase, tokens) are never
  echoed back in error messages or logs — messages stay generic.
- Proxy URLs are restricted to the scheme whitelist
  ``http`` / ``https`` / ``socks5``; ports are clamped to 1..65535.
- ``bool`` is *not* accepted where an int is expected (Python's
  ``isinstance(True, int)`` trap is guarded explicitly).
"""
from __future__ import annotations

import logging
from typing import Any

from flask import jsonify, request

log = logging.getLogger("multiprofile.api.validation")

# Fields whose raw values must never appear in messages or logs.
SENSITIVE_FIELDS = frozenset(
    {"password", "proxyPass", "proxyPassword", "passphrase", "token", "apiToken"}
)

PROXY_SCHEMES = ("http", "https", "socks5")
JOB_TYPES = ("launch_profile", "stop_profile", "bulk_launch", "bulk_stop")
PROXY_TYPES = ("none", "custom", "github", "http", "https", "socks5", "socks5h")
NETWORK_MODES = ("DIRECT", "HTTP_PROXY", "HTTPS_PROXY", "SOCKS5", "REMOTE_GATEWAY")


class ValidationError(Exception):
    """Raised when a request payload fails validation.

    ``field`` is the dotted field path (e.g. ``"networkConfig.proxy.port"``)
    or ``"_body"`` for whole-body problems.
    """

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def _safe_message(field: str, message: str) -> str:
    # Never leak secret values into responses/logs, even by accident.
    if any(s in field.lower() for s in ("password", "passphrase", "token", "secret")):
        return message
    return message


def error_response(exc: ValidationError):
    return (
        jsonify(
            {
                "error": {
                    "code": "invalid_input",
                    "message": _safe_message(exc.field, exc.message),
                    "field": exc.field,
                }
            }
        ),
        400,
    )


def register_error_handlers(app) -> None:
    """Map ValidationError to the 400 envelope on the given Flask app."""

    @app.errorhandler(ValidationError)
    def _handle_validation_error(exc: ValidationError):
        log.info("invalid input: %s: %s", exc.field, exc.message)
        return error_response(exc)


def require_json() -> dict[str, Any]:
    """Return the request body as a dict, or raise ValidationError."""
    payload = request.get_json(force=True, silent=True)
    if payload is None:
        raise ValidationError("_body", "request body must be valid JSON")
    if not isinstance(payload, dict):
        raise ValidationError("_body", "request body must be a JSON object")
    return payload


# ------------------------------------------------------------------ #
#  Scalar validators                                                    #
# ------------------------------------------------------------------ #
def v_str(
    value: Any,
    field: str,
    *,
    min_len: int = 0,
    max_len: int = 2048,
    allow_none: bool = False,
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValidationError(field, "must be a string")
    if len(value) < min_len:
        raise ValidationError(field, f"must be at least {min_len} character(s)")
    if len(value) > max_len:
        raise ValidationError(field, f"must be at most {max_len} characters")
    return value


def v_int(value: Any, field: str, *, min: int | None = None, max: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, "must be an integer")
    if min is not None and value < min:
        raise ValidationError(field, f"must be >= {min}")
    if max is not None and value > max:
        raise ValidationError(field, f"must be <= {max}")
    return value


def v_float(
    value: Any, field: str, *, min: float | None = None, max: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(field, "must be a number")
    f = float(value)
    if min is not None and f < min:
        raise ValidationError(field, f"must be >= {min}")
    if max is not None and f > max:
        raise ValidationError(field, f"must be <= {max}")
    return f


def v_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(field, "must be a boolean")
    return value


def v_enum(value: Any, field: str, choices: tuple[str, ...], *, case_insensitive: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(field, f"must be one of: {', '.join(choices)}")
    candidate = value.lower() if case_insensitive else value
    lookup = {c.lower(): c for c in choices} if case_insensitive else {c: c for c in choices}
    if candidate not in lookup:
        raise ValidationError(field, f"must be one of: {', '.join(choices)}")
    return lookup[candidate]


def v_port(value: Any, field: str) -> int:
    return v_int(value, field, min=1, max=65535)


def v_proxy_scheme(value: Any, field: str) -> str:
    return v_enum(value, field, PROXY_SCHEMES, case_insensitive=True)


def v_str_list(value: Any, field: str, *, max_items: int = 200, item_max_len: int = 128) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(field, "must be an array of strings")
    if len(value) > max_items:
        raise ValidationError(field, f"must contain at most {max_items} items")
    out: list[str] = []
    for i, item in enumerate(value):
        out.append(v_str(item, f"{field}[{i}]", min_len=1, max_len=item_max_len))
    return out


def v_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(field, "must be a JSON object")
    return value


# ------------------------------------------------------------------ #
#  Composite payload validators                                         #
# ------------------------------------------------------------------ #
def _validate_proxy_block(proxy: dict[str, Any], prefix: str) -> None:
    """Validate a structured proxy block (scheme/host/port/user/pass)."""
    v_dict(proxy, prefix)
    if "scheme" in proxy:
        v_proxy_scheme(proxy["scheme"], f"{prefix}.scheme")
    if "host" in proxy:
        v_str(proxy["host"], f"{prefix}.host", min_len=1, max_len=255)
    if "port" in proxy and proxy["port"] is not None:
        v_port(proxy["port"], f"{prefix}.port")
    if "username" in proxy:
        v_str(proxy["username"], f"{prefix}.username", max_len=256)
    if "password" in proxy:
        v_str(proxy["password"], f"{prefix}.password", max_len=1024)


def _known_device_ids() -> set[str] | None:
    """Known device preset ids, or None if the registry is unavailable."""
    try:
        from ..devices.registry import registry
    except Exception:
        return None
    try:
        return {p.id for p in registry.list()}
    except Exception:
        return None


def validate_profile_payload(payload: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate a profile create (partial=False) or update (partial=True) body.

    Returns the payload unchanged; raises ValidationError on any violation.
    Device preset selection is checked against the device registry when it
    is available: unknown ``deviceId`` values are rejected at the door.
    """
    v_dict(payload, "_body")
    if "name" in payload:
        v_str(payload["name"], "name", min_len=1, max_len=120)
    if "userAgent" in payload:
        v_str(payload["userAgent"], "userAgent", min_len=1, max_len=4096)
    if "userAgentId" in payload:
        v_str(payload["userAgentId"], "userAgentId", min_len=1, max_len=128)
    if "startUrl" in payload:
        v_str(payload["startUrl"], "startUrl", min_len=1, max_len=2048)
    if "notes" in payload:
        v_str(payload["notes"], "notes", max_len=4096)
    if "color" in payload:
        v_str(payload["color"], "color", min_len=1, max_len=16)
    if "clientId" in payload and payload["clientId"] is not None:
        v_str(payload["clientId"], "clientId", min_len=1, max_len=128)
    if "poolId" in payload and payload["poolId"] is not None:
        v_str(payload["poolId"], "poolId", min_len=1, max_len=128)
    if "tags" in payload and payload["tags"] is not None:
        v_str_list(payload["tags"], "tags", max_items=50, item_max_len=64)
    if "seed" in payload and payload["seed"] is not None:
        v_int(payload["seed"], "seed", min=0, max=2**63 - 1)
    if "timezone" in payload:
        v_str(payload["timezone"], "timezone", min_len=1, max_len=64)
    if "language" in payload:
        v_str(payload["language"], "language", min_len=1, max_len=32)
    if "webrtcMode" in payload:
        v_enum(payload["webrtcMode"], "webrtcMode", ("default", "disabled", "proxy"), case_insensitive=True)

    device_id = payload.get("deviceId", payload.get("device_id"))
    if device_id is not None and device_id != "":
        v_str(device_id, "deviceId", min_len=1, max_len=128)
        known = _known_device_ids()
        if known is not None and device_id not in known:
            raise ValidationError("deviceId", "unknown device preset id")

    # Legacy flat proxy fields.
    if "proxyHost" in payload and payload["proxyHost"]:
        v_str(payload["proxyHost"], "proxyHost", min_len=1, max_len=255)
    if "proxyPort" in payload and payload["proxyPort"] not in (None, ""):
        v_port(payload["proxyPort"], "proxyPort")
    if "proxyUser" in payload:
        v_str(payload["proxyUser"], "proxyUser", max_len=256)
    if "proxyPass" in payload:
        v_str(payload["proxyPass"], "proxyPass", max_len=1024)
    if "proxyType" in payload:
        v_enum(payload["proxyType"], "proxyType", PROXY_TYPES, case_insensitive=True)
    if "customProxy" in payload and payload["customProxy"]:
        v_str(payload["customProxy"], "customProxy", min_len=1, max_len=2048)

    # Structured network config block.
    if "networkConfig" in payload and payload["networkConfig"] is not None:
        net = v_dict(payload["networkConfig"], "networkConfig")
        if "mode" in net:
            v_enum(net["mode"], "networkConfig.mode", NETWORK_MODES)
        if "proxy" in net and net["proxy"] is not None:
            _validate_proxy_block(net["proxy"], "networkConfig.proxy")
    return payload


def validate_job_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a job enqueue body (POST /api/jobs)."""
    v_dict(payload, "_body")
    v_enum(payload.get("type"), "type", JOB_TYPES)
    ids = payload.get("profileIds")
    if isinstance(ids, str):
        ids = [ids]
    v_str_list(ids, "profileIds", max_items=200, item_max_len=128)
    if not ids:
        raise ValidationError("profileIds", "at least one profile id is required")
    if "url" in payload and payload["url"] is not None:
        v_str(payload["url"], "url", min_len=1, max_len=2048)
    for flag in ("direct", "cdp", "tileRequired"):
        if flag in payload:
            v_bool(payload[flag], flag)
    if "options" in payload and payload["options"] is not None:
        options = v_dict(payload["options"], "options")
        if "tileRequired" in options:
            v_bool(options["tileRequired"], "options.tileRequired")
    return payload


def validate_proxy_test_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a proxy test body (POST /api/proxy/test) — the network config door."""
    v_dict(payload, "_body")
    if "customProxy" in payload and payload["customProxy"]:
        v_str(payload["customProxy"], "customProxy", min_len=1, max_len=2048)
    for key in ("host", "proxyHost"):
        if payload.get(key):
            v_str(payload[key], key, min_len=1, max_len=255)
    for key in ("port", "proxyPort"):
        if payload.get(key) not in (None, ""):
            v_port(payload[key], key)
    for key in ("protocol", "type"):
        if payload.get(key):
            v_proxy_scheme(payload[key], key)
    for key in ("username", "user", "proxyUser"):
        if key in payload and payload[key] is not None:
            v_str(payload[key], key, max_len=256)
    for key in ("password", "pass", "proxyPass"):
        if key in payload and payload[key] is not None:
            v_str(payload[key], key, max_len=1024)
    if "timeout" in payload and payload["timeout"] is not None:
        v_float(payload["timeout"], "timeout", min=1, max=60)
    return payload


def validate_clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
    v_dict(payload, "_body")
    if "mode" in payload:
        v_enum(payload["mode"], "mode", ("config_only", "config_and_cookies", "full_session"))
    if "name" in payload and payload["name"] is not None:
        v_str(payload["name"], "name", min_len=1, max_len=120)
    return payload
